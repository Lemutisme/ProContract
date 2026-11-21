"""Gateway-side custody for the opt-in actual native selector capture hook.

Call bind_request AFTER allocator/resource injection and BEFORE any upstream I/O.
No credentials or header values are retained. Capture metadata is a host linkage,
not a model claim; the capture directory must be inaccessible to candidate writers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from procontract_policy_splice import _json, _packets

META = ("procontract_capture", "procontract_capture_thread", "procontract_capture_request")
SHA = re.compile(r"[0-9a-f]{64}")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def same(left, right):
    return encoded(left) == encoded(right)


def _read(directory, identity):
    if not isinstance(identity, str) or not SHA.fullmatch(identity):
        raise ValueError("capture reference is not a SHA256")
    path = directory / (identity + ".json")
    if path.is_symlink() or not path.is_file():
        raise ValueError("native capture is not a private regular object")
    raw = path.read_bytes()
    if sha(raw) != identity:
        raise ValueError("native capture bytes changed")
    return _json(raw)


def _write(path, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("capture record already exists with another request") from None
        return
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _initial_prompt(body):
    try:
        packets, _ = _packets(body)
    except ValueError:
        texts = [part["text"] for item in body.get("input", []) if item.get("type") == "message"
                 for part in item.get("content", []) if isinstance(part, dict) and isinstance(part.get("text"), str)]
        start = '<codex_internal_context source="pro_contract_worker">\n'
        end = '\n</codex_internal_context>'
        prompts = [text[len(start):-len(end)] for text in texts if text.startswith(start) and text.endswith(end)]
        if len(prompts) != 1:
            raise ValueError("cannot identify the actual initial worker prompt") from None
        return prompts[0]
    return "".join(item[3] for item in packets)


def _base_instructions(body):
    # Current Core prepends BaseInstructionsFragment as a dedicated developer
    # message (client.rs), after optional Responses-Lite additional_tools.
    # Retained older shapes may instead have the Responses instructions field.
    if isinstance(body.get("instructions"), str):
        return body["instructions"], {"field": "instructions"}
    items = body.get("input", [])
    index = 1 if items and items[0].get("type") == "additional_tools" else 0
    if len(items) <= index:
        raise ValueError("native base instructions are absent")
    item = items[index]
    content = item.get("content") or []
    if (item.get("type") != "message" or item.get("role") != "developer" or len(content) != 1
            or content[0].get("type") != "input_text" or not isinstance(content[0].get("text"), str)):
        raise ValueError("native base instruction placement is not qualified")
    return content[0]["text"], {"input_index": index, "content_index": 0}


def bind_request(capture_directory, retained_directory, original, effective, *, gateway_request_id):
    """Return the retained binding, or None for requests outside the opt-in worker.

    Bytes are copied verbatim. Initial binding is write-once by native manifest;
    request ordinals >0 are retained as suffix/retry, never as another checkpoint.
    This function neither sends nor closes a request and creates no fork receipt.
    """
    if not isinstance(original, bytes) or not isinstance(effective, bytes):
        raise ValueError("capture requires original and effective bytes")
    before, after = _json(original), _json(effective)
    metadata = before.get("client_metadata") or {}
    if not any(key in metadata for key in META):
        return None
    if any(not isinstance(metadata.get(key), str) or not metadata[key] for key in META):
        raise ValueError("incomplete native capture metadata")
    ordinal = metadata[META[2]]
    if not re.fullmatch(r"0|[1-9][0-9]*", ordinal):
        raise ValueError("native capture ordinal is not canonical")
    if not isinstance(gateway_request_id, str) or not gateway_request_id:
        raise ValueError("gateway request identity is required")
    capture_root, retained_root = Path(capture_directory).resolve(), Path(retained_directory).resolve()
    if not capture_root.is_dir() or not retained_root.is_dir():
        raise ValueError("host must provision both custody directories")
    identity = metadata[META[0]]
    manifest = _read(capture_root, identity)
    if (manifest.get("schema") != "native-selector-capture/v1"
            or manifest.get("entry") != "initial_native_selector_exchange"
            or manifest.get("native_thread_id") != metadata[META[1]]):
        raise ValueError("native capture belongs to another worker")
    archive = _read(capture_root, manifest["archive_sha256"])
    if archive.get("schema") != "frozen-public-evidence/v1" or not same(archive.get("boundary"), manifest["boundary"]):
        raise ValueError("native archive differs from the captured boundary")
    for body in (before, after):
        if body.get("model") != manifest["model"]:
            raise ValueError("effective model differs")
        if (body.get("reasoning") or {}).get("effort") != manifest["reasoning_effort"]:
            raise ValueError("effective reasoning effort differs")
        if _base_instructions(body)[0] != manifest["instructions"]:
            raise ValueError("effective base instructions differ")
        if not same(((body.get("text") or {}).get("format") or {}).get("schema"), manifest["response_schema"]):
            raise ValueError("effective response schema differs")
        if not same(body.get("client_metadata"), metadata):
            raise ValueError("gateway changed native capture metadata")
        if body.get("previous_response_id") or body.get("conversation") or body.get("background"):
            raise ValueError("capture depends on unretained provider state")
    # A gateway may apply documented controls elsewhere; these fields must remain exact.
    for key in ("tools", "tool_choice", "parallel_tool_calls", "reasoning", "text", "instructions"):
        if (key in before) != (key in after) or not same(before.get(key), after.get(key)):
            raise ValueError("gateway changed selector tool or model settings")
    resource = after.get("input", [])[-1] if after.get("input") else None
    try:
        text = resource["content"][0]["text"]
        marker, end = '<task_resource_snapshot>\n', '\n</task_resource_snapshot>'
        if (resource.get("type") != "message" or resource.get("role") != "developer"
                or len(resource["content"]) != 1 or not text.startswith(marker) or not text.endswith(end)):
            raise ValueError("missing effective allocator projection")
        allocation = _json(text[len(marker):-len(end)])
        if (allocation.get("schema") != "task_resources/v1" or allocation.get("source") != "task_allocator"
                or not isinstance(allocation.get("allocation_id"), str) or not allocation["allocation_id"]
                or allocation.get("model_requests", {}).get("current_request_included") is not True):
            raise ValueError("invalid effective allocator projection")
    except (TypeError, KeyError, IndexError, AttributeError) as error:
        raise ValueError("missing effective allocator projection") from error
    initial = ordinal == "0"
    if initial:
        if not same(after["input"][:-1], before.get("input")):
            raise ValueError("gateway changed initial selector input beyond allocator projection")
        prompt = _initial_prompt(before)
        if prompt != manifest["prompt"] or sha(prompt.encode()) != manifest["prompt_sha256"]:
            raise ValueError("native prompt is absent, changed or truncated in actual wire")
        if any(item.get("type") in ("reasoning", "function_call", "custom_tool_call", "custom_tool_call_output")
               or (item.get("type") == "function_call_output" and item.get("call_id")) for item in before["input"]):
            raise ValueError("initial capture contains old tool or reasoning history")
    binding = {"schema": "native-selector-gateway-binding/v1", "capture": identity,
               "archive_sha256": manifest["archive_sha256"], "native_thread_id": metadata[META[1]],
               "request_ordinal": int(ordinal), "initial": initial, "gateway_request_id": gateway_request_id,
               "original_sha256": sha(original), "effective_payload_sha256": sha(effective),
               "base_instructions_location": _base_instructions(after)[1],
               "allocation": allocation, "boundary": manifest["boundary"],
               "closure": "not_observed_here", "admissible": False}
    for digest, raw in ((sha(original), original), (sha(effective), effective)):
        _write(retained_root / (digest + ".payload.json"), raw)
    _write(retained_root / (identity + "." + ordinal + ".binding.json"), encoded(binding))
    return binding
