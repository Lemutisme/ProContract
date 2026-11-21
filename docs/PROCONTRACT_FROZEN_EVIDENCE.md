# First S3 delivery: a frozen public evidence view

`scripts/procontract_frozen_evidence.py` captures and reopens the evidence
component of a selector state. It is a working data export/reader with a
zero-model, two-process restoration control. It is not yet the native archive
export bridge, a model exchange loop, or a complete solver checkpoint.

The proposed selector-only intermediate step is feasible. The existing native
query semantics are already read-only (`controller/archive.rs`, `EvidenceAccess::read`).
The public tool entry is not a read-only store client: `read_observed` reserves an
access receipt and appends the prepared response, and `finish_access` appends its
completion (`controller/observations.rs`). A frozen parent must not receive those
writes. The new continuation's host owns its new access records and grant.

## Delivered capability

`capture(read, boundary)` is a **host-only** adapter. `read(query, byte_budget)`
must be the native unobserved public query capability bound to one invocation;
`boundary()` must observe the actual contract/journal prefix and invocation source
bindings. The host holds the capture barrier. The exporter pages all three
indexes, reads exactly their referenced public handles, verifies every content
hash, and checks the boundary again. It does not interpret arbitrary store paths
or infer an old prefix from today's final store. Unexpected I/O failures abort;
only an authoritative `QueryError` becomes a retained denial.

The view includes:

- `index`, `frontier` and invocation-bound `sources`, in their original order.
- All reachable public evidence bytes, identified by their native SHA-256 handle.
- Explicit denied handles, including indexed references that the native public
  reader would not allow to be read; no private blob is imported as a fallback.
- The host-observed state boundary. Metadata equality checks consistency, not the
  truth of a caller's declaration; actual native binding remains required.

`retain(host, parent_operation, payload_bytes, read, boundary, target_file)` also
requires an actually finished Operations parent and its immutable outcome, with
confirmed shutdown. That outcome must bind
`measurement.selector_checkpoint = {payload_sha256, boundary}` to these inputs.
An arbitrary closed operation is insufficient. It writes an exclusive hash-pinned
snapshot and immutable `frozen_public_evidence` / `selector_evidence_capture`
objects linking the original assignment and outcome. These are component-capture
objects, **not** qualified `operations-fork/v1` receipts.

`FrozenEvidence.load(path, file_sha256).read(query, budget=8192)` serves the frozen
view. Reads never open SQLite or the original source directory and never write
the snapshot. It accepts only `sources/index/frontier/read`, applies the handle
allowlist and frozen source bindings, pages eight entries or 2048 UTF-8 bytes,
and shrinks replies to the requested JSON byte budget. Unknown operations,
foreign handles, invalid offsets and modified bytes are rejected.

The JSON-lines service is directly runnable:

```
python3 scripts/procontract_frozen_evidence.py SNAPSHOT.json --sha256 FILE_SHA256
```

Each stdin line is `{"query":{"op":"sources"},"budget":8192}` or another
read query. Each stdout line is a result or explicit error. This service provides
no model, shell, arbitrary path, network, write, capture or deployment operation.
Its code is a bounded Python implementation of the native query response rules;
equivalence to every native archive state still needs a differential check when
the native exporter is connected.

## Actual test scope

Six controls in `test_procontract_frozen_evidence.py` include one real closed
Operations parent followed by two new Operations child allocations/subprocesses.
The parent and archive are synthetic, explicitly labeled. Each child reopens its
own snapshot copy and returns identical query results, including a foreign-handle
denial; all three assignments differ and all child processes close. The producer
capability is unavailable in either child. Snapshot bytes remain unchanged.

Other controls cover changing capture boundaries, wrong parent binding, unknown
handles and queries, UTF-8 byte boundaries, paging/response budgets, corrupt or
missing bytes, wrong named-source subject, nonprogressing pages and I/O failure.
This establishes evidence-component export/reopen behavior, not that a historical
native selector or solver has been restored. The tests make zero model calls.

```
python3 -B -m unittest discover -s scripts -p test_procontract_frozen_evidence.py -v
```

Set `FROZEN_EVIDENCE_TEST_ARTIFACTS` to a new directory to retain the real parent
and child assignments, outcomes and snapshot copies.

## Remaining connections

The native capture bridge must supply the actual unobserved query capability and
boundary and produce the parent binding above. A prospective snapshot is taken at
that selector boundary; a closed run's final database is not automatically its
earlier evidence namespace. Native archive/reference response parity remains
unqualified until that bridge is exercised.

A selector exchange runner must restore the payload and this view under a new
grant, expose only the frozen evidence tool through the existing restricted
code-mode mechanism, retain each new request/tool response/access receipt, and
stop at a structured decision or the declared grant/terminal failure. v1's
`exec` scripts cannot be handed to a shell or Python eval. The model loop, actual
producer/continuation receipts, host reader joins and owner admission are not
part of this first component delivery. Files, processes, pending work, full
environment and original resources remain explicitly unrestored; new allocations
do not renew the parent's grant.
