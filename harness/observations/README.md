# Frozen native history candidate

`native_history.capture_history` retains selected host-owned native
`CommandExecution` event lines as exact JSONL bytes, with an index of native
identity, original coordinates, retained chunk ranges, limits and omissions.
`capture_history.py` composes these files into an existing capture provider's
`projection.observations`; native `read_evidence` supplies catalog IDs and byte
pages. No new kernel, tool API or task strategy is introduced.

The provisioner supplies the session root, base provider command and explicit
per-log, per-file and total storage bounds. A minimum readable unavailable-history
record is checked before the CLI starts the base provider. If otherwise valid
storage pressure prevents indexing the history, the artifact capture survives
with a readable `state: unavailable` record. No unindexed raw chunks are published.
Diagnostics must remain outside the captured projection and source session root.

Cutoffs name the last matching native event per source. Partial/invalid lines
before a later matching event affect that interval's coverage. Trailing partial
telemetry is preserved in collection diagnostics, so it does not change an
unchanged command-history subject. A moving bounded source window still changes
the represented scope. The records never certify all OS/network activity or the
absence of a prohibited action.

Example future provider wiring (each argument is host-selected):

```text
python3 harness/observations/capture_history.py
  --sessions <attempt-owned CODEX_HOME>/sessions
  --diagnostics <host-outside-projection>/history-scan.json
  --max-bytes-per-log 8388608 --max-file-bytes 524288 --max-total-bytes 33554432
  -- python3 <existing-provider> --config <existing-host-config>
```

Status: nine Python mechanism/boundary controls pass. The opt-in production
`native_history_fixture_is_available_through_real_paged_reader` control reconstructs
all 164 retained Coq event lines through 108 evidence reads, including the 144
absent from the prior tail. Chunk and original-line digests match; metadata cannot
attest execution. The two initial fixture errors (stale catalog, then omitted
request/terms reads) are retained in external qualification records. Full native
provider deployment and paid task validation remain unperformed; no frozen cohort
uses this candidate.

Run local controls with `python3 -m unittest discover -s harness/observations/tests -v`.

`profile.with_history` builds a copied configuration around an existing provider,
preserving its source CID and timeout. The provisioner must already have a fresh
projection provider and create the diagnostics parent directory outside the
projection and session tree. This helper does not modify a running task or choose
when history should be read. A direct workspace-capture adapter without a provider
is deliberately rejected: changing its file materialization is a different
capability change and cannot be hidden inside a history-only comparison.
