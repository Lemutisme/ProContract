# Durable operations and scoped version selection

[Documentation](README.md) · [Code map](PROCONTRACT_CODE_MAP.md) ·
[Research runtime](PROCONTRACT_RESEARCH_RUNTIME.md)

Under the [kernel + evolvable harness architecture](PROCONTRACT_LAYERS.md), this
interface is a harness capability. The same kernel carries ordinary task and RSI
improvement responsibilities; the harness's strategy library chooses their work
and research methods. Version adoption and actual later use retain separate
receipts. This ownership does not add a second RSI kernel or automatically enable
campaign-wide analysis/adoption.

`scripts/procontract_operations.py` supplies execution and evidence operations to
ordinary tasks and improvement responsibilities. It calls the existing
`pro-contract-store` kernel; it adds no learning or goal transitions. The older
`scripts/procontract_rsi.py` remains a historical fixed-workflow campaign driver.
The new interface neither imports its scheduler nor adopts its empirical gates.

A protected provisioner creates `Operations` with an `Authority` and explicitly
admitted `Measurement` capabilities. The authority names its original source,
deployment scope, editable files, permitted measurements and optional actual
limits. Missing deadlines/count limits remain absent. A changed host implementation
requires explicit migration before new work, rather than silently reinterpreting
an old assignment. Measurement executable/configuration sources are pinned and
checked before execution. The caller may choose an action allocation; the host
caps it by the admitted capability and original authority.

`procontract_operations_rpc.Broker` exposes a local capability socket for one
responsibility. Mount the socket into its offline environment; keep the host
journal, authority, sealed evaluator and inputs outside it. Actor identity and
settlement provenance come from that binding. The native executor mounts only runtime binaries/resources, not host scripts or
measurement inputs. Read-only visibility still counts as reach. The actor cannot bootstrap a version,
create authorization evidence, choose an unoffered measurement, widen its editable
files or supply a human settlement. The example research context is
[`strategies/research/operations.md`](../strategies/research/operations.md).

The capability surface is intentionally small:

| Operation | Meaning |
| --- | --- |
| register / get / read | Immutable v2 version definitions and incremental event retrieval |
| resolved | Instructions actually supplied, retrieved sources and run conditions |
| observation / knowledge | Outcomes or explicitly labeled claims; scoped interpretation/correction, without adopting behavior |
| submit / dispatch / inspect / reconcile / cancel | One explicit assignment, durable reservation, process identity, outcome and original resources |
| subscribe / inbox / wakes / wait | Selected event inputs, coalesced/reconstructible pending cursor, durable waiting; selector failure does not acknowledge input |
| adopt | Existing kernel Issue/Propose/Support and atomic Discharge + selection, with actual delegated provenance and an expected-incumbent fence |

Evidence intake and delivery now have explicit views:

- The provisioner can `capture_source` with an expected hash. Bytes are retained
  independently of the original path and read through bounded `source_read` pages.
  Each page binds the source, offset, end and byte digest; binary/partial UTF-8
  pages retain exact base64 bytes. The actor cannot provide an arbitrary host path.
- `search` offers literal, snapshot-bound retrieval by text/kind. Its cursor pages
  an evidence query; it is **not** an input acknowledgement. Correction links remain
  visible as `superseded_by`, without making the newer interpretation certified truth.
- `receive` returns the selected subscription input, owner epoch and a `through`
  cursor for the actual delivered page. It neither acknowledges input nor starts
  work. A selected `wait` through that boundary leaves later input pending.
- `knowledge` can retain applicability and counterevidence references in addition
  to its interpretation, uncertainty and correction link. These are strategy-authored
  claims, not automatic policies or kernel truth states.

`procontract_evidence_bundle.py` imports explicit source/observation/claim manifests;
duplicate import is idempotent. Domain interpretation stays outside the importer.
`procontract_native_operation.py` is the shared native adapter used by qualification
and research. `submit_pending` reserves a caller-selected native action for the
retained input identity; repeating it does not create a new assignment/allocation.
It does not choose a research sequence or automatically retry failed actions.

Native model/effort come from the selected definition; an invocation can have no
hard deadline. Actual supplied action limits remain enforced. A native directory
has exclusive admission, and a short, separately mounted capability socket avoids
coupling Unix socket limits to long durable workspace paths. Per-invocation offered
operations/measurements can narrow the host authority. Output capture preserves
regular-file identities, never follows actor-controlled links into host files,
and leaves large/binary artifacts retained instead of forcing them into text replies.

The host implementation fingerprint includes operation, evidence, RPC and store
binding sources. A repeated `issue` with changed terms is rejected rather than
silently returning the earlier responsibility as if it had been reissued.

Version definitions identify all supplied instruction files, code/build, model,
resolver and tool identities; task and research lineages are distinct. The actor's
file bytes are content-addressed. A provisioner can explicitly permit changes to
build/model/resolver/tool definition fields; these are declarations whose actual
use must be bound by the execution adapter. They are not self-authenticating build
attestations. Per-run resolved inputs are separate from version definitions.
The qualification adapter fingerprints all package files, including the tool host,
and its actual resolver/transport/host sources; it rejects a changed pinned build
before inference. It retains exact native model request payloads as well as
version/context identities and allocator request/usage records for every role.

Dispatch can be delivered repeatedly. A worker claims the durable reservation
before starting external work. Cancellation/finalization belongs to the operation,
not the requesting transport. A retained receipt is reconciled into one outcome
and wake transaction after caller/process loss. A lost worker without a receipt
is `unknown`; re-dispatch does not repeat it and its observation still wakes subscribers.
Authenticated effect scopes fence conflicting actions. Confirmed local process/group
shutdown can reconcile an interrupted result without inventing quality. Original assignments and allocations
are not renewed. This is not an exactly-once claim about external effects. Local
process shutdown cannot certify container/remote shutdown after the client failed;
those lifetimes require a retained adapter attestation. The native qualification
adapter confirms/removes only containers with that invocation's exclusive host
mounts. Its selected-wait handling quiesces the invocation after the actor turn
ends, even if an unrelated legacy preparation worker is still running.

Adoption is scoped to the original authority and a host-admitted measurement. The
outcome must bind the complete retained assignment, not merely an operation name.
Importing historical evidence with a colliding name cannot make it evidence for a
new version, environment or allocation. The
protected evaluator determines its own admissibility result; no numeric research
threshold is built into the operation host. Presumed adoption requires actual
beyond-reach evidence. The actor's interpretations or within-reach self-tests
cannot supply that condition. Initial selection cites the provisioner's standing
authorization and makes no performance claim. Kernel/store settlement conditions
are unchanged. A lost adoption acknowledgement returns the original committed
receipt; a different request or stale incumbent is rejected. Committed receipts remain
readable after allocation expiry; that read creates no new authority. A research
version cannot inherit the existing task deployment's kind. Cancellation and an
admitted adoption have a single host ordering. A later kernel defeat makes the
selected version unavailable; the host preserves the latest selection sequence
and requires an explicit new decision rather than silently choosing a fallback.

New hosts explicitly bind `selection_protocol=native-acceptances/v1`. Their
incumbent view and lost-ack recovery consult the store's transactional acceptance
and incumbent records; a journal entry that resembles a selection has no authority.
The selection sequence is the CAS fence, while the acceptance sequence identifies
what was accepted. Re-settling identical content through ordinary kernel commands
does not revive an old acceptance. Existing `ResearchRuntime._work` resolves its
default version through this same view, with no additional version selector.

A host without this protocol binding needs explicit migration before selecting or
bootstrapping. Its immutable objects and already-retained decision results remain
readable. New provisioning cannot claim an existing unbound kernel store as a fresh
scope. These interfaces do not themselves switch an old runtime or migrate a store.
The credit module's preregistered improvement terms and paired measurement judgment
are a subsequent integration step; the existing adoption path above still uses its
original host-qualified measurement rule.

The native runtime additionally retains a cancelled action's terminal receipt,
lets a publication outlive its caller's transport, waits for a known local
inspection owner to finalize before assessing its receipt, and permits a fresh strategy
decision after confirmed check/reviewer interruption. Thread teardown still
cancels work. Unknown effects remain blocked. Completed shutdown without a
caller-level computation result spends the original slot but does not invent an
observation or prevent a separately authorized next action.

For a still-owned publication, `progress_status` now exposes its original
request/options, epoch and action deadline. An exact `publish_progress` repeat
joins that owner rather than creating another operation. Handoff drains known
work before proposing; unknown pending effects remain blocked. Completed result
lookup remains available after expiry and runtime reconstruction. The capability
and measured limits are described in [publication recovery](PROCONTRACT_PUBLICATION_RECOVERY.md).

Run the small real-model qualification explicitly:

```sh
PRO_CONTRACT_STORE_BIN=/path/to/package/bin/pro-contract-store \
  python3 scripts/qualify_procontract_operations.py \
  --root /new/isolated/episode --package /path/to/package --image IMAGE
```

The fixture uses GPT-5.6-Luna/max, native ProContract, offline containers, an
independent sealed reducer measurement, and separately recorded task/research
allocations with no model-request ceiling. A native task outcome atomically wakes
the research responsibility; the model uses the socket to diagnose, author a
candidate, select experiments and decide adoption. A fresh native task loads the
selected version. The fixture contains no replacement patch or promotion decision.
This is a deliberately synthetic defect and tests operational closure, not broad
improvement, causal ProgramBench gains or model-weight reinforcement learning.

Fault controls are in `scripts/test_procontract_operations.py`; native lifecycle
controls are in the extension's execution/progress/inspection tests. Unknown native
process effects, pre-Issue recovery and automatic in-process retry of a failed
legacy native selector are not silently repaired by this interface: the generic
host preserves their operation outcome/pending input for a separately authorized
arrangement. Existing fixed campaigns remain pinned and receive no automatic
migration or hidden feedback.


The real-trajectory research pilot is `scripts/qualify_procontract_insights.py`.
Its input bundle, controls, model-authored knowledge and version decisions are
separate from the frozen ProgramBench corpus. Protected decision/reporting checks
are finite controls; they do not establish general task-performance gains.

Input-bound native assignments identify the acknowledged cursor as well as the
pending boundary and owner epoch. Processing one page therefore permits the caller
to select work on the remaining page; duplicate delivery of that same input interval
retains its original assignment and allocation. The host still does not select a
research workflow or require analysis after every event.

When provisioning evidence for research, retain its actual subject and affected
claim identities. A generic topic label is not a substitute for those relations:
the literal retrieval interface searches retained record metadata, not the contents
of every source file. Historical context can be imported under its original source
while the research subscription selects fresh inputs. Choosing which evidence to
announce, inspect and revise remains context-level orchestration.

[Step credit for E2E RSI](PROCONTRACT_STEP_CREDIT.md) shows how existing observation,
knowledge, resolved-input and assignment/outcome objects can retain decision-level
attributions. Local executable interventions, counterfactual model continuations
and cross-task improvements remain distinct measured questions; no credit-specific
kernel transition or automatic reward is introduced.

An opt-in responsibility recovery companion is available in
`scripts/procontract_responsibility_recovery.py`. It attaches to an existing
ResearchRuntime with pinned source/configuration and a provisioner-selected work
policy. If a native responsibility owner closes without selecting a disposition,
the companion queues one linked return under the current epoch, generation,
research version and original host authority. The existing supervisor dispatches
it. What to inspect or do next remains the prompt/skill policy supplied to that
owner; the companion does not select measurements or adopt a version.

An explicit wait, cancellation, live or unresolved effect, or already selected
call/work disposition prevents re-entry. In particular, a leaf's closure before
its automatic return is queued must not be mistaken for an unselected caller.
The producer must have exited; final closure and selection-graph checks share
the transaction that queues the return. Concurrent controllers and lost polling
responses do not create another return for the same unfinished invocation.
The ended action and its allocation remain retained; returning responsibility
does not retry that action or renew a fixed task grant.

This recovery profile covers an established native owner channel. A zero-response
startup failure still needs owner handling; automatic availability-triggered
recovery is not implemented here. The companion neither provisions a second host
nor edits the running protected bundle. A fresh owner invocation and its actual
disposition must be observed before claiming that it completed an RSI loop.

## Feedback capabilities

Native outcomes can retain a separate source of public tool-step events, with
original step/thread/turn IDs, start/completion state, exit status, output and raw
RPC coordinates. `work_trace(choice)` pages metadata; `record` and a list of
`field` keys select a value for bounded byte reads. Final-message projection no
longer determines whether a later researcher can inspect tool receipts. Missing
or legacy producer bindings stay explicit; a source finalized for another
operation, assignment or version is rejected rather than attributed to the caller.
Capture runs during native cleanup, including Python
exception paths; failed outcomes can expose the same step-source pointer. Host-only
return finalization binds an existing assignment and can retain its evidence after
expiry or cancellation without admitting another operation. A trace-capture failure
does not erase the task result. A process killed before cleanup still needs recovery
of its retained files; this change does not claim every hard-crash path is covered.

`knowledge_view` follows explicit `supersedes` edges at a stable query snapshot.
It retains branches and declared applicability and supplies paging handles, with a
default response limit of 4096 bytes. Long fields use `object_view` byte pages.
The view does not choose a latest statement as universal truth. Work can carry typed
`knowledge` IDs whose views enter its runtime context at dispatch. The original
objects and historical assignments are unchanged.

Measurement capabilities may declare `subject_paths` within their existing
project. The host binds those file/type/mode identities and the declared runner,
environment and inputs into a separate measurement-subject object. The full
snapshot still accompanies the outcome. This makes earlier evidence discoverable
when only unrelated notes changed; it neither suppresses another measurement nor
certifies that the declared dependency list is complete.

`procontract_review_profile.py` supplies the shared configuration path for an
explicit reviewer computation grant. Local ProCodex uses it, and the staged
Terminal-Bench adapter patch delivers a selected profile into actual native
settings, using a provisioner-declared analysis image or the owned task image.
The verifier image is not selected. An omitted profile leaves existing settings
intact. This is executable frozen-evidence inspection; pixel viewing
is a separate capability. Existing native reservation and task limits remain.
The staged adapter qualifies the actual Python entrypoint and records probe
closure during preparation, before arming its solver allocation. Its settings
must use that qualified, pinned image. This detects a missing interpreter without
claiming that every task dependency or possible computation has been validated.

Reviewer CPU, memory, process and scratch-space limits can be explicitly declared
in `review_execution.resources`. Omission keeps the historical small-analysis
limits; an explicit identical default has the same grant identity. Changing an
existing grant's resources cannot renew its deadline or spent calls. The tool and
receipt expose effective limits. Allocation across concurrent executor/reviewer
containers remains a provisioner responsibility; per-container values are not an
aggregate task-resource guarantee.

## Issued terms and their retained digest input

`Operations.issue` retains an `issued_terms` object before submitting a new Issue.
Its versioned binding contains the exact JSON input string sent to the native
digest command, its byte hash, domain, returned digest and admitted CLI identity.
The string survives outer JSON key sorting by `atomic()` and object storage.
This object is a retained input/intent, not evidence that Issue committed; the
kernel's current Contract remains authoritative. Retention sends no notification
and admits no work.

On reopen, the harness reproduces the accepted native digest from that input and
compares requested JSON structure, allowing object-key reordering while keeping
array order, values and numeric representations distinct. It returns the original
Contract without re-Issue or a new allocation. No native hash algorithm or old
event encoding changes. Existing issuance readback remains possible after an
action deadline; this does not admit new execution.

A legacy Contract without this receipt can be backfilled only from a preimage
that reproduces its original terms hash. A sorted display with no surviving
preimage is not authority to rewrite the hash. Keep recovered historical
preimages and their verification as separate receipts. Protected live runtime
source and frozen experimental packages require their existing explicit delivery
procedure; this development change does not hot-swap them.
