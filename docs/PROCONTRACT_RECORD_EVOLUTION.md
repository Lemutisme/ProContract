# Record-level responsibility: proposed V2 semantics

Status: design; not a migrated store,
deployed kernel, or claim of empirical improvement. V1 remains authoritative
until the storage and adapter transition is qualified. The source findings and
the selection are recorded with the experiment records, which are not part of this repository.

## What a record means

Three objects must not be collapsed:

1. A proposal binds immutable artifact bytes to a Contract revision and a fresh
   generation. Two proposals of the same bytes are different events.
2. A judgment binds its exact proposal, evidence, evaluator, environment and
   reach. Support is a judgment, not formal acceptance. Its historical bytes
   remain available after a later proposal or withdrawal.
3. An acceptance binds an authorized Discharge to an exact supported judgment.
   This is the acceptance record in the paper's dependency semantics. A task's
   selected acceptance determines formal delivery. Merely finding an old Support
   in a V1 history cannot create such a record.

Artifact hash, proposal ID, judgment ID and acceptance ID have distinct types.
An artifact identifies content; the other IDs identify immutable ledger events.
Their content and authenticated provenance cannot be overwritten. Liveness and
selection are current projections of events, not mutable truth inside a record.

## Task projection

V2 retains revision, bindings, owner and a monotonic concurrency version, with:

- a pending proposal and its exact supporting acceptance IDs;
- historical proposals and judgments;
- historical acceptance records;
- an optional selected acceptance;
- lifecycle status derived from release and selected-acceptance validity.

Proposing a replacement does not discard a selected live acceptance or reopen a
fulfilled task. It also does not silently select the replacement. A new successful
Discharge accepts the new judgment and selects that exact acceptance. Selecting a
different *existing* live acceptance is an explicit Settler operation. Ranking
quality, evidence strength or costs belongs to owner/strategy context.

Useful delivery does not require formal acceptance. A harness may publish an
exact retained proposal or supported judgment as a provisional result while the
task remains owed. It must retain that delivery's identity and status; it cannot
present it as the selected formal acceptance. This matters when several useful
artifacts exist but none yet has an authorized Discharge. Kernel liveness and
owner/strategy selection must not turn lack of settlement into loss of work.

Released tasks admit no new execution through this projection. Revision changes
the claim; it invalidates the task's earlier records as supporting acceptances.
Resuming an executor, storing a partial artifact and changing its harness do not
themselves revise the claim, discharge responsibility or renew resources.

## Evidence edges and invalidation

Edges name exact earlier acceptance records and are conjunctive. An opaque source
digest is still useful for custody, but is not a replacement for these typed
edges. Chronology, parent task and identical artifact bytes do not imply support.

Before a pending proposal or acceptance is admitted, every named supporting
acceptance must be live. Edges only point to existing records; this provides an
acyclic order without a domain-specific workflow. A derived acceptance retains
the exact edge set bound to its judged proposal.

Withdraw or Challenge resolves its exact historical target. Withdrawing a
judgment seeds every acceptance derived from that judgment; withdrawing an
acceptance seeds that acceptance. The transaction traverses the full historical
reverse graph, including already inactive intermediate records. It deactivates
the entire descendant closure and cancels pending proposals using an affected
support. Revise and Release seed every acceptance of their task.

Only tasks whose selected acceptance is affected lose that selection and reopen
(unless released). A surviving older acceptance is not automatically selected.
A later fresh acceptance of identical bytes has a fresh ID and does not revive
old descendants. Unrelated live records and task selections survive unchanged.

All affected task versions advance in the same transaction. A stale command must
fail as a whole: no partially invalidated graph, partially changed incumbent or
successful delivery built from a mixture of pre/post states.

## Store and migration boundary

The reducer receives a consistent affected graph, authenticated command and
expected versions. It returns a deterministic event/projection delta. SQLite
discovers and locks the affected closure, validates all reads and commits the
delta, journal and actual selection together. Replay must produce the same
projection and must not issue external effects.

V1 serialization, event hashes and raw history stay immutable. Migration must
record an anchor, source chain and explicit mapping of genuinely supported and
settled historical coordinates. No migration by artifact-hash deduplication is
allowed. A V1 Support without Discharge stays an unaccepted judgment. A legacy
task's previous scope/reach is not reclassified by a newer adapter.

Old-writer fencing is a storage requirement, not a startup check in V2. One
candidate implementation is a database trigger requiring a V2 connection-local
capability function for every authoritative write; old connections cannot invoke
the function and their transaction fails. This needs a real old-binary write
test, including a connection opened before migration, before it is selected.
The authoritative V2 tables, V1 archive boundary and all journal write paths
must be covered. A new process checking a schema number alone is insufficient.

Kernel-schema migration is distinct from the currently selected 20e→d7 harness
replacement demonstration, which retains the existing kernel and ledger format.

## How A and C consume this relation

A registers the actual evidence channel and the claim that channel establishes.
Host receipts carry observed execution facts, not inferred universal compliance.
Beyond reach requires a protected independent channel relative to both producer
and selector, or a complete mechanical criterion. An authorized convention may
then settle the supported coordinate. Evidence exposure and convention identity
are retained; no fixed sample count or success percentage enters this reducer.

C freezes an ordinary Selection Contract's comparison, effect, uncertainty and
cost rule before candidate generation. The harness evaluates the frozen rule;
the kernel knows its bindings and supporting accepted measurements, not metric
units. Adoption names the resulting acceptance. Incumbent delivery requires a
live selected acceptance; explicitly authorized experimental delivery has a
separate binding and is not misreported as adoption. Withdrawal invalidates
dependent adoption in the same transaction as its supporting measurement.

## Qualification order

First implement a separate executable *reference specification*, with no runtime
authority. Use it for differential tests of the production reducer. Cover:

- identical artifact bytes with distinct acceptances and distinct descendants;
- withdrawal of a nonselected ancestor and traversal through inactive records;
- selected withdrawal with another surviving acceptance and no fallback;
- proposal alongside an existing selected acceptance;
- revision/release and cancellation of dependent pending proposals;
- stale expected versions and atomic rejection across multiple tasks;
- restart/replay, old-writer rejection and serialized concurrent transactions;
- delivery selected before an intervening withdrawal;
- ordinary work and improvement work through identical transitions.

The specification is a testing oracle, not a second operational RSI kernel.
Wire adapters and migrate only after this storage/transition layer passes. Then
measure the harness capabilities on preregistered fresh task families. Report
mechanical correctness, actual deployment and empirical benefit separately.

## First implemented primitive

`codex-rs/pro-contract/src/records.rs` now supplies an append-only topology of
typed acceptance IDs, immutable acceptance records and exact support edges.
Insertion rejects duplicate identities, unknown parents and cycles through its
existing-parent order, without partial mutation. Iterative reverse traversal
computes the full historical descendant closure. It deliberately takes no
current-selection or liveness filter and contains no task/model/metric logic.

The five new controls include 6,144 comparisons against an independently written
forward fixed-point specification, identical-content/different-record histories,
historical intermediate records and atomic invalid insertion. All 41 kernel
library tests pass. Qualification and exact source hashes are retained with
the experiment records, which are not part of this repository.

This primitive does not yet authenticate acceptance, enforce live-parent
admission, update selections, change the V1 reducer, migrate a store or implement
cross-task atomicity. Those remain the next integration layer. Exposing the
topology library does not create operational acceptance or adoption authority.
