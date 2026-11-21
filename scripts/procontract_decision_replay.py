#!/usr/bin/env python3
"""Host adapter for one retained decision request through Operations.

The registered profile owns payload paths, provider and credentials. A submitted
operation names only a checkpoint. This captures a request-level continuation,
not a restored solver session, and never supplies a quality/adoption certificate.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import procontract_operations as ops

SCHEMA = "decision-replay-profile/v1"
CHECKPOINT = "decision-request-checkpoint/v1"
SOURCE_MODES = {"effective_payload", "reconstructed_payload", "synthetic"}


def implementation():
    return ops.sha({"operations": ops.IMPLEMENTATION, "sources": {
        name: ops.sha(Path(__file__).with_name(name).read_bytes())
        for name in ("procontract_decision_replay.py", "procontract_replay_transport.py")}})


def text(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def checkpoint(path, parent_state_id, analysis_unit, source_mode, source_receipts, omitted):
    """Provisioner-only descriptor. References preserve its source qualification.

    This function verifies bytes and shape, not historical closure or permissions.
    Those facts are supplied and checked by the experiment owner before admission.
    """
    path = Path(path).resolve(strict=True)
    text(parent_state_id, "parent_state_id")
    if (not isinstance(analysis_unit, dict) or set(analysis_unit) != {"id", "family"}
            or not all(isinstance(v, str) and v for v in analysis_unit.values())):
        raise ValueError("analysis_unit needs id and family")
    if source_mode not in SOURCE_MODES:
        raise ValueError("unknown source mode")
    if not isinstance(source_receipts, list) or not all(isinstance(x, str) and x for x in source_receipts):
        raise ValueError("source_receipts must be references")
    if source_mode != "synthetic" and not source_receipts:
        raise ValueError("historical input requires its source qualification references")
    if not isinstance(omitted, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v
                                              for k, v in omitted.items()):
        raise ValueError("omitted components need explicit reasons")
    raw = path.read_bytes()
    if not isinstance(strict_json(raw), dict):
        raise ValueError("request must be a JSON object")
    body = {"schema": CHECKPOINT, "parent_state_id": parent_state_id,
            "analysis_unit": analysis_unit, "source_mode": source_mode,
            "source_receipts": source_receipts, "omitted": omitted,
            "payload_sha256": ops.sha(raw), "payload_bytes": len(raw)}
    return {"id": ops.sha(body), "path": str(path), **body}


def profile(host_root, endpoint, checkpoints, *, auth_env=None, model="gpt-6-luna", effort="max",
            max_request_bytes=16 * 1024 * 1024):
    """Build a protected profile before registering the Operations measurement."""
    return {"schema": SCHEMA, "implementation": implementation(),
            "host_root": str(Path(host_root).resolve()), "endpoint": endpoint,
            "auth_env": auth_env, "model": model, "effort": effort,
            "max_request_bytes": max_request_bytes,
            "checkpoints": {item["id"]: item for item in checkpoints}}


def observation(response):
    """Retain the whole structured output; classify transport outcomes separately."""
    if not isinstance(response, dict):
        return {"category": "no_response", "decision": None}
    output = response.get("output")
    if not isinstance(output, list):
        return {"category": "no_output", "decision": None}
    calls = [item for item in output if isinstance(item, dict) and
             isinstance(item.get("type"), str) and item["type"].endswith("_call")]
    if calls:
        return {"category": "tool_request", "decision": None, "calls": calls}
    content = [part for item in output if isinstance(item, dict) and item.get("type") == "message"
               and isinstance(item.get("content"), list)
               for part in item.get("content", []) if isinstance(part, dict)]
    refusals = [part for part in content if part.get("type") == "refusal"]
    if refusals:
        return {"category": "refusal", "decision": None, "refusals": refusals}
    strings = [part["text"] for part in content
               if part.get("type") == "output_text" and isinstance(part.get("text"), str)]
    joined = "".join(strings)
    try:
        decision = strict_json(joined)
    except (ValueError, TypeError, UnicodeError):
        decision = None
    return {"category": "structured_json" if isinstance(decision, dict) else "unstructured_output",
            "decision": decision if isinstance(decision, dict) else None, "text": joined}


def _assignment(profile_path, assignment_path):
    profile_path, assignment_path = Path(profile_path).resolve(), Path(assignment_path).resolve()
    config = strict_json(profile_path.read_bytes())
    if config.get("schema") != SCHEMA or config.get("implementation") != implementation():
        raise ValueError("unqualified replay implementation")
    endpoint = urlsplit(config["endpoint"])
    if (endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.fragment):
        raise ValueError("invalid provider endpoint")
    if endpoint.scheme == "http" and endpoint.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("plaintext provider must be loopback")
    assignment = strict_json(assignment_path.read_bytes())
    request = assignment["request"]
    identity = text(request["id"], "operation id")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", identity):
        raise ValueError("operation id is not transport-safe")
    host = ops.Operations(config["host_root"])
    item = host.inspect(identity)
    expected = {**item["request"], "version": host.get(request["version"], "version")}
    if ops.encoded(assignment) != ops.encoded(expected) or assignment_path != host.operation_dir(identity) / "assignment.json":
        raise ValueError("assignment is not the retained Operations assignment")
    if item["state"] != "running":
        raise ValueError("operation is not running")
    if assignment["measurement"]["identity"] != implementation():
        raise ValueError("measurement implementation differs")
    sources = assignment["measurement"].get("sources", {})
    if sources.get(str(profile_path)) != ops.sha(profile_path.read_bytes()):
        raise ValueError("profile is not pinned by this assignment")
    if any(not Path(p).is_file() or ops.sha(Path(p).read_bytes()) != digest for p, digest in sources.items()):
        raise ValueError("registered runner sources changed")
    return config, assignment, host


def _payload(config, assignment):
    request = assignment["request"]
    if set(request["inputs"]) != {"checkpoint"}:
        raise ValueError("replay selects one registered checkpoint")
    selected = config["checkpoints"].get(request["inputs"]["checkpoint"])
    if selected is None:
        raise ValueError("checkpoint is not in the protected profile")
    body = {k: v for k, v in selected.items() if k not in ("path", "id")}
    if body.get("schema") != CHECKPOINT or ops.sha(body) != selected["id"]:
        raise ValueError("checkpoint identity differs")
    maximum = config["max_request_bytes"]
    if type(maximum) is not int or maximum < 1:
        raise ValueError("invalid payload byte allowance")
    with Path(selected["path"]).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum or len(raw) != selected["payload_bytes"] or ops.sha(raw) != selected["payload_sha256"]:
        raise ValueError("checkpoint bytes differ")
    payload = strict_json(raw)
    if payload.get("model") != config["model"]:
        raise ValueError("request model differs from the admitted profile")
    if config.get("effort") is not None and (payload.get("reasoning") or {}).get("effort") != config["effort"]:
        raise ValueError("request effort differs from the admitted profile")
    if payload.get("previous_response_id") or payload.get("conversation") or payload.get("background"):
        raise ValueError("request depends on unretained provider state or background execution")
    if any(not isinstance(tool, dict) or tool.get("type") not in ("function", "custom")
           for tool in payload.get("tools", [])):
        raise ValueError("hosted tools are outside the single-request replay scope")
    return selected, raw


def retain_bytes(host, kind, raw):
    return host.put(kind, {"sha256": ops.sha(raw), "bytes": len(raw),
                          "encoding": "base64", "body": base64.b64encode(raw).decode("ascii")},
                    source="replay-host", notify=False)


def retain_trace(host, directory, assignment, received):
    """Keep exact response bytes, including partial/error bodies, in host objects."""
    files = {}
    raw_dir = directory / "transport"
    if raw_dir.exists():
        for path in sorted(raw_dir.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise ValueError("transport evidence must be private regular files")
            files[path.name] = retain_bytes(host, "decision_replay_raw", path.read_bytes())
    return host.put("decision_replay_trace", {
        "schema": "decision-replay-trace/v1", "assignment": ops.sha(assignment),
        "transport": received, "files": files}, source="replay-host", notify=False)


def provider_response(directory, received):
    """Read the final provider object from exact raw bytes, not transport metadata.

    A partial SSE frame is not a dispatched event. Retain it in raw custody, but
    do not give a truncated terminal event a completed-decision interpretation.
    """
    descriptor = received.get("response") or {}
    raw_path = directory / "transport" / "response.raw"
    if not raw_path.exists():
        return None
    raw = raw_path.read_bytes()
    if ops.sha(raw) != descriptor.get("sha256") or len(raw) != descriptor.get("bytes"):
        raise ValueError("raw response differs from transport receipt")
    headers = (received.get("http") or {}).get("headers", {})
    if headers.get("content-encoding") not in (None, "", "identity"):
        return None
    kind = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if kind == "application/json":
        try:
            value = strict_json(raw)
            return value if isinstance(value, dict) and value.get("status") in (
                "completed", "failed", "incomplete", "cancelled") else None
        except (ValueError, UnicodeError):
            return None
    if kind != "text/event-stream":
        return None
    try:
        stream = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError:
        return None
    final = None
    for block in stream.split("\n\n")[:-1]:
        lines = [line[5:].removeprefix(" ") for line in block.split("\n") if line.startswith("data:")]
        try:
            event = strict_json("\n".join(lines))
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") in (
                "response.completed", "response.failed", "response.incomplete", "response.cancelled"):
            if isinstance(event.get("response"), dict):
                final = event["response"]
    return final


def run(profile_path, assignment_path):
    # Only a genuine running assignment can write a result. Pin/assignment errors
    # outside this boundary remain Operations failures, never caller attestations.
    config, assignment, host = _assignment(profile_path, assignment_path)
    directory = Path(assignment_path).resolve().parent
    with (directory / "replay.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = directory / "result.json"
        if saved.exists():
            return strict_json(saved.read_bytes())
        return _run_locked(config, assignment, host, directory, profile_path)


def _run_locked(config, assignment, host, directory, profile_path):
    import procontract_replay_transport as transport
    execution = assignment["request"]["id"]
    retained_assignment = {k: v for k, v in assignment.items() if k != "version"}
    allocation = ops.sha(retained_assignment)
    intent_path = directory / "dispatch-intent.json"
    base = {"schema": "decision-replay-observation/v1", "validity": "observation_only",
            "admissible": False, "execution": execution, "allocation": allocation,
            "usage": None, "terminal": None}
    if intent_path.exists():
        # This includes a partial intent left by a crash. Never turn missing
        # acknowledgement into another send or assert that no send happened.
        result = {**base, "category": "dispatch_uncertain", "effects_resolved": False,
                  "counts": {"reserved_http_attempts": 1},
                  "trace": retain_trace(host, directory, retained_assignment, None)}
        ops.atomic(directory / "result.json", result)
        return result
    try:
        selected, raw = _payload(config, assignment)
        if assignment["deadline"] is not None and time.time() >= assignment["deadline"]:
            raise ValueError("new replay allocation expired before dispatch")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "X-Execution-ID": execution, "X-Allocation-ID": allocation}
        auth_env = config.get("auth_env")
        if auth_env is not None:
            if not isinstance(auth_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", auth_env):
                raise ValueError("invalid credential source")
            credential = os.environ.get(auth_env)
            if not credential or "\r" in credential or "\n" in credential:
                raise ValueError("registered credential is unavailable or invalid")
            headers["Authorization"] = "Bearer " + credential
        payload_object = retain_bytes(host, "decision_request_bytes", raw)
        checkpoint_object = host.put("decision_request_checkpoint", {
            k: v for k, v in selected.items() if k != "path"}, source="replay-host", notify=False)
    except Exception as error:
        result = {**base, "category": "preflight_rejected", "effects_resolved": True,
                  "error_category": type(error).__name__, "counts": {"reserved_http_attempts": 0}}
        ops.atomic(directory / "result.json", result)
        return result
    intent = {"schema": "decision-replay-dispatch/v1", "execution": execution, "allocation": allocation,
              "checkpoint": selected["id"], "payload_sha256": selected["payload_sha256"],
              "profile_sha256": ops.sha(Path(profile_path).read_bytes()), "at": time.time()}
    # A crash after this durable intent is not permission to POST again. Operations also fences
    # duplicate workers; this protects the actual network-effect boundary inside that worker.
    with intent_path.open("x") as stream:
        stream.write(ops.encoded(intent) + "\n"); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        received = transport.send(raw, selected["payload_sha256"], config["endpoint"], headers=headers,
                                  execution_id=execution, allocation_id=allocation,
                                  deadline=assignment["deadline"], output_dir=directory / "transport")
    except Exception as error:
        # Intent is already spent. Keep available bytes and unknown remote state;
        # neither an exception nor a missing response is a retry authorization.
        received = {"error_category": type(error).__name__, "local_closed": None,
                    "remote_terminal_observed": False}
    # Transport is intentionally an opaque retained observation until its producer schema is
    # qualified. It cannot provide a quality score or adoption authority.
    response = provider_response(directory, received)
    observed = observation(response)
    content = {"schema": "decision-observation/v1", "parent_state_id": selected["parent_state_id"],
               "output": observed}
    artifact = host.put("decision_observation", content, source="replay-host", notify=False)
    remote, local = received.get("remote") or {}, received.get("local") or {}
    terminal = remote.get("terminal")
    local_closed = local.get("socket_closed") is True and local.get("watchdog_stopped") is True
    remote_closed = remote.get("terminal_observed") is True
    result = {**base, "effects_resolved": local_closed and (remote_closed or remote.get("state") == "not_reached"),
              "execution": execution, "allocation": allocation, "checkpoint": selected["id"],
              "parent_state_id": selected["parent_state_id"], "analysis_unit": selected["analysis_unit"],
              "source_mode": selected["source_mode"], "omitted": selected["omitted"],
              "payload_sha256": selected["payload_sha256"], "payload": payload_object,
              "checkpoint_object": checkpoint_object,
              "trace": retain_trace(host, directory, retained_assignment, received), "transport": received,
              "terminal": terminal, "observation": artifact, "category": observed["category"],
              "usage": (received.get("said") or {}).get("usage"),
              "counts": {"reserved_http_attempts": 1, "transport_requests_sent": local.get("requests_sent")},
              "scope": "one API request; no returned tool executed; no solver state or old allocation restored"}
    ops.atomic(directory / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("assignment")
    args = parser.parse_args()
    try:
        result = run(args.profile, args.assignment)
    except Exception as error:
        # Do not echo source bytes, headers or credential values through an exception message.
        print(ops.encoded({"status": "replay_not_completed", "error_category": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
    print(ops.encoded({"execution": result["execution"], "category": result["category"],
                       "terminal": result["terminal"], "effects_resolved": result["effects_resolved"]}), flush=True)


if __name__ == "__main__":
    main()
