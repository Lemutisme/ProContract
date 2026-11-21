"""Capture and reopen one invocation's public evidence capability.

Host-only capture takes a read capability and a stable boundary probe. The model
gets only queries on retained bytes, never a store path or a capture capability.
This is the evidence component of a checkpoint, not a solver/session restorer.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
import sys

import procontract_operations as ops
from procontract_decision_replay import strict_json

SCHEMA = "frozen-public-evidence/v1"
DIGEST = re.compile(r"[0-9a-fA-F]{64}")
PAGES = ("index", "frontier", "sources")


class QueryError(ValueError):
    """An authoritative query denial, distinct from capture/I/O failure."""


def _handle(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise QueryError("expected 64 hex characters")
    return value.lower()


def _query(query, budget):
    if not isinstance(query, dict) or set(query) - {"op", "handle", "source_id", "offset"}:
        raise QueryError("unknown evidence query fields")
    if len(ops.encoded(query).encode()) > 4096:
        raise QueryError("evidence query exceeds 4 KiB")
    if query.get("op") not in (*PAGES, "read"):
        raise QueryError("unknown evidence operation")
    offset = query.get("offset", 0)
    if type(offset) is not int or offset < 0:
        raise QueryError("offset must be a nonnegative integer")
    if type(budget) is not int or budget < 0:
        raise QueryError("invalid response byte budget")
    handle = query.get("handle")
    if handle is not None:
        handle = _handle(handle)
    source = query.get("source_id")
    if source is not None and not isinstance(source, str):
        raise QueryError("source_id must be a string")
    return query["op"], offset, handle, source


def _page(entries, offset, budget):
    if offset > len(entries):
        raise QueryError("index offset exceeds the available entries")
    end = min(offset + 8, len(entries))
    while True:
        result = {"entries": entries[offset:end], "offset": offset, "next_offset": end,
                  "eof": end == len(entries), "total": len(entries)}
        wire = ops.encoded(result).encode()
        if len(wire) <= budget:
            # A Python caller gets the same isolation as the JSON-lines caller:
            # nested page entries must not remain aliases into the frozen view.
            return strict_json(wire)
        if end <= offset + 1:
            raise QueryError("evidence response budget is too small")
        end -= 1


def _text(data, handle, offset, budget, named=None):
    try:
        data[:offset].decode("utf-8")
        data[offset:].decode("utf-8")
    except UnicodeError:
        raise QueryError("offset must be a UTF-8 byte boundary within the recorded evidence") from None
    if offset > len(data):
        raise QueryError("offset must be a UTF-8 byte boundary within the recorded evidence")
    end = min(offset + 2048, len(data))
    while True:
        while end > offset:
            try:
                text = data[offset:end].decode("utf-8")
                break
            except UnicodeDecodeError:
                end -= 1
        else:
            text = ""
        result = {"handle": handle, "offset": offset, "next_offset": end,
                  "eof": end == len(data), "total_bytes": len(data), "text": text}
        if named is not None:
            result.update({key: named[key] for key in
                           ("source_id", "source_subject_hash", "selected_subject_hash")})
        wire = ops.encoded(result).encode()
        if len(wire) <= budget and (end > offset or end == len(data)):
            return strict_json(wire)
        if end == offset:
            raise QueryError("evidence response budget is too small")
        end = offset + (end - offset) // 2


class FrozenEvidence:
    def __init__(self, body):
        # Copy/validate JSON so the caller cannot later mutate the restored view.
        self.body = strict_json(ops.encoded(body))
        if self.body.get("schema") != SCHEMA:
            raise ValueError("unknown frozen evidence schema")
        self.pages = self.body["pages"]
        if set(self.pages) != set(PAGES) or any(not isinstance(x, list) for x in self.pages.values()):
            raise ValueError("all three public indexes must be captured")
        self.blobs = {}
        for handle, value in self.body["blobs"].items():
            raw = base64.b64decode(value, validate=True)
            if _handle(handle) != handle or ops.sha(raw) != handle:
                raise ValueError("captured evidence bytes differ from handle")
            raw.decode("utf-8")
            self.blobs[handle] = raw
        self.denied = self.body["denied"]
        if not isinstance(self.denied, dict) or any(not isinstance(v, str) or not v for v in self.denied.values()):
            raise ValueError("denied handles require recorded reasons")
        if set(self.blobs) & set(self.denied):
            raise ValueError("a handle cannot be both readable and denied")
        self.named = {}
        for entry in self.pages["sources"]:
            name = entry["source_id"]
            if not isinstance(name, str) or not name or name in self.named:
                raise ValueError("source names must be unique")
            handle = _handle(entry["handle"])
            if handle in self.blobs and strict_json(self.blobs[handle]).get("subject_hash") != entry["source_subject_hash"]:
                raise ValueError("named source differs from its frozen subject binding")
            self.named[name] = entry
        required = _handles(self.pages)
        if set(self.blobs) | set(self.denied) != required:
            raise ValueError("capture must account for exactly the indexed public handles")

    @classmethod
    def load(cls, path, expected_sha256):
        raw = Path(path).read_bytes()
        if ops.sha(raw) != expected_sha256:
            raise ValueError("frozen evidence file differs from the pinned bytes")
        return cls(strict_json(raw))

    def read(self, query, budget=8192):
        op, offset, handle, source = _query(query, budget)
        if op in PAGES:
            # Native Query accepts the optional fields here but does not use them.
            return _page(self.pages[op], offset, budget)
        if handle is not None and source is not None:
            raise QueryError("read accepts either handle or source_id, not both")
        named = None
        if source is not None:
            if source not in self.named:
                raise QueryError("source_id is not bound in this invocation")
            named = self.named[source]
            handle = _handle(named["handle"])
        if handle is None:
            raise QueryError("read needs a handle from the task index or an invocation source_id")
        if handle in self.denied:
            raise QueryError(self.denied[handle])
        if handle not in self.blobs:
            raise QueryError("evidence handle is not public evidence of this responsibility")
        return _text(self.blobs[handle], handle, offset, budget, named)


def _handles(pages):
    values = set()
    for entry in pages["index"]:
        values.add(_handle(entry["handle"]))
        for key in ("inspections", "review_checkpoints"):
            values.update(_handle(x) for x in entry.get(key, []) or [])
        for key in ("delivery_message", "host_observations"):
            if entry.get(key) is not None:
                values.add(_handle(entry[key]))
    for entry in pages["sources"]:
        values.add(_handle(entry["handle"]))
    for entry in pages["frontier"]:
        values.add(_handle(entry["evidence"]))
    return values


def capture(read, boundary, *, max_entries=10000, max_bytes=64 << 20):
    """Host-only adapter for the native *unobserved* query capability.

    boundary() must report the actual contract/journal prefix and invocation
    bindings. Capture requires an owner-held barrier, then verifies this boundary
    did not change. An I/O failure is never converted to an authoritative denial.
    This function does not infer historical boundaries from a final store.
    """
    before = strict_json(ops.encoded(boundary()))
    if not isinstance(before, dict) or not before:
        raise ValueError("capture needs a host-bound state boundary")
    pages = {}
    for op in PAGES:
        entries, offset, total = [], 0, None
        while True:
            page = read({"op": op, "offset": offset}, 8192)
            if total is None:
                total = page["total"]
            if (type(total) is not int or not 0 <= total <= max_entries or page["total"] != total
                    or page["offset"] != offset or not isinstance(page["entries"], list)
                    or type(page["next_offset"]) is not int or type(page["eof"]) is not bool
                    or page["next_offset"] != offset + len(page["entries"])
                    or page["next_offset"] > total or page["eof"] != (page["next_offset"] == total)):
                raise ValueError("inconsistent public index page")
            entries += page["entries"]
            if page["eof"]:
                break
            if page["next_offset"] <= offset:
                raise ValueError("public index did not advance")
            offset = page["next_offset"]
        pages[op] = entries
    blobs, denied, used = {}, {}, 0
    for handle in sorted(_handles(pages)):
        raw, offset, total = bytearray(), 0, None
        while True:
            try:
                page = read({"op": "read", "handle": handle, "offset": offset}, 8192)
            except QueryError as error:
                if offset:
                    raise ValueError("evidence became unavailable during capture") from error
                denied[handle] = str(error)
                break
            if total is None:
                total = page["total_bytes"]
            data = page["text"].encode("utf-8")
            if (type(total) is not int or total < 0 or used + total > max_bytes
                    or page["total_bytes"] != total or page["handle"] != handle or page["offset"] != offset
                    or type(page["next_offset"]) is not int or type(page["eof"]) is not bool
                    or page["next_offset"] != offset + len(data) or page["next_offset"] > total
                    or page["eof"] != (page["next_offset"] == total)):
                raise ValueError("inconsistent evidence byte page or capture limit exceeded")
            raw.extend(data)
            if page["eof"]:
                if ops.sha(bytes(raw)) != handle:
                    raise ValueError("captured evidence does not match its public handle")
                blobs[handle] = base64.b64encode(raw).decode("ascii")
                used += len(raw)
                break
            if page["next_offset"] <= offset:
                raise ValueError("evidence byte page did not advance")
            offset = page["next_offset"]
    if ops.encoded(boundary()) != ops.encoded(before):
        raise ValueError("source state changed during capture")
    body = {"schema": SCHEMA, "boundary": before, "pages": pages, "blobs": blobs, "denied": denied}
    FrozenEvidence(body)
    return body


def retain(host, parent, payload, read, boundary, target):
    """Retain the evidence component under an actually closed Operations parent.

    read/boundary and the target path are provisioner capabilities, never actor
    tool arguments. Native capture bridge and historical as-of reconstruction
    are separate work; a caller-supplied declaration is not their proof.
    """
    item = host.inspect(parent)
    if item["state"] != "finished" or (item.get("outcome") or {}).get("shutdown_confirmed") is not True:
        raise ValueError("checkpoint parent has not closed")
    outcome = host.get(item["outcome"]["object"], "operation_outcome")
    if outcome["assignment"] != ops.sha(item["request"]):
        raise ValueError("parent outcome belongs to another assignment")
    if not isinstance(payload, bytes) or not isinstance(strict_json(payload), dict):
        raise ValueError("selector input must be retained JSON bytes")
    body = capture(read, boundary)
    expected = {"payload_sha256": ops.sha(payload), "boundary": body["boundary"]}
    bound = (outcome.get("measurement") or {}).get("selector_checkpoint")
    if ops.encoded(bound) != ops.encoded(expected):
        raise ValueError("closed parent did not bind this selector payload and evidence boundary")
    raw = (ops.encoded(body) + "\n").encode()
    # Exclusive output makes repeated calls reviewable instead of replacing a checkpoint.
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        import os
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    target.chmod(0o400)
    evidence = host.put("frozen_public_evidence", body, source="checkpoint-host", notify=False)
    return host.put("selector_evidence_capture", {
        "schema": "selector-evidence-capture/v1", "parent": parent,
        "parent_assignment": ops.sha(item["request"]), "parent_outcome": item["outcome"]["object"],
        "payload_sha256": ops.sha(payload), "payload_base64": base64.b64encode(payload).decode("ascii"),
        "evidence": evidence, "evidence_file_sha256": ops.sha(raw), "boundary": body["boundary"],
        "scope": "payload and public evidence only; native export bridge/solver restoration not certified",
    }, source="checkpoint-host")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    view = FrozenEvidence.load(args.snapshot, args.sha256)
    for line in sys.stdin:
        try:
            request = strict_json(line)
            result = {"result": view.read(request["query"], request.get("budget", 8192))}
        except (ValueError, KeyError, TypeError) as error:
            result = {"error": str(error)}
        print(ops.encoded(result), flush=True)


if __name__ == "__main__":
    main()
