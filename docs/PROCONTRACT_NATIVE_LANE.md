# The native task lane: current behaviour

[Documentation](README.md) · [Kernel and harness](PROCONTRACT_LAYERS.md) ·
[Code map](PROCONTRACT_CODE_MAP.md)

These notes describe how the native extension's task lane currently handles
deadlines, assessment, continuation, interruption and resources. They apply the
boundary of the [layer document](PROCONTRACT_LAYERS.md): what is described here
is harness behaviour, and none of it adds a kernel transition. Where the lane
still selects workflow in code, the notes say so.

## Deadlines and allocations

The native adapter accepts an optional `SuccessionSettings.total_secs` and
persists a nullable deadline. Ordinary `procodex` tasks default to no hard task
deadline. Progress and continuation do not require a finite grant. Explicit
limits, including those of earlier tasks and benchmark profiles, keep their
original meaning across restarts. Local action/tool limits remain separate.
Interruption retains responsibility and completed work without certifying handoff
or completion; a later turn may resume the same task.

A task allocation does not itself choose an executor workflow. In the current
adapter, a deadline-only allocation with zero fresh sessions must preserve a
separately authorized same-thread repair allowance. It must not create a fresh
executor, renew the deadline, or convert an exhausted nonzero successor allowance
into additional local work.

## Review and terms

Review transport preserves independent scope: a rejected support binding
becomes a gap for that obligation, while unrelated observations and findings
remain usable. Ambiguous identities or contradictory envelopes still require
correction. Retaining local results creates neither new authority nor whole-task
Support, and choosing the next investigation remains strategy work.

Resolved reviews retain a shared citation table. Coverage references a stable key;
the corresponding source hash, byte range, quotation, subject and terms bindings
are stored once in that review. Existing public paging, checkpoints and diagnostic
continuation carry the table with its judgments. The model-message allowance is
distinct from host-derived evidence representation; reusing evidence does not make
an otherwise valid response too large. Legacy inline reviews remain readable.

The terms adapter keeps non-obligatory request context (preferences, permissions,
background) separate from acceptance obligations. Strategy interprets that context;
its storage does not grant execution authority. Independent review still compares
the classification with the original request, so placing a genuine requirement in
context cannot silently remove it. This is a request/evidence representation in
the harness/store, not a new settlement-kernel transition.

## Assessment and continuation

Execution authority and acceptance terms have distinct sources. In the native
task lane, strategy may select an undisputed derived obligation or the exact
host-retained original request admission. A terms dispute can block whole-task
acceptance without revoking the user's original tool/resource grant. Request
selection is bound to the intake record, thread and original text; current owner
fences, cancellation and original allocations still govern dispatch. It does not
authorize extra goals in disputed terms or certify the natural-language plan.
The strategy may continue, diagnose, verify or rest; no workflow is forced.

An assessment does not itself choose the next execution. The review wire now
describes request/terms discrepancies as omitted, added or altered obligations,
anchored to the original request and affected catalog IDs. Evidence gaps and finite
coverage remain obligation judgments. Historical unanchored records retain their
original interpretation; they are not silently rewritten by a newer strategy.
Anchors establish source identity, not the truth of the model's interpretation.

After capture/proposal and after selected observations, `continuation.md` chooses
among the offered capabilities: public checks, focused diagnosis, full acceptance
verification, executor continuation, successor, or rest. A handoff does not itself
schedule full review. The selector can read the task's immutable public archive;
it has no mutable execution tool. The harness retains the choice and dispatches
the selected operation under the original authority and resources.

Local checks and diagnosis retain their observations without issuing Support or
Defeat. Only selected full verification uses the original acceptance policy and
its kernel transitions. A local observation may guide repair before formal review,
and a captured partial delivery may rest without a completion claim. Capture source
(delivery or in-turn publication) is independent of the assessment's purpose.
Local operations omit withheld acceptance cases and their eligibility gates;
the original issued policy remains unchanged for full verification. Complete public
check receipts can be reused by a diagnosis in the same immutable selection loop.

Local continuation is reserved before dispatch and remains spent across recovery.
Owner input fences stale selections, and native interruption remains distinct from
an artifact failure. Original deadlines and successor reservations still apply.
The existing interpretation of local versus fresh-executor allowances is preserved:
an exhausted nonzero successor grant cannot become additional local attempts.
This compatibility boundary is not a new kernel invariant.

Verification summaries and successor transport describe observations, authority
and context. Their former instructions about which experiment to do first and
when to repair/hand off live in replaceable strategy text. Initial preparation and
the single review-protocol correction remain legacy code-selected workflow;
this is a partial migration, not a claim that all
orchestration has moved out of code. No RSI coordinator is introduced.

Continuation wire choices are a tagged union: check/verify/rest carry a reason,
diagnose carries a question, and resume/successor carry a next step with either
undisputed requirement IDs or the separately offered `request_admission` handle.
The output schema and parser
express the same alternatives. Repeated IDs do not widen a scope and are normalized;
unoffered actions and scopes still cannot create authority. Old stored selections
remain readable as historical records and are never replayed as new requests.

The selection's durable event hash links successor reservation/return or local
submission acknowledgements. A returned successor may identify a captured delivery
by its exact turn. Strategy receives these records alongside the current assessment
and the offered executor capabilities. Submission, return, artifact change and a
successful repair remain distinct; an absent link, including a legacy record, is
unknown rather than proof of nonexecution. Local submission does not claim a
completed turn. This adds observation transport in the harness, not a kernel state
transition or an instruction to retry.

The text benchmark adapter disables the native image-viewing tool. If another tool
still returns nontext content, the gateway delivers an explicit incomplete-observation
notice and retains available text plus an audit hash. It never forwards the omitted
content or changes the model/request/time grant. A modality mismatch in a tool result
therefore remains actionable feedback rather than terminating the entire task. This
profile-specific limit does not remove image capability from ordinary ProContract use.

Automatic turn admission also queues its additional context and instruction together.
Both are available in the first sampling step, alongside pending mailbox input. Starting
with context while deferring its instruction caused an avoidable second model response
for large worker packets. This is a general native harness input-assembly correction;
it does not convert an automatic turn into user authorization or change task grants.

## Interrupted execution and selected work

The captured work representation is independent of a completed handoff. After a
native executor failure, or a successor failure with confirmed shutdown, the
adapter retains an interruption snapshot and exposes it to the same continuation
strategy. Checks and diagnosis do not propose that snapshot to the kernel. If
strategy selects full verification, that operation proposes the exact captured
subject; repeated verification does not create duplicate proposals. The archive
exposes the interruption observation without manufacturing a delivery receipt.

Root failure during preparation remains recorded until the existing preparation
finishes, then reaches this selection path. The benchmark driver must follow the
lane's disposition: a failed root turn does not independently terminate active
preparation or continuation. User interruption and unresolved executor lifetime
retain their distinct stopping behavior. Original reservations, resource limits
and owner epochs remain in force; no retry-count policy is added to the kernel.

This migration does not yet provide autonomous recovery after controller process
loss or selector failure, or execution selection independent of issued terms.
Those broader continuation/wake capabilities remain explicit implementation work.

Publication preparation has its own execution outcome. When capture fails and the
executor has been confirmed resumed, or local materialization fails before any
assessment dispatch, the harness records a finished operation error. The original
reservation stays spent. The error remains readable and a repeated request recalls
that outcome; a new request can do further authorized work under the original grant.
An unknown barrier/resume or assessment effect remains unresolved. A failed local
preparation does not imply either a failed artifact or an outstanding external action.

These operation records use a separate public evidence scope from artifact assessment.
They cannot become diagnostic findings, Support or Defeat. Journal recovery can
finish an interrupted bookkeeping step from the retained operation outcome without
replaying its effects. Choosing whether to repair a fixture, change methods or make
another publication remains strategy work.

## Allocator observations

The shared model gateway projects an allocator snapshot into every request after
reserving that call. It records the exact forwarded payload hash and snapshot.
The remaining count includes the current call and all task roles; failed calls
stay spent. Absent time or count limits remain absent. These are observed resources,
not workflow instructions, future reservations, acceptance claims or user time
preferences.

The same committed allocator state is published atomically to a provisioner-owned
host file. Optional `resource_snapshot` settings connect that view to the native
controller. Known exhaustion prevents a new selector or worker from starting; a
previously selected non-model check may finish and retain its results. Missing or
inconsistent views remain unavailable, while the allocator still authoritatively
admits every request. The view cannot renew a grant. No fixed review reserve or
universal task countdown is introduced. A missing publication grant also does not
hide a separately declared native task deadline.

Public archive records identify their observation scope. Scope restrictions survive
check-only or unrelated partial observations; a new explicit judgment on that
obligation can revise them. Older-generation judgments cannot supersede newer ones
merely by arriving later. These are harness evidence relations, not extra kernel
states or benchmark-specific success rules.

