"""Bounded semantic views of retained native request input, without role inference."""
import base64
import gzip
import hashlib
import io
import json

from procontract_actor_identity import native_contexts


def source_context(host, identity, request_index=None, context_index=0, offset=0, length=0,
                   query=None, max_bytes=8 << 20, prefix="pro_contract_packet", source="pro_contract_worker",
                   view_encoding="auto", record_offset=0, record_limit=0):
    """Read one context from an immutable API request (or a selected request list).

    max_bytes explicitly bounds both stored bytes and gzip expansion. Context
    offsets are UTF-8 bytes after decoding, not offsets in serialized JSON.
    The default returns identity/coverage/query facts, without a content dump.
    Explicit content and record pages remain independently retrievable. A nonmatch
    reports only what this representation contains, never nondelivery.
    """
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive source/expansion bound")
    if type(context_index) is not int or context_index < 0 or type(offset) is not int or offset < 0 or type(length) is not int or not 0 <= length <= 65536:
        raise ValueError("context_index/offset must be nonnegative; length is 0..65536 (0 omits content)")
    if view_encoding not in ("auto", "base64"):
        raise ValueError("view_encoding must be auto or base64")
    if type(record_offset) is not int or record_offset < 0 or type(record_limit) is not int or not 0 <= record_limit <= 64:
        raise ValueError("record_offset must be nonnegative; record_limit is 0..64 (0 omits records)")
    if query is not None and (not isinstance(query, str) or not query):
        raise ValueError("query must be a nonempty literal string or null")
    if not isinstance(prefix, str) or not prefix or not isinstance(source, str) or not source:
        raise ValueError("prefix and source must be nonempty context labels")
    descriptor = host.get(identity, "source")
    if descriptor["bytes"] > max_bytes:
        raise ValueError("retained source exceeds max_bytes; select a smaller source or increase the explicit read bound")
    raw = bytearray()
    while True:
        page = host.source_read(identity, offset=len(raw), length=min(65536, max_bytes))
        raw.extend(base64.b64decode(page["base64"]))
        if page["eof"]:
            break
    encoding = "gzip" if raw.startswith(b"\x1f\x8b") else "json"
    if encoding == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("expanded source exceeds max_bytes; no context absence was established")
    request = json.loads(raw)
    if isinstance(request, list):
        if type(request_index) is not int or not 0 <= request_index < len(request):
            raise ValueError("a request list requires its explicit zero-based request_index")
        request = request[request_index]
    elif request_index is not None:
        raise ValueError("request_index applies only to a request list")
    if not isinstance(request, dict) or not isinstance(request.get("input"), list):
        raise ValueError("source must contain a native API request with an input list, or a list of such requests")
    contexts = native_contexts(request["input"], prefix=prefix, source=source)
    result = {"source": identity, "source_sha256": descriptor["sha256"], "source_encoding": encoding,
              "request_index": request_index, "context_index": context_index, "context_count": len(contexts),
              "scope": "Observed native input representation only; no inferred role, authority, model use, causal credit or nondelivery."}
    if not contexts and context_index == 0:
        return {**result, "state": "not_observed", "coverage": "unknown", "query": None}
    if context_index >= len(contexts):
        raise ValueError("context_index exceeds the observed context list")
    context = contexts[context_index]
    result.update({key: context[key] for key in ("representation", "source_label", "state", "coverage")})
    record_count = len(context["records"])
    if record_offset > record_count:
        raise ValueError("record_offset exceeds context records")
    result.update(record_count=record_count, content_included=False)
    if record_limit:
        records = context["records"][record_offset:record_offset + record_limit]
        result.update(records=records, record_offset=record_offset, record_end=record_offset + len(records),
                      records_eof=record_offset + len(records) == record_count)
    if context["text"] is None:
        return {**result, "query": None}
    data = context["text"].encode("utf-8")
    if offset > len(data):
        raise ValueError("offset exceeds decoded context")
    result.update(context_sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    if query is not None:
        position = data.find(query.encode("utf-8"))
        result["query"] = {"literal": query, "state": "observed" if position >= 0 else "not_observed_in_this_context",
                           "offset": position if position >= 0 else None}
    if length:
        view = data[offset:offset + length]
        result.update(content_included=True, offset=offset, end=offset + len(view), eof=offset + len(view) == len(data),
                      slice_sha256=hashlib.sha256(view).hexdigest())
        text = None
        if view_encoding == "auto":
            try:text = view.decode("utf-8")
            except UnicodeDecodeError:pass
        if text is None:
            result.update(view_encoding="base64", base64=base64.b64encode(view).decode())
        else:
            result.update(view_encoding="utf8", text=text)
    return result
