# Import a retained native selector source

`scripts/procontract_selector_native_source.py` verifies a closed native source
bundle and makes it readable by an Operations host. The importer is a new
verification operation; it did not produce the original task state. Its ID is
retained only as `imported_by`. Original native root execution, selector worker,
turn, responsibility and gateway allocation keep separate identities.

The host first freezes a descriptor, then a profile containing named descriptor
references. Models select only a name in that host registry:

```python
descriptor = describe(state_root, capture_sha256,
                      collection="procontract-selector-collection-20261119-v2",
                      owned_effects=collection_root / "cases/coq-block-bound/OWNED_EFFECTS.json")
# Write descriptor once, retain its bytes/hash, then:
config = profile(new_host_root, {"first_initial": reference(descriptor_path)})
```

`describe` is a host provisioning helper. It reads files, never opens a source
database. Its descriptor has schema `native-selector-source-descriptor/v1`,
`collection`, `state_root`, `capture`, `files`, and `requests`. Each file reference
is an absolute `path` and exact `sha256`; an absent WAL/SHM is explicitly
`{path, absent: true}`. The required roles are manifest, archive, native_closure,
session, allocation, run, final_closure, owned_effects, budget, budget_wal and
budget_shm. Each request enumerates its integer request_id/ordinal plus binding,
original/effective payloads, original/forwarded gzip audits and identity headers.
The original native closure's issued request count must match all ordinals
`0..N-1`, with exactly one initial point. A suffix remains a suffix.

The admitted runner command is:

```text
python3 -B scripts/procontract_selector_native_source.py --profile PROFILE ASSIGNMENT
```

Register `implementation()` as its measurement identity; the existing Operations
host pins the profile and runner command files. The only input is
`{"source":"first_initial"}`. `run(profile_path, assignment_path)` requires the
actual retained running assignment, verifies its source registry, and writes:

```json
{"schema":"native-selector-source-import/v1","source":"OBJECT_ID","effects_resolved":true,"admissible":false}
```

Before opening SQLite, the importer reads and hashes DB and WAL bytes, together
with source stat observations. It opens a new private temporary copy, includes
the WAL, and queries the actual budget/request/context/response-identity tables.
The original SHM is observed for custody stability and never opened or copied
into SQLite. All source hashes/stats must stay unchanged through verification.
The immutable proof retains the descriptor, all source bytes, and the custody
observations. It does not accept supplied query-row JSON in place of the DB.

Validation ties native manifest/archive hashes and boundary to actual initial
payload, prompt, base instructions, response schema, model/effort, tool fields,
request tracking metadata, allocation projection, independent gzip audits,
header thread identity, native session start/return, native closure and final
source-run closure. Since checkpoint.execution names the original root, v1 also
requires **every reserved request of that root** to have a finished HTTP 200
`response.completed` observation with no failure. A selected worker's completion
cannot close an unrelated unknown request. This is the supported source regime
of this importer, not a new kernel rule about successful work.

`resolve(host, source_id)` re-reads the immutable proof and the actual closed
import assignment/outcome. It reopens DB/WAL from retained bytes in another
private temporary directory and re-derives the facts. It never consults the
original paths on readback. It returns record, original namespaced execution and
allocation, closure, payload bytes, evidence body, boundary, original and
canonical evidence digests, origin, imported_by, and parent_measurement (the
actual import capability's name). The source's collection namespace is host
configuration, not a caller-supplied task alias. The statistical record remains
the original native responsibility.

`capture(host, source_id, target)` writes an exclusive canonical evidence copy
and returns a `selector_evidence_capture` object. Its schema remains
`selector-evidence-capture/v1`, but it has
`origin={kind:"native_selector_source",object:ID}` and no legacy `parent`,
`parent_assignment` or `parent_outcome`. `resolve_capture` rejects mixing those
two origin types and verifies every field against the source proof.

The archive originally emitted by Rust has its own raw-byte SHA. The imported
canonical JSON plus newline has a different SHA. `source_archive_sha256` and
`evidence_file_sha256` retain both; decoded equality is checked rather than
mislabeling reserialized bytes as original bytes.

Remote completion is explicitly **host gateway terminal plus native return and
closure**. These original bundles contain no raw response SSE or provider-signed
shutdown receipt. The importer neither manufactures them nor upgrades the
original gateway parser's evidence. A new continuation must use its own actual
Operations assignment, allocation and closure. The old resource snapshot remains
captured context and does not renew or authorize a grant. This capability alone
does not restore an executor, measure task quality, establish independent tasks,
hide exposure, or adopt a candidate.

Tests use synthetic source bundles, real SQLite/WAL custody and real Operations
import subprocesses. They cover unavailable original paths on readback,
uncheckpointed WAL, unknown source names, bad terminals, missing request
ordinals, wrong worker headers, altered original allocation, source mutations,
unclosed imports, mixed origins and forged derived claims. Real-data composition
and any model continuations are separately owned qualifications.

## Provisioning example

This creates the host and verifies the source; it does **not** send a continuation.
The provisioner supplies the endpoint, helper, original source descriptor and
authorization. `target` is a new private **file**, not a directory or object ID;
it is written exclusively and made read-only. Preserve an existing target on
re-entry and use the retained capture ID rather than overwriting it.

```python
import sys
from pathlib import Path
import procontract_operations as ops
import procontract_selector_native_source as native
import procontract_selector_exchange as exchange
import procontract_selector_receipts as receipts

root = Path("/new/owned/host-root")
parent_profile = root / "import-profile.json"
exchange_profile = root / "exchange-profile.json"
ops.atomic(parent_profile, native.profile(root / "host", {
    "first_initial": native.reference(descriptor_path)
}))
config = exchange.profile(root / "host", endpoint, code_mode_host, code_mode_sha256,
                          parent_measurement="import_native", max_requests=8,
                          max_reads=32, model="gpt-6-luna", effort="max")
config["reliability_measure"] = receipts.registration(config)
ops.atomic(exchange_profile, config)
host = ops.Operations.create(root / "host", ops.Authority(
    principal, authorization_source, "selected selector exchange A/A", (),
    ("import_native", "exchange", "measure"), allow_adoption=False,
    operation_limit=5), {
    "import_native": ops.Measurement(
        (sys.executable, "-B", native.__file__, "--profile", str(parent_profile)),
        native.implementation(), "local native-source verification"),
    "exchange": ops.Measurement(
        (sys.executable, "-B", exchange.__file__, "--profile", str(exchange_profile)),
        exchange.implementation(), "admitted model route and frozen native reader",
        max_seconds=600, external_effects=True),
    "measure": ops.Measurement(
        (sys.executable, "-B", receipts.__file__, "--profile", str(exchange_profile)),
        receipts.implementation(), "operational validity projection")
})
host.subscribe("selector-real-aa", "task", ["operation_outcome"])
version = host.register(exchange.version_definition(config))
host.submit("import-native", "selector-real-aa", version, "import_native",
            {"source": "first_initial"}, "verify original source", seconds=60)
host.run_worker("import-native")
done = host.inspect("import-native")
assert done["outcome"]["shutdown_confirmed"] is True
source_id = done["outcome"]["measurement"]["source"]
capture_id = native.capture(host, source_id, root / "captures/d660-evidence.json")
reader = receipts.ReceiptReader(host)
checkpoint_id = reader.checkpoint(capture_id)
# Retain source_id, capture_id, checkpoint_id and version in the provisioner's
# READY record. Shared-slot admission occurs before submitting either child.
```

After actual admission each side submits a distinct ID with inputs
`{"capture": capture_id}`, measurement `exchange`, the same version, and its
own `seconds=600`; the source's old grant is not consumed or renewed. Each
optional measurement then submits `{"source": CHILD_OPERATION_ID}` to `measure`.
The source import plus two exchanges plus two projections use five operations.
Checkpoint/continuation receipts and `fork.pair` do not require those projections.
`fork.admit` is not implied by pairing: its unshared component list remains for
the actual improvement terms to assess.
