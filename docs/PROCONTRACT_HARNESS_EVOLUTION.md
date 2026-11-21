# Harness evolution from retained trajectories

Decision record, November 13. Current candidate starts at `acd5bf659`; benchmark
results cited below belong to their recorded earlier packages. One kernel owns
responsibility, progress, evidence bindings and authorized transitions. The
harness provides the capabilities below; strategy chooses their use.

## Prioritized interfaces

| Component | Observed gap | Smallest useful evolution | Selection stays in strategy |
|---|---|---|---|
| Working context and retrieval | The four new mdbook/tree-sitter traces had large contexts without offered reset/history tools. An earlier eight-task context bundle improved score and known cost, with adverse cases. | Make working state and source retrieval available across both manual and automatic context replacement. Preserve current task, actual grant and outstanding effects. | Which state matters, when to switch, what to retrieve, whether recovery is worth its cost. |
| Execution observations and tool output | mdbook's executor received paired watcher results that later review could not see. A current provider collector projects only a bounded recent tail. | Retain source-addressable historical call/result observations with a paged index; deliver selected records through the existing trusted observation interface. Preserve failed, pending, partial and superseded observations. | Which past observation is relevant; whether its version and conditions still answer the current question. |
| Tool and process lifecycle | Code-mode cells, native process sessions and external jobs have different handles and lifetimes. A finished call need not end detached effects. | Carry producer, operation identity, completion state and continuation/cancellation handles through tool return, context switch and handoff. Batch independent work while retaining each member's result. | Batch shape, wait versus cancel, investigation versus repair. |
| Assessment and work selection | tree-sitter repeatedly pursues local output mismatches while major behavior remains incomplete; an empty public failure list misses unexamined interfaces. | Present current work, scoped findings, actual comparison conditions and previous action/outcome links together with source access. Existing prior-review transport must not promote old conclusions to new proof. | Expected task value of a repair or experiment, competing explanations, whole-task versus focused review. |
| Execution capabilities and environment | FP8's declared user did not reach a direct docker-exec adapter. MCP and reviewer-image access also have distinct configured and actual delivery boundaries. | Resolve the task's capabilities into the actual invocation and record a small real readback: identity, filesystem, tools, modalities, network and devices as applicable. Keep setup, executor and verifier identities separately scoped. | Which allowed resources to use. Offline benchmarking is one profile; it is not a general long-horizon restriction. |
| Measurement and economics | Shared session IDs overwrote actor identity; fewer calls sometimes cost more; scalar score ties hide gained and lost tests. | Join by exact actor/request/window/operation identity, retain charged all-role usage, missing charge status and per-condition outcomes. Keep telemetry passive. | How to trade latency, cost and information; when a model/role change is worthwhile. |
| Version delivery and RSI event consumption | New observations were stored in an evidence host whose subscription still selects only the earlier RLM measurement source. | Use existing inbox/wake/assignment/version interfaces to deliver explicitly selected evidence to a research responsibility. Record selection, actual resolved version, exposure, measured outcome and adoption separately. | Which insight merits a candidate, what experiment can reject it, when to adopt or revert. |

These are interfaces within the harness, not seven services or a required seven-stage
workflow. No universal test quota, fixed reset interval or global deadline follows.

## Evidence that changes the next action

The eight-task context study records baseline 91.8997% versus note+reset 93.0240%,
known cost down 8.1842%. Nomino retains its score at 75.72% higher cost, and Tparse
loses 0.4706 points at higher cost. The treatment changes both manual capability
and automatic compaction mode. Preserve the measured bundle result without
claiming that resets are always economical or isolating one cause from it.

The four new general-harness pairs improve mean score by 0.5954 points but raise
known cost by 24.659%. In the deeply inspected tree-sitter pair, execution inputs
grow from 132.50M to 251.64M tokens while reviewer cost falls. In mdbook, execution
inputs fall from 238.24M to 208.84M with more execution calls. Cost management needs
actual usage and changed decisions; request count is insufficient.

The current actor helper corrects all 5,453 requests in those four traces without
changing total charges. It is already used by the retro projection. The current
context profile passes two actual native controls with scripted replies, finite
and open-ended, and no paid model calls. These qualify identity/transport/lifecycle,
not a new quality-cost result.

## The next observation interface should be retrievable, not a bigger tail

Offline replay of the existing Frontier v4 collector on the archived candidate
mdbook run finds 562 native CommandExecution observations with a sufficiently
large source slice. Its default 8 MiB-per-log scan observes 470 and projects the
latest 20; 450 scanned older events are omitted. Actual earlier successful watcher
results are among the omissions. Some recent watcher results survive, so the
collector is useful; it is not a complete historical source.

This collector was not active in that original ProgramBench run. The replay
demonstrates the future adapter's retention boundary, not the cause of the original
missing receipts. Keeping more recent events would only move that boundary.

Reuse `controller/capture_projection.rs` producer binding and
`workers/review_evidence.rs` scoped evidence delivery. Add an index of retained
observation handles and fetch exact event/output pages as needed. A source binding
must retain actor, invocation, execution conditions, observed time, completion and
the artifact/version relation actually known. An unbound historical command can
remain useful historical evidence without certifying current bytes. The projection
should say which source ranges it did not observe.

First controls: old successful call outside the default tail; failed then repaired
call; changed artifact with an old success; partial output; a still-live operation;
model/task text that resembles a native receipt. Then check actual reviewer receipt
delivery and whether the next selection uses it. A score comparison comes after
this composition works; static collector counts cannot establish score improvement.

## RSI is an execution path, not just a knowledge store

At this audit, the `procontract-rlm-rsi-20261109/host` evidence store has 80
observations, 47 from the subscribed `rlm-terminal-measurement` source. Its inbox
cursor is 0, pending event sequence 359; these are positions, not event counts.
Other observation sources and knowledge records do not automatically enter that
subscription. This does not mean no person/agent used them, or that every research
runtime is idle. It identifies the delivery gap in this particular intake path.

The code already has source selection, inbox pages, wake, assignment, delivery and
authorized adoption. Use them for a selected research responsibility rather than
adding another kernel or a hard-coded universal retro loop. Preserve the older
host's three-file authority and use an explicitly scoped configuration for broader
harness research. Source-specific inbox state should not be silently rewritten to
make an old run appear to have consumed new insights.

## Source map

- `scripts/procontract_actor_identity.py`: exact thread and unambiguous session
  joins; passive retrospective measurement.
- `config/procontract-context-windows.toml` and
  `core/src/session/turn.rs::run_auto_compact`: native capability/profile boundary;
  see [context details](PROCONTRACT_CONTEXT_COST.md).
- `ext/pro-contract/src/controller/capture_projection.rs` and
  `ext/pro-contract/src/workers/review_evidence.rs`: producer-bound observation
  provenance and exact frozen-source reading (paths relative to `codex-rs/`).
- `scripts/procontract_operations.py`: `subscribe`, `receive`, `wait`, assignments
  and fenced adoption. `scripts/procontract_research_runtime.py`: event-driven
  execution and actual selected-version delivery.
- `codex-rs/ext/pro-contract/policies/`: replaceable work-method, continuation and
  reviewer context. Instructions choose workflow; they do not acquire authority.

Exact source hashes, replay output, intake snapshot and prior empirical reports
are retained with the experiment records, which are not part of this repository.
