# Decision studies as a research measurement

`scripts/procontract_decision_study.py` is an opt-in `ResearchRuntime`
measurement capability. A selected call supplies retained `candidate` and
`study` source IDs, and a normal `return_to` continuation. It produces an
observation, with `admissible=false` and `beyond_reach=false`. It does not change
the kernel, issue Support, adopt a version, enact a selector decision, or require
a positive mechanism test before other research work.

## Protected registration

Provision a fresh or explicitly migrated runtime; do not hot-edit a frozen host.
The measurement and `reconcile_argv` run the same study script and protected
configuration, with `--reconcile` added for recovery. Set
`measurement.external_effects=true`. Version 1 accepts a capability without a
project and uses immutable source IDs only:

```json
{
  "work": {
    "capability": "decision_study",
    "sources": {"candidate": "RETAINED_SOURCE_ID", "study": "RETAINED_SOURCE_ID"}
  },
  "return_to": {
    "capability": "research",
    "prompt": "Interpret the returned observation and choose further work."
  }
}
```

The provisioner-owned `decision-study-host/v1` configuration contains:

* `runtime_root`, `capability`, and `sources=implementation()`;
* `frames`: an allowlist of frame ID to exact file path/SHA, baseline policy
  path/SHA, and explicit exposure description. Frame bytes are a JSON array;
  every point has `point_id`, original `run_id`, `payload`, `payload_sha256`,
  `strategy_sha256`, and optional `family`, `original_action`, `pre_input`;
* `provider`: endpoint, model, effort, credential environment name, environment
  identity. The actor cannot supply these fields;
* `resources`: `max_calls`, `request_seconds`, `max_source_bytes`,
  `max_request_bytes`, `max_bootstrap_replicates`;
* `admission`: existing `capacity_root` and `disk_reservation_bytes`. The
  provisioner must register the actual shared registry, not a private substitute.

Runtime provisioning pins the script and config. The driver additionally pins
its imported implementation and validates the real Operations assignment, chosen
work, source bytes, full frame, policy spans and exact response schema before
dispatch. The resulting plan and payload copies are retained before any output.
Current replay/transport/Operations sources match the qualified 686d sender;
changing any implementation requires new configuration and qualification.

The pure `procontract_decision_study_spec.py` defines
`procontract-decision-study/v1`. Its fields bind candidate source/SHA, frame
ID/SHA, baseline SHA, a versioned reading, direction/effect/falsifier,
pre-input applicability, retained unknowns, A/B repeat counts, ordering and
analysis seeds, run clustering, bootstrap draws and A/A selection. See its test
fixture for a complete machine-readable example. Registered readings include
action delivery, serialized-JSON marker, delivered-JSON length and the separately
qualified zero-step check reading below. Arbitrary scorer code and post-output
applicability are rejected.

## Execution and recovery

Version 1 runs one child at a time in randomized point and within-point order.
Every planned repetition gets a distinct real Operations execution/allocation
and the original 686d single-POST entrypoint. There is no retry or tool execution.
The child host derives its authority from the selected parent operation, has an
exact call ceiling, inherits any parent deadline, and cannot adopt. Waiting for
the shared slot does not start any child grant.

A separate local keeper owns one registered shared slot. It checks the existing
pause, capacity, memory and disk conditions under `shared-admission.lock`, and
continues holding the slot if caller loss or a disconnected provider leaves
external effects unresolved. It exits only after child reconciliation establishes
closure. This does not resolve remote work merely because a local socket closed.
Lost keeper identity requires host reconciliation; the driver cannot mint a
replacement lease automatically.

Re-entering a started study reconciles the retained children and never fills
missing calls. Recovery cancels an undispatched reservation through its original
CAS-protected worker after writing cancellation; the worker records cancellation
before launching a sender, without a new allocation. It also cancels still-live
children and preserves uncertain external effects. The runtime's existing
reconciler can revisit this result. The initial actor can have ended; its selected
`return_to` is activated once by the existing runtime on the actual outcome, with
the original generation/epoch fence. A cancelled owner does not get a fresh return
grant.

## Reading the observation

The report retains all planned points and repeats. Point contrasts are means of
each arm's registered repeats. Missing, unstarted, unmeasured, unresolved or
wrongly bound observations remain unknown. Closure by itself is not a measured
non-delivery. A known non-delivery requires a bound replay observation of an
actual sent request and confirmed effects. Binary readings include worst-case
bounds for unknown repeats; unbounded delivery length has no invented finite
bound. The percentile interval resamples whole original runs, recomputing the
point mean; it is a working interval, not a task-quality or generalization claim.

A/A uses shared A1/A2 when registered. B/B is reported when B has at least two
repeats. Shared calls are named and paid once; the four cross-arm combinations
are not separate independent observations. Historical replies are never added
as fresh repetitions. Unsupported predicates are returned as preflight failure;
an empty applicable frame is not applicable, and missing applicability remains
unknown. The reported support/refutation is of the registered decision reading,
not a ProContract judgment or adoption.

The policy resolver remains limited to qualified contiguous named/legacy packets
whose concatenation starts with the full baseline policy plus two newlines and
facts JSON. Byte ranges outside that policy remain unchanged. It does not cover
arbitrary developer policies, tool changes, native rechunking, or full solver
restoration. A copied historical prefix keeps prior retrieval and reasoning fixed.

## Qualification

`test_procontract_decision_study.StudyTest` drives actual runtime selection,
measurement subprocesses, child Operations, loopback HTTP, raw retention, shared
admission, and return selection. Initial/return native actors and Docker image
inspection are fixtures. It does not call a real model or launch a container.
`test_procontract_decision_study_spec` checks the pure readout.

```sh
REPLAY_PROVIDER_FIXTURE=/path/to/qualified/fake_provider.py \
PRO_CONTRACT_STORE_BIN=/path/to/pinned/pro-contract-store \
python3 -B -m unittest test_procontract_decision_study.StudyTest \
  test_procontract_decision_study_spec -v
```

Run from `scripts/`. `STUDY_TEST_ARTIFACTS` optionally retains isolated stores,
assignments, payloads, raw responses, reports and custody records after each test.
This qualification does not deploy a tool into an existing research host or
replace the separately owned B-round driver.

## Registered zero-step check reading

The B author delivered a different measurement from unconditional action delivery.
`procontract_zero_step_check.py` is the exact reviewed author source, SHA256
`7bd845c9f76f1f50b99ae09a247c0b2f1794029221d1f7e2b1a46d3c0b83148b`.
It is a static host module, not an actor-selected executable path. Use:

```json
{
  "reading": {
    "id": "zero_step_check_avoidance",
    "version": 1,
    "parameters": {"closed_non_delivery": "unknown", "population": "known_applicable"}
  },
  "applicable": {"all": []},
  "prediction": {
    "direction": "increase", "min_effect": 0,
    "falsifier": {"kind": "nonpositive_interval", "min_effect": 0}
  }
}
```

These are fields of the full study spec. Extra applicability filters, another
population or non-delivery zero are not accepted for this registered reading.
Frame rows include the complete `facts` object. Before dispatch, the driver
compares it, with strict JSON types, to the facts decoded from the original
baseline payload. Changing a frame file and its digest cannot substitute
another set of capabilities for that request.

Applicability requires that `check` is offered and its native capability says
`executable_steps` is the integer zero and
`check_pipeline_executes_program_or_container` is the literal false. A positive
count with literal true is inapplicable; missing, malformed or inconsistent
capabilities are unknown. The statistical population is explicitly the known
true stratum. Unknown applicability outside that stratum is reported separately,
not made false and not silently used to claim a result for all input states.
The frozen B inputs have 49 true / 109 false / 60 unknown points; the 49 true
points come from four Terminal-Bench runs and four tasks. This is not a
218-task or cross-family primary result.

Within that fixed stratum, only a retained, closed, schema-valid complete
decision is scored: check is zero, another offered action is one. Tool requests,
refusals, invalid outputs and unknown outcomes remain `None`. Every planned
repeat is retained; any unknown primary point leaves the strict mean and interval
unknown, with the binary worst-case bounds reported separately. For this reading,
`unknown_calls` counts unknown scores **within the primary stratum**;
`not_applicable_calls`, `unknown_applicability_calls` and total `unscored_calls`
remain separately visible. Costs still account for every recorded call.

`nonpositive_interval` explicitly permits refutation when the oriented upper
endpoint is at most minus the declared threshold. The existing
`opposite_interval` keeps its strict less-than comparison. Thus an exact [0,0]
interval refutes this author's positive prediction when its threshold is zero;
this does not silently change the old readings' decision rule.

The same eligible stratum also reports a **separate** rest-delivery guardrail:
known closed non-rest delivery is zero, while unstarted, unresolved or unmeasured
calls are unknown. It shares the original calls and adds no cost, stopping or
adoption gate. A/A and B/B retain the primary score's unknown semantics. Neither
avoiding check nor selecting rest proves useful work or better task quality.

Qualification compares the 12 authored examples, all 218 original payload facts,
1,744 synthetic action/non-delivery cases, and exact prior readout compatibility.
It reads no formal B screening outputs. Additional tests are in
`test_procontract_zero_step_reading` and
`test_procontract_decision_study.RegisteredStudyTest`.
