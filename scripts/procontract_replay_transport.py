"""One request to a model provider, sent as the bytes it was kept as.

A decision replay sends again a request that an earlier run sent. It is a replay only if nothing
between the kept bytes and the wire changes them. This module is that one step and nothing else:

  send   checks that the bytes are the ones named by their SHA-256, posts them once to one
         endpoint, and keeps what came back: the body as it arrived, the status, what the reply
         says of itself (its id, its model, its usage, how it ended), and how the exchange ended
         on this side.

It does not build, project or re-serialise a request, add anything to its body, follow a redirect,
send a second request, or act on a tool call in the reply. It does not read the reply's choice:
what the model decided is for whoever measures it.

Two endings are kept apart. Local: the socket is closed, the watchdog has stopped, the files are
written; this module can see these and reports them as it finds them. Remote: the provider said how
the request ended, by a complete HTTP answer or by a terminal event in the stream. When it did not,
because the stream broke or the deadline passed, the remote state is unknown, however cleanly this
side closed. An unknown remote state is never reported as an ending.

Identities and credentials stay outside the body. `execution_id` and `allocation_id` are recorded
here and are not sent unless the caller puts them in `headers`. The value of a header is recorded
only for the few names listed below; for every other header only its name is kept, so a credential
is neither written to disk nor returned.

Standard library only. Proxies from the environment are not used.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

SCHEMA = "replay-transport/1"

# Headers whose values are kept. Every other header is kept by name only.
REQUEST_VALUES = ("accept", "accept-encoding", "content-length", "content-type", "host", "openai-beta", "user-agent",
                  "x-allocation-id", "x-execution-id")
RESPONSE_VALUES = ("content-encoding", "content-length", "content-type", "date", "openai-processing-ms", "openai-version",
                   "request-id", "retry-after", "transfer-encoding", "x-request-id")
REQUEST_IDS = ("x-request-id", "request-id")

# How a stream, or a reply that is one JSON object, says the request ended.
TERMINAL_EVENTS = ("response.completed", "response.failed", "response.incomplete", "response.cancelled", "error")
TERMINAL_STATUSES = ("completed", "failed", "incomplete", "cancelled")

FILES = ("started.json", "response.raw", "result.json")
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _write(path: Path, value: dict) -> None:
    """Write a record whole: to a temporary file, synced, then renamed."""
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=1, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _kept(headers, values: tuple) -> dict:
    """Headers as they are recorded: the value for a listed name, the name alone otherwise."""
    return {name.lower(): (value if name.lower() in values else None) for name, value in headers}


def _target(endpoint: str) -> dict:
    """The endpoint's parts, checked. Plain HTTP is accepted only to this host: a credential is
    not sent in the clear to another one."""
    if not isinstance(endpoint, str):
        raise ValueError("an endpoint is a URL")
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.fragment or parts.username or parts.password:
        raise ValueError("an endpoint is an http or https URL with a host, without credentials or a fragment")
    if parts.scheme == "http" and parts.hostname not in LOOPBACK:
        raise ValueError("plain http is accepted only to this host")
    return {"scheme": parts.scheme, "host": parts.hostname, "port": parts.port or (443 if parts.scheme == "https" else 80),
            "path": parts.path or "/", "query": parts.query}


def _checked(payload, expected_sha256, endpoint, headers, execution_id, allocation_id, deadline, connect_timeout, idle_timeout):
    """Everything that can be refused before a byte is sent or a file is written."""
    if not isinstance(payload, bytes):
        raise ValueError("a payload is the bytes that were kept, not text and not an object")
    if not (isinstance(expected_sha256, str) and len(expected_sha256) == 64):
        raise ValueError("a payload is named by its SHA-256 in hexadecimal")
    if hashlib.sha256(payload).hexdigest() != expected_sha256.lower():
        raise ValueError("the payload is not the bytes its SHA-256 names")
    target = _target(endpoint)
    if not isinstance(headers, dict) or not all(
            isinstance(name, str) and name and isinstance(value, str) and "\n" not in name + value and "\r" not in name + value
            for name, value in headers.items()):
        raise ValueError("headers are names and values as text, on one line each")
    try:
        for name, value in headers.items():
            name.encode("ascii")
            value.encode("latin-1")
    except UnicodeError:
        raise ValueError("a header's name is ASCII and its value Latin-1") from None
    names = [name.lower() for name in headers]
    if len(set(names)) != len(names):
        raise ValueError("a header is given once")
    if "content-type" not in names:
        raise ValueError("the caller states the content type; nothing is assumed")
    if "transfer-encoding" in names:
        raise ValueError("the payload is sent whole, with its length")
    given = next((value for name, value in headers.items() if name.lower() == "content-length"), None)
    if given is not None and given != str(len(payload)):
        raise ValueError("the stated content length is not the payload's")
    for name, value in (("execution_id", execution_id), ("allocation_id", allocation_id)):
        if not (isinstance(value, str) and value):
            raise ValueError(f"{name} names the new execution or allocation: a non-empty string")
    for name, value in (("deadline", deadline), ("connect_timeout", connect_timeout), ("idle_timeout", idle_timeout)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
            raise ValueError(f"{name} is a positive number of seconds, or absent")
    if connect_timeout is None:
        raise ValueError("connecting has a time limit")
    if deadline is not None and time.time() >= deadline:
        raise ValueError("the allocation's deadline has passed: nothing is sent")
    return target


def _events(raw: bytes) -> list:
    """The events of a server-sent stream, in order: (event name, data text)."""
    found = []
    # SSE dispatch requires the blank line. EOF (including a broken HTTP body)
    # discards an unfinished event even when its data happens to be valid JSON.
    for block in raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n").split("\n\n")[:-1]:
        name, data = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:][1:] if line[5:].startswith(" ") else line[5:])
        if name is not None or data:
            found.append((name, "\n".join(data)))
    return found


def _said(raw: bytes, content_type: str | None, encoded: bool) -> dict:
    """What a reply says of itself: whether it reached a terminal state and which, its id, model
    and usage. Read from the bytes; never guessed. A reply that cannot be read says nothing."""
    said = {"terminal": None, "response_id": None, "model": None, "status": None, "usage": None, "error": None,
            "events": None, "done_marker": False, "unread": None}
    if encoded:
        said["unread"] = "the body is content-encoded and is kept as it arrived"
        return said
    kind = (content_type or "").split(";")[0].strip().lower()

    def take(response):
        if isinstance(response, dict):
            for name, key in (("response_id", "id"), ("model", "model"), ("status", "status"), ("usage", "usage")):
                if response.get(key) is not None:
                    said[name] = response[key]
            if response.get("error") is not None:
                said["error"] = response["error"]

    if kind == "text/event-stream":
        events = _events(raw)
        said["events"] = len(events)
        for name, data in events:
            if data == "[DONE]":
                said["done_marker"] = True
                continue
            try:
                body = json.loads(data)
            except ValueError:
                continue
            if not isinstance(body, dict):
                continue
            label = body.get("type") if isinstance(body.get("type"), str) else name
            take(body.get("response"))
            if label in TERMINAL_EVENTS:
                said["terminal"] = label
                if label == "error":
                    said["error"] = body.get("error", body)
    elif kind == "application/json":
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            said["unread"] = "the body is not JSON"
            return said
        if isinstance(body, dict):
            take(body)
            if isinstance(body.get("status"), str) and body["status"] in TERMINAL_STATUSES:
                said["terminal"] = "response." + body["status"]
    else:
        said["unread"] = "a content type this module does not read"
    return said


def send(payload: bytes, expected_sha256: str, endpoint: str, *, headers: dict, execution_id: str, allocation_id: str,
         deadline: float | None, output_dir: Path, connect_timeout: float = 30.0, idle_timeout: float | None = None) -> dict:
    """Post `payload` once to `endpoint` and keep what came back in `output_dir`.

    `payload` is the kept request body; it is sent only if its SHA-256 is `expected_sha256`.
    `headers` are sent as given: the content type and the credential come from the caller. The
    connection adds only what HTTP needs (Host, Content-Length, and Accept-Encoding: identity
    unless the caller states one). `deadline` is the new allocation's end in Unix seconds, or
    None; at the deadline the socket is shut and the call returns. `idle_timeout` bounds a
    silence on the connection. `output_dir` holds one send: a directory that already holds one is
    refused.

    Anything refused raises ValueError before a byte is sent or a file is written. After that the
    call returns a record and never raises for what the network did: the record says what
    happened. It is also written as result.json, beside started.json (written before connecting)
    and response.raw (the reply's body as it arrived, after transfer decoding).

    The record's `outcome` says how the exchange ended on the wire: completed; provider_failed (a
    terminal event or status other than completed); http_error; redirect_not_followed;
    no_terminal_event (the reply ended without saying how the request ended); interrupted (the
    reply broke off, or none came); deadline; not_sent (no connection was made, so nothing left
    this host). `remote` says what the provider said of the request: `terminal_observed` is true
    when a status that is not a success arrived, or a terminal event or status did, even if the
    connection then broke; otherwise the remote state is "unknown" (or "not_reached" when no
    connection was made). `local` holds what this side closed, as observed.
    """
    target = _checked(payload, expected_sha256, endpoint, headers, execution_id, allocation_id, deadline,
                      connect_timeout, idle_timeout)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any((output_dir / name).exists() for name in FILES):
        raise ValueError("this directory already holds a send; a send is not repeated into it")

    sent_headers = dict(headers)
    lower = {name.lower() for name in sent_headers}
    usual = target["port"] == (443 if target["scheme"] == "https" else 80)
    implied = [(name, value) for name, value in (
        ("Host", target["host"] if usual else f"{target['host']}:{target['port']}"),
        ("Accept-Encoding", "identity"), ("Content-Length", str(len(payload)))) if name.lower() not in lower]
    record = {
        "schema": SCHEMA, "execution_id": execution_id, "allocation_id": allocation_id,
        "request": {"sha256": expected_sha256.lower(), "bytes": len(payload),
                    "endpoint": {"scheme": target["scheme"], "host": target["host"], "port": target["port"], "path": target["path"],
                                 "query_names": sorted({name for name, _ in parse_qsl(target["query"], keep_blank_values=True)})},
                    "headers": _kept(list(sent_headers.items()) + implied, REQUEST_VALUES)},
        "deadline": deadline, "started_at": time.time(),
    }
    _write(output_dir / "started.json", record)

    secrets = [value for name, value in headers.items() if name.lower() not in REQUEST_VALUES and len(value) >= 6]
    secrets += [value for _, value in parse_qsl(target["query"], keep_blank_values=True) if len(value) >= 6]

    def plain(error: BaseException) -> dict:
        """An error by its class, number and text; the text is dropped if it could carry a secret."""
        text = str(error)[:200]
        return {"class": type(error).__name__, "errno": getattr(error, "errno", None),
                "text": None if any(secret in text for secret in secrets) else text}

    expired = threading.Event()
    connection = watchdog = reply = None
    stage, failure, status, reason, reply_headers = "connect", None, None, None, []
    received, digest, first_byte_at, complete = 0, hashlib.sha256(), None, False
    raw = bytearray()
    raw_path = output_dir / "response.raw"
    try:
        with open(raw_path, "wb") as kept:
            try:
                limit = connect_timeout if deadline is None else max(0.001, min(connect_timeout, deadline - time.time()))
                if target["scheme"] == "https":
                    connection = http.client.HTTPSConnection(target["host"], target["port"], timeout=limit,
                                                             context=ssl.create_default_context())
                else:
                    connection = http.client.HTTPConnection(target["host"], target["port"], timeout=limit)
                connection.connect()
                stage = "send"
                connection.sock.settimeout(idle_timeout)
                if deadline is not None:
                    held = connection.sock

                    def expire():
                        expired.set()
                        try:
                            held.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass

                    watchdog = threading.Timer(max(0.0, deadline - time.time()), expire)
                    watchdog.daemon = True
                    watchdog.start()
                connection.request("POST", target["path"] + ("?" + target["query"] if target["query"] else ""),
                                   body=payload, headers=sent_headers)
                stage = "reply"
                reply = connection.getresponse()
                status, reason, reply_headers = reply.status, reply.reason, reply.getheaders()
                stage = "body"
                while True:
                    piece = reply.read1(65536)
                    if not piece:
                        break
                    if first_byte_at is None:
                        first_byte_at = time.time()
                    kept.write(piece)
                    raw.extend(piece)
                    digest.update(piece)
                    received += len(piece)
                # A body with a stated length that ended short is not complete.
                complete = not (reply.length is not None and reply.length > 0)
            except (OSError, http.client.HTTPException) as error:
                failure = plain(error)
            finally:
                kept.flush()
                os.fsync(kept.fileno())
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join(timeout=10)
        # With Connection: close, HTTPConnection hands socket ownership to the
        # HTTPResponse. Close that file explicitly before recording local closure;
        # waiting for refcount cleanup would make the retained fact premature.
        if reply is not None:
            reply.close()
        if connection is not None:
            connection.close()

    reply_kept = _kept(reply_headers, RESPONSE_VALUES)
    encoded = reply_kept.get("content-encoding") not in (None, "", "identity")
    said = _said(bytes(raw), reply_kept.get("content-type"), encoded) if status is not None else None

    # How the exchange ended on the wire.
    if expired.is_set():
        outcome = "deadline"
    elif stage == "connect":
        outcome = "not_sent"
    elif status is None:
        outcome = "interrupted"
    elif 300 <= status < 400:
        outcome = "redirect_not_followed"
    elif not 200 <= status < 300:
        outcome = "http_error"
    elif failure is not None or not complete:
        outcome = "interrupted"
    elif said["terminal"] is None:
        outcome = "no_terminal_event"
    else:
        outcome = "completed" if said["terminal"] == "response.completed" else "provider_failed"

    # What the provider said of the request, which is another question: a status that is not a
    # success is its answer, and so is a terminal event that arrived, even if the connection
    # broke or the deadline passed after it. Without either, nothing was said.
    if status is not None and not 200 <= status < 300:
        terminal = f"http_{status}"
    elif status is not None:
        terminal = said["terminal"]
    else:
        terminal = None
    record.update({
        "ended_at": time.time(), "first_byte_at": first_byte_at, "outcome": outcome,
        "stage_reached": stage, "failure": failure,
        "http": None if status is None else {"status": status, "reason": reason, "headers": reply_kept},
        "response": {"file": "response.raw", "bytes": received, "sha256": digest.hexdigest(), "complete": complete,
                     "request_id": next((reply_kept[name] for name in REQUEST_IDS if reply_kept.get(name)), None)},
        "said": said,
        "remote": {"terminal_observed": terminal is not None, "terminal": terminal,
                   # Nothing left this host only when no connection was made; otherwise, without a
                   # terminal, what the provider did with the request is unknown.
                   "state": terminal if terminal is not None else ("not_reached" if outcome == "not_sent" else "unknown")},
        "local": {"socket_closed": (connection is None or connection.sock is None) and
                                   (reply is None or reply.isclosed()),
                  "watchdog_stopped": watchdog is None or not watchdog.is_alive(),
                  "deadline_fired": expired.is_set(), "requests_sent": 0 if stage == "connect" else 1},
    })
    _write(output_dir / "result.json", record)
    return record
