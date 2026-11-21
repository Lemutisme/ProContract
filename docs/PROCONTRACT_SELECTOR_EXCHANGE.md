# Selector exchange components and their current qualification

This delivery extends the single-request replay with a native public-archive
export API, an isolated code-mode reader, an Operations exchange runner, and
receipts rederived from actual host records. Qualification includes native
fixtures and an actual retained Coq source with a scripted HTTP provider. Real
model continuations are separately admitted; full solver restoration is not
implemented by this selector capability.

## Native export

The extension exposes `SelectorArchiveInvocation`, `SelectorArchiveReader` and
`ArchiveCaptureLimits` from `controller/archive_export.rs`:

```rust
let boundary = SelectorArchiveReader::observe_boundary(
    ledger.clone(), blobs.clone(), &invocation,
).await?;
let reader = SelectorArchiveReader::bind(
    ledger, blobs, &invocation, boundary,
).await?;
let snapshot = reader.export(ArchiveCaptureLimits {
    max_entries: 10_000, max_bytes: 64 << 20,
}).await?;
```

The caller owns the existing connection and the capture barrier. The API opens
no database path and calls the actual native `EvidenceAccess::read`, never the
writing `read_observed` entry. It pins Contract state, kernel/journal heads and
invocation source bindings, checks the boundary around every query, and returns
the `frozen-public-evidence/v1` body accepted by the Python view. Only the native
reader determines public membership, the frontier and named sources. A stale
boundary, inaccessible assessment, corrupt blob or allocation overrun refuses
the export. An indexed-but-nonpublic handle remains an explicit denial.

The native differential control compares twelve queries against two independent
Python processes using the exported data. Private bytes are absent and source
event counts stay unchanged; appending a real event then invalidates the reader.
That fixture has an empty frontier/denial map and three ASCII blobs; this is not
a claim of exhaustive native compatibility. Existing Python controls separately
cover UTF-8, nonempty denials and nested page isolation.

## Only a frozen reader behind exec/wait

`procontract_selector_tools.FrozenToolSession` launches a hash-pinned existing
`codex-code-mode-host` over framed stdio. It registers only
`contract_read_evidence`; neither model code nor its callback can select a store
path or another tool. Native JS executes the code; no Python eval, shell or
replacement JS interpreter is used. Actual controls confirm that process,
require, fetch and Deno are unavailable and foreign tools/handles are refused.

Each session has new execution/allocation labels, a fresh native session,
optional read allowance/deadline, a declared heap allowance and bounded trace.
All callbacks and outputs are attributed to that session. The process inherits
the operation group so owner cancellation also reaches it; cleanup confirms the
child's exit. A yielded cell cannot be used through another session.

The retained qualified code-mode binary currently negotiates execution limits
but not server-side output truncation. The exchange applies a declared client
representation with the native approximate four-bytes-per-token convention,
explicit omission text, a 64 KiB observation ceiling and control headers outside
the data budget. `wait` limits and first-line `// @exec:` directives are parsed;
unsigned-integer checks exclude booleans. Host capabilities are recorded, and
unsupported wire fields are not silently sent to the older binary. Notifications
received while observing a cell are retained and included as labeled data in
the returned observation; they are never messages to another person/service.

This is not a claim of byte-identical Core frontend formatting or its complete
asynchronous mailbox behavior. Callback servicing currently occurs during
exec/wait observations. The actual renderer and code-mode binary form part of
the runner/profile identity; original environment state remains omitted.

## Operations exchange runner

`scripts/procontract_selector_exchange.py --profile PROFILE ASSIGNMENT` is a
registered measurement command. The protected profile fixes host, provider,
model/effort, code-mode binary and request/read allowances. The actual assignment
selects exactly one immutable `selector_evidence_capture` object in that host.
The runner verifies its source, registered source capability and exact
payload/evidence binding. For an Operations parent it checks the original closed
assignment/outcome. For a native source it uses the explicit typed origin
described below. It verifies and parses the **same profile bytes**.

This runner accepts the initial selector request only: prior reasoning, actual
tool calls/results, item references or provider-conversation state are refused.
The old terminal-prefix v2/v3 requests remain a separate single-request unit;
their original code-mode memory is not reconstructed by this runner.

The first POST is the exact retained payload. Later requests preserve its JSON
values and append the actual model output plus tool observations. Each POST has
its own retained intent/raw trace, external execution header and the original new
allocation. No request is automatically retried. A persisted uncertain intent is
spent; quota expiry, refusals, malformed output, unsupported tools and unknown
remote effects remain explicit outcomes. Local closure does not assert provider
closure. No returned `successor`, `verify` or other decision is enacted.

The result remains `observation_only`, `admissible=false`. JSON Schema validation
uses the captured schema and records validator dependency versions. External or
rebased schema references are refused. Raw calls, incomplete results, reported
costs and unpriced counts remain available. A valid JSON decision is not task
success or adoption evidence by itself.

Provision with `exchange.profile(...)`, register an Operations measurement whose
identity is `exchange.implementation()`, and register the version returned by
`exchange.version_definition(config)`. The source must be produced by a qualified
capture/import runner; the echo-parent in the old tests is explicitly a fixture
and cannot certify a native source.
`test_procontract_selector_exchange.py` supplies a complete runnable zero-model
example of parent → capture → two independent exchanges.

## Actual receipts and the host reader

`procontract_selector_receipts.ReceiptReader(host)` publishes the existing
`operations-fork/v1` checkpoint/continuation/measurement shapes, and its
`read(object_id, expected_kind)` rederives every body from actual immutable
capture, assignment and outcome records. It does not accept caller-authored
closure, costs, subject or unit labels. Checkpoint/continuation publication does
not require a measurement.

The checkpoint captures context and public evidence. Files, mutable Contract
runtime, exposure state, environment, pending work and old resources remain
explicitly omitted. `analysis_unit.id` is the native responsibility in the
captured boundary, with family `native-responsibility`: this is a grouping key,
not proof that different responsibilities are independent benchmark tasks.
Studies requiring another clustering design need an explicitly qualified mapping.
Only an improvement's actual `tolerated` terms can admit these omissions.

The optional built-in measure is **operational reliability**, not task quality:
whether the closed exchange retained a structured object satisfying its captured
schema. Register `config['reliability_measure'] = receipts.registration(config)`
before host creation/exchanges, and register the separate projection command
`procontract_selector_receipts.py --profile PROFILE ASSIGNMENT` with identity
`receipts.implementation()`. It takes `{'source': EXCHANGE_OPERATION_ID}` and
executes under its own actual Operations assignment. Unknown schema validity or
unclosed effects do not produce a Boolean measurement. Provider/configuration
categories stay retained; this property is not a benchmark model score.

`checkpoint(capture_id)`, `continuation(checkpoint_id, exchange_operation_id)` and
`measurement(projection_operation_id)` return immutable object IDs. The host
reader verifies the actual joins before returning their bodies. Alias-unit,
cost and outcome forgeries are rejected. Continuation costs contain observed
attempts/wall time, plus provider-reported dollars only when every call is priced;
projection/research costs are separate. The diagnostic pool identity stays fixed
when the environment changes; no freshness, exposure reset or adoption is implied.
Credit code and the retained native store binary are unchanged.

## Retained native source and remaining restoration scope

The actual caller now captures an initial native view and effective request at
the same decision point; see `PROCONTRACT_SELECTOR_CAPTURE.md`. The closed Coq
collection supplies two such points from one responsibility. They are not two
independent tasks, and its final historical store is not treated as an as-of
snapshot. The native archive already captured at the point is the evidence view.

`procontract_selector_native_source` verifies the immutable bundle, including
original gateway DB/WAL through private byte copies, and retains a typed
`native_selector_source`. Its capture has
`origin={kind:"native_selector_source",object:ID}` and no Operations `parent`.
The exchange's `parent_measurement` names this host's registered **import
capability**, whose real completed operation is checked. It does not rename that
import operation as the original native execution.

The checkpoint receipt takes execution/allocation from the namespaced original
native run and gateway allocation. `source_origin` preserves the original root,
selector worker, turn, request coordinates and source object; `imported_by`
remains in that source's separate provenance. The reader rederives the source
from immutable proof and the completed import, rather than accepting a claimed
native parent or re-opening original source paths. The original raw archive hash
and restored canonical-file hash are both retained. Mixed native/Operations
parent fields are refused before sending.

`test_procontract_native_selector_recovery.NativeRecoveryTest` composes the real
retained Coq initial source, actual importer/Operations/code-mode processes and
two scripted HTTP continuations. It checks exact first-request bytes, equal
restored evidence, distinct new executions/allocations, complete source joins,
measurement receipts and `fork.measured`; a mixed-origin forgery sends zero
requests. The current helper is the same-source 2081 package binary, separately
qualified for the frozen reader. These are fake response controls, not real
model outcomes.

Actual A/A admission belongs to the experiment owner, with two new grants and
explicit request allowances. No returned action is enacted by the exchange.
Only context and public evidence are restored. Files, mutable kernel/controller
state, original execution environment, exposure, pending work and original
resources remain explicitly unshared. Original completion evidence is host
gateway terminal plus native return/closure; original raw response SSE was not
retained. Full executor restoration and a task-quality comparison remain further
work. No quality judgment/adoption has run, and the frozen 686d study is unchanged.
