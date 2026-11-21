# Capture an actual initial selector exchange

This opt-in harness hook joins an actual isolated native selector worker to its
public evidence view and to the gateway's final effective request bytes. It does
not recover an executor, create an Operations parent, or establish task quality.
Existing runs and settings remain unchanged when `selector_capture` is absent.

Provision a host-owned directory outside every candidate's writable workspace,
and set the native extension's `settings.json`:

```json
{
  "selector_capture": {
    "directory": "/absolute/host/native-selector-captures",
    "max_entries": 10000,
    "max_bytes": 67108864
  }
}
```

These numbers are explicit export allocations, not universal research policy.
The existing worker model and reasoning effort must be pinned. The exporter
reads the exact `EvidenceAccess` object installed into the selector. Its native
boundary is checked before and after export and again before first generation.
The snapshot and native manifest are content-addressed immutable files. The
manifest includes the actual prompt, actual base instructions (including their
existing resource text), response schema, configured model/effort, archive hash,
and the real native worker thread ID. It captures the initial exchange only.

The isolated worker registers a `ModelRequestContributor`, which adds three
tracking fields to `client_metadata` after the awaited boundary check:

| Field | Meaning |
| --- | --- |
| `procontract_capture` | SHA256 of the native manifest's exact bytes |
| `procontract_capture_thread` | Actual native worker thread ID |
| `procontract_capture_request` | Transport preparation ordinal, starting at `"0"` |

The counter belongs to this native worker. A retry or later tool-loop request
gets a later ordinal; it cannot become a second initial sample. An opted-in
capture failure blocks that selector request and leaves an error where possible.
The hook neither adds model input instructions nor changes the chosen strategy.

The provisioner must install the matching gateway hook in a **new** run's gateway,
after resource reservation/injection and before opening an upstream connection:

```python
request, deadline, forwarded = budget.reserve_with_context(raw, prepared, changes)
binding = bind_request(native_capture_root, gateway_capture_root, raw, forwarded,
                       gateway_request_id=str(request))
# Only then connect and send exactly `forwarded`.
```

Import `bind_request` from `scripts/procontract_selector_capture.py`. Both roots
are host configuration, never a model-selected path. The gateway retains exact
original/effective byte strings, including the actual tool definitions, response
format and injected allocator snapshot. It checks native worker identity,
prompt/instructions/model/effort/schema, preservation of the tool/settings fields,
and the initial input plus allocator projection. Truncated or changed initial
prompts, old reasoning/tool histories, and unretained provider-state dependencies
are refused. Noncapture requests return `None`; they are not silently certified.
Current Core places base instructions in its dedicated first developer item,
after an optional Responses-Lite `additional_tools` prefix; older requests may
use the top-level `instructions` field. The binding records the exact accepted
location and checks those bytes. It does not search later context for a matching
string. Structured field comparisons use canonical JSON bytes, so `true` and `1`
are different even when nested in a schema or input object.

`<manifest>.<ordinal>.binding.json` is write-once. Exact re-reading is idempotent;
another request cannot overwrite an initial binding. Later ordinals are explicitly
`initial=false`. A binding is an observation with `admissible=false`, and its
closure is `not_observed_here`: capture is not a provider completion receipt.

The native worker separately records `native-selector-closure/v1` after its real
shutdown path. Cancellation cleanup records confirmed or unconfirmed shutdown;
process loss can leave no closure. These observations must be joined to actual
host assignment/allocation/outcome records before any Operations fork receipt is
issued. A different execution ID alone is not restoration or independence.

The host owns the capture directory and controls ledger writes. Hash checks do
not defeat a hostile host replacing files between check and use. The initial
boundary is a pre-generation view; later original-selector evidence accesses may
legitimately extend its own journal. Those later requests are suffixes, not new
captures of the old boundary. The paired gateway hook is required: a native
manifest alone is not the effective payload, and a gateway record alone is not
the native evidence snapshot.

The native integration control uses the actual `run_turn`, isolated extension
registry and request contributor, a fresh native ledger fixture, a local budget
gateway that binds before connect, and a fake SSE provider. It proves this route's
joins/closure without paid model calls. It does not prove a real-model exchange,
two actual continuations, complete asynchronous tool parity, or a quality gain.
