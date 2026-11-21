# Harness evolution after the November 13 upstream integration

Base: `c79cbc14dbc5b0dcc00bf68d344479e8348a1750`, incorporating upstream
`69231e119bfca0b14453515e97d99688ce1c47c9`. This document selects a development
order; it does not claim the proposed repairs are implemented or measured.
The kernel continues to own responsibility, progress, evidence bindings and
authorized transitions. Execution, context, measurement and replaceable strategy
remain harness components, including for RSI.

November 14 candidate update: live publication status/reattachment and handoff
drain are implemented in `b940a7c401`; resolved worker capability/evidence facts
are implemented in `9fa561541d`. The original below describes the c79 baseline
and its evidence. The candidate passed 288 extension tests and four real-native
scripted-provider controls; its old-source live-waiter control fails as expected.
See [publication recovery](PROCONTRACT_PUBLICATION_RECOVERY.md). These results
qualify the mechanisms, not a benchmark quality/cost gain or a frozen-cohort
deployment. Artifact-capture completeness and actual reviewer image consumption
remain separate work in the independent RSI repository.

[Step credit for E2E RSI](PROCONTRACT_STEP_CREDIT.md) sharpens the research question:
retain each decision's delivered evidence and effects, then test the smallest
useful intervention. Operation ownership and evidence recovery are prerequisites
for broader checkpoint-based causal evaluation.

Evidence: the original seven TB4.1 failures, eight passing contrasts and three
separately labeled supplemental failures, retained with the experiment records, which are not part of this repository. Those solver attempts
used frozen `82db02f91` and adapter v5. The integrated package has scripted native
qualification, not a new paid benchmark quality result.

## 1. Preserve ownership across the loss of a caller

The math trajectory lost a code-mode caller while its publication reviewer was
still issuing requests. A repeated publication returned an unresolved-effects
error. The existing owned task survives caller loss, but a live operation has
no reattachment/status path through that interface.

Relevant implementation: `controller/runtime/progress.rs::publish_assessment`
spawns an owned task with a oneshot waiter; `progress_ready` can recover a completed
receipt but rejects a pending operation. The caller-loss test releases its gated
worker, waits internally for completion, and only then recalls the result. It
does not exercise reattachment or handoff while work remains live.

Add the smallest harness capability that identifies the admitted operation,
original request/options, owner epoch and eventual outcome. Status/reattachment
must distinguish still-owned work from an unknown owner/effect. Handoff must
account for that owned work. Reuse upstream command/tool/cell lifecycle events
and existing journal/epoch/grant machinery; do not add kernel workflow states.

Validation: gated live reviewer; lost waiter; matching and changed options;
completion racing reattachment; owner loss; cancellation; stale epoch; handoff;
finite and absent task deadlines. Assert one reservation, no duplicated effects,
no renewed grant, and exactly the retained outcome. Never repair this by clearing
pending or weakening the unknown-effects guard. The original code-mode host
exit's cause remains unestablished.

## 2. Capture and retrieve the evidence the work depends on

The ATRX provider filled 32 MiB with an alphabetically earlier dependency tree,
omitting the 8,909-byte declared output. A same-budget allocation control retains
the output by displacing one 12,495-byte third-party data file.

`controller/views.rs::workspace_view_for` already prioritizes required files
within a captured subject. Extend the provider selection boundary, not that
solved view problem: a view cannot recover bytes never captured. Reuse
`capture_projection`, the frozen-source barrier, evidence access receipts and
the public archive. Select task-authorized deliverables/evidence through a
general interface; preserve bounded allocation, omissions, source identity and
the conditions of capture. A filename heuristic is not provenance.

Use the upstream history/notes backend separation for source-addressed local
retrieval on custom Responses routes. Remote history still needs compatible
backend authentication. Index safe operation identities/outcomes; upstream
lifecycle telemetry explicitly forbids logging sensitive raw payloads. Authorized
task evidence remains a separate capture source.

Validation: same byte bound under different directory orders; multiple outputs;
large and binary outputs; oversized selections; exact historical-version reads;
omitted versus absent distinctions; traversal, symlink and permission boundaries;
stable subjects when captured state does not change.

## 3. Carry the conditions under which an observation supports a conclusion

Cargo's initial reserve interpretation preceded contract delivery; the draft
subsequently reinforced it. Batched's public APIs disagreed and review promoted
the original faulty caller to a reference. ATRX had a real external coordinate
conflict and a separately meaningful task-defined computation. Preserve those
distinctions rather than calling all three cases the same reasoning failure.

The harness should transport existing `source_quote`/`inferred` information,
original user text, reference/version identity and scoped observations without
promoting them during briefing, retrieval or context replacement. Strategy
decides how to investigate ambiguities and which reference is applicable.
Preserve useful conditional results and reversible artifact versions when an
external audit disputes their applicability.

Validation: new tasks with a faulty reference, two plausible conventions, a real
external inconsistency and a legitimate equivalent implementation. Include cases
where the stricter interpretation is required. Do not encode benchmark answers
or automatic preference for the most permissive interpretation.

## 4. Optimize context and work using actual cost and recoverable state

Batched spent about 53.5% of known cost on reviewers; CLS spent most on executor
and successor. Embedding's multiple reviews helped produce a passing artifact.
The recent four-pair general-harness candidate improved mean score by 0.5954pp
while increasing known cost by 24.659%. There is no universal review/reset cap
supported by these observations.

Provide role/action cost, actual input/retrieval and operation outcomes to
replaceable strategy. Compare focused retrieval and deliberate fresh windows
with summarizing compaction after the recovery path works. Retain useful
counterexamples and positive controls. Strategy chooses work that can change
the next decision, including a new method or rest; mechanism does not mandate
an investigation workflow or a finite task lifetime.

Reuse upstream typed context items and compaction. Async compaction requires V2
backend support and excludes TokenBudget; discarded summaries can still cost
money. Measure all-role billed work, missing charges, actual state recovery,
latency and final quality. Reduced prompt size is an intermediate observation.

## 5. Compose tools and make RSI adoption traceable

Reuse incremental tool catalogs, retained command sessions and per-operation
outcomes. Strategy chooses batching, checking, review and orchestration. Add
safe-success controls alongside rejection controls for transformations; bind
visual/behavioral observations to the actual running artifact. The CLS minimal
browser control showed identical pixels with different old-selector heights;
it did not establish full-site equivalence or change the official score.

Ordinary improvement responsibilities use existing Operations inbox/wake,
assignments, version resolution, outcome and authorized adoption. Importing
knowledge is not delivery to a research agent; delivery is not a tested candidate;
a favorable control is not deployment. Keep each recorded separately. The
current historical RSI host's limited editable-file scope is not silently widened
by importing these observations.

Validate one principal intervention at a time where practical, with both passing
contrasts and failures. Retain negative outcomes and score/cost tradeoffs. Fresh
selected attempts bind the actual executable, tools, strategy and environment;
frozen cohorts retain their admitted packages. The order above follows current
dependencies and is not a universal improvement workflow or a second RSI kernel.
