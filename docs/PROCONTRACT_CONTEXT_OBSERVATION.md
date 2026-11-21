# Native input observations and research call contracts

The research harness exposes `source_context` beside `source_read`. It reads a
retained source object containing a native API request, or one explicitly selected
request from a JSON list. JSON and gzip use the same content-custody checks. The
call's explicit `max_bytes` bounds source loading and gzip expansion; this is a
reader resource bound, not an evidence-sufficiency rule or task deadline.

The decoder handles call-less named `agent` fragments, historical user fragments,
and inline `codex_internal_context` envelopes. Ordinary tool results and assistant
quotes are not context fragments. The inline transport guidance accompanying a
large packet remains separate from that packet. Missing/duplicate fragment
indices produce incomplete/ambiguous observations rather than an empty policy.
A contiguous fragment sequence has no end marker, so its completeness remains
limited to the observed prefix.

`source_context(identity, request_index=None, context_index=0, offset=0,
length=0, query=None, max_bytes=8388608)` returns the source identity/hash,
request and input-record coordinates, representation/coverage, decoded-context
hash, and a literal query result. The default omits content and input records.
Request a bounded UTF-8 byte view with `length>0`; `view_encoding=auto` emits text
when valid UTF-8, otherwise base64. Explicit `view_encoding=base64` returns only
base64. These encodings are never duplicated. `record_offset`/`record_limit`
independently page the original input coordinates. `query` searches the whole
decoded context, including fragment boundaries. Context offsets differ from
serialized-source offsets. A nonmatch means not observed in that context; it
does not establish that the policy was never delivered. Source labels identify
the recorded representation, not role authority, comprehension or causal effect.
No inferred actor role is returned. Unsupported source formats fail explicitly.

The compact default follows a real Luna readback: an8192-byte view expanded to
21025serialized bytes when text and base64 were both returned, and the actual
tool/gateway path truncated it. Query facts now precede optional content; a large
explicit page can still exceed a downstream channel, so byte-range reads remain
available. This changes the versioned observation interface, not the frozen
readback or its original successful scalar observations and truncation evidence.

This capability addresses the completed RSI retrospective that mistook a failed
raw JSONL substring search for nondelivery, and the later short native controls
whose inline representation was invisible to a fragment-only decoder. Request
selection is explicit; histories, actors and separate contexts are never merged
to manufacture a match. Raw `source_read` remains available for other formats.

Research `describe` also exposes capability-specific nested JSON Schemas for
`work`, `select.call.return_to`, and `wait`. Capability definitions are reused
with `$ref`; unoffered capabilities do not appear. `return_to` is a work object,
not an existing choice ID. `purpose` remains a legal work field. Wrong types now
receive a precise structural error before any selection is retained.

Identity domains stay distinct: `get` reads immutable objects;
`work_status(identity)` reads a selected choice and its operation;
`work_output(identity)` reads its retained outputs. The catalog describes these
interfaces without selecting a workflow, adding authority, or changing the
kernel. Dynamic admission, epochs, source IDs and version bindings continue to
be checked by the existing runtime.

Validation uses source-bound historical requests, positive and negative decoder
controls, a real local capability socket, and the standalone actor client against
ResearchRuntime. These establish observation and argument-contract behavior;
they do not measure task quality, model understanding or billed-cost improvement.

The native operation version binding includes the protected host Python source
distribution, including lazily imported query/decoder/schema helpers. Existing
named-entrypoint hashes remain inspectable. Integrate this version in a fresh
stage after owned work closes; do not replace the sources of an admitted action.
