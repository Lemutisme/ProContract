# One retained decision request through Operations

`scripts/procontract_decision_replay.py` is an Operations measurement runner for
one retained Responses API request. It reads the exact payload bytes, checks their
SHA-256, sends one POST, and retains raw observations. It does not resume the old
allocation, run tools, retry, measure task quality, or adopt a version.

This is a request-level diagnostic. It is not a solver checkpoint/restorer. Files,
processes, kernel state, evidence, exposure and tool state have not been restored.
The owner records omissions and source qualification; the helper does not prove
historical closure, permission, model deployment identity or independent samples.

## Provisioning and the actual entrypoint

Provision a fresh protected diagnostic host. Do not expose provisioning, its
profile, credentials or filesystem to a candidate author. The existing Operations
authority and OS custody remain the trust boundary; this Python helper is not a
new sandbox. The example below is the production path used by the integration
tests. It uses no native store transitions and disables adoption.

```python
import os
import sys
from pathlib import Path
import procontract_operations as ops
import procontract_decision_replay as replay

# Owner-provided paths/values. endpoint is a complete /v1/responses URL.
# Use the owner's loopback fake provider until this exact runner is qualified.
root = Path("/protected/new-replay-host").resolve()
profile_path = root.parent / "replay-profile.json"
payload_path = Path("/protected/retained-effective-payload.json")
endpoint = "http://127.0.0.1:PORT/v1/responses"

point = replay.checkpoint(
    payload_path, parent_state_id="canonical-old-state-id",
    analysis_unit={"id": "old-task-id", "family": "programbench"},
    source_mode="effective_payload",
    source_receipts=["owner-source-qualification-reference"],
    omitted={"solver": "single API request; no full solver state restored"},
)
ops.atomic(profile_path, replay.profile(
    root, endpoint, [point], auth_env="REPLAY_PROVIDER_TOKEN",
    model="gpt-6-luna", effort="max",
))
os.chmod(profile_path, 0o600)
authority = ops.Authority(
    "experiment-owner", "owner-admission-reference", "decision-AA-only",
    (), ("replay",), allow_adoption=False, operation_limit=16,
)
host = ops.Operations.create(root, authority, {
    "replay": ops.Measurement(
        (sys.executable, "-B", str(Path(replay.__file__).resolve()),
         "--profile", str(profile_path)),
        replay.implementation(), "owner-qualified-provider",
        external_effects=True,
    ),
})
host.subscribe("replay-pilot", "task", ["operation_outcome"])
version = host.register({
    "kind": "harness", "files": {},
    "build": {"implementation": replay.implementation()},
    "model": {"name": "gpt-6-luna", "effort": "max"},
    "resolver": "retained-request/v1", "tools": {},
})
# Export the credential only in the protected worker environment, never the body.
# Choose a new globally unique operation ID for every admitted request.
host.submit("new-unique-request-id", "replay-pilot", version, "replay",
            {"checkpoint": point["id"]}, "admitted A/A request", seconds=None)
host.dispatch("new-unique-request-id")
# Later: host.reconcile("new-unique-request-id")
```

`seconds=None` does not invent a global time limit. A declared finite new request
allocation may be supplied by the owner. The original deadline and resource
snapshot remain unchanged inside the payload and confer no new authority.
The owner limits admission/concurrency; this helper does not launch a cohort.

Operations invokes:

```
python3 -B scripts/procontract_decision_replay.py --profile PROFILE ASSIGNMENT_JSON
```

The assignment must be the running operation's retained `assignment.json`, at its
expected path. Actor input contains only a checkpoint ID registered in the pinned
profile. Provider, credential source, source mode and payload path cannot be
overridden in that operation input. Hosted provider tools and unretained
`previous_response_id`/conversation/background state are rejected. Function/custom
tool definitions remain byte-identical; returned calls are retained but not run.

## Evidence, identity and closure

The new execution ID is the operation ID. The allocation ID is the SHA of the
original Operations assignment, including admitted time, deadline and authority.
Both are HTTP headers; neither changes the body. Authentication is a separate
header sourced from the profile's environment variable. Credentials are not put
in the retained request metadata. Only HTTPS or loopback HTTP is accepted;
environment proxies, redirects and automatic retries are not used.

Before sending, the runner stores exact request bytes and the checkpoint
descriptor as immutable Operations objects. It fsyncs an exclusive dispatch intent
before the transport call. A preflight rejection writes a closed observation with
zero reserved HTTP attempts. An existing intent without result is uncertain and
spent; neither it nor duplicate Operations delivery authorizes another POST.
A per-operation lock also prevents two runner invocations from racing the intent.

Each result contains:

- `payload` and `checkpoint_object`: immutable host object IDs.
- `trace`: an immutable object binding the assignment SHA, full transport report
  and every raw transport file as base64 bytes with SHA and length.
- `observation`: a content-addressed object binding canonical `parent_state_id`
  to the complete parsed decision, refusal, tool call or unstructured output.
- `transport`, `terminal`, `usage`, `counts`: observed protocol and accounting
  facts. Unknown usage remains null; no price estimate becomes a paid-cost fact.
- `admissible=false`, `validity=observation_only`: this is never a quality or
  adoption certificate. `structured_json` describes syntax, not a legal action.

The host's `operation_outcome` binds the result to the real assignment and records
local process exit. Transport closure distinguishes local socket/watchdog closure
from a provider terminal response. A disconnect/deadline without a provider
terminal leaves external effects unresolved. No-connection failures can establish
that the provider was not reached. If the Operations worker is killed at its hard
deadline before the runner writes a result, available raw files stay retained but
remote closure remains unknown; the owner must reconcile, never resend that ID.

Identical complete decisions from the same parent can share an observation ID;
their assignments, traces, usage and outcomes remain distinct. A different parent
cannot reuse that observation identity. Action agreement and decision agreement
are separate measurements. This runner does not attach `operations-fork/v1`
receipts or assert a restored task, independent noise samples, Support or adoption.

## Zero-model qualification

The owner supplies `fake_provider.py`; tests call the actual registered Operations
runner subprocess. There is no alternative client standing in for the sender.

```sh
REPLAY_PROVIDER_FIXTURE=/path/to/fake_provider.py \
  python3 -B -m unittest discover -s scripts -p test_procontract_decision_replay.py -v
```

Set `REPLAY_TEST_ARTIFACTS` to a new directory to retain all test hosts, assignments,
raw response files and actual received payloads. Otherwise fixtures are temporary.
Transport-only controls are in `test_procontract_replay_transport.py`. Loopback HTTP
passing does not qualify TLS; actual provider admission follows the experiment
owner's separate wire/TLS qualification, source selection and request allowance.
