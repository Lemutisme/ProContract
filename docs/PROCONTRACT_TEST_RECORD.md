# Test record for this release

[Documentation](README.md) · [Testing](PROCONTRACT_TESTING.md)

This records one run of the tests on the source of this release, on one Linux
host. It says what passed, what was skipped and what was not run. The
integration is upstream Codex at `583ec348cf` with the ProContract changes on
top. Native binaries were built from the Rust tree of this release; the later
changes are Python, tests and documents and leave that tree unchanged.

## Python

Latest results for all **164 top-level `scripts/test*.py` modules pass**:
1455 cases including 159 skips, hence 1296 executed cases. This combines one
frozen full sweep with one targeted fixture correction, not a second all-green
sweep. Another 10 cases in `codex_package.test_layout` and
`codex_package.test_cargo` pass. This is not recursive discovery of every
upstream Python test.

The full sweep had 163 passing modules and one
failed assertion in `test_procontract_guarded_recovery`. All 362 inventoried
script files had identical hashes before and after the sweep. The remaining
assertion expected the old version-event fields; it now additionally expects
the exact `manifest_binding`. Its 9-case module rerun passes. No product code
changed after the full sweep.

The sweep used newly built store SHA-256
`cdb19040c164d7f41a3fe3a9ad30f8157b59e93a14d9ef546b35d5ad55729b3e`, isolated
temporary directories, `python3 -B -m unittest MODULE`, two concurrent modules
and a 600-second module limit. Unavailable native/Docker/model fixtures retain
their declared skips. A passing module does not qualify its skipped capabilities.
No paid model requests were made.

The first sweep after the upstream merge had 14 modules that did not pass. What
failed and how it was resolved:

| Initial failure | Resolution and boundary |
| --- | --- |
| Root could read evaluation's chmod-000 file; the capability launcher correctly refused root | Run these permission fixtures as UID 65534: 33 cases pass. Launcher root refusal is unchanged. The capability fixture now honors `RUSTUP_HOME`. |
| Learning exit no longer matched its launch after sorted JSON readback; affected RSI, learning-claim, insight and guarded recovery | New exits retain `claim_binding`; producer, recovery and PH audit validate the same preimage. Old receipts retain the exact old digest rule. Invalid old receipts remain invalid; malformed present bindings never fall back. |
| Version/harness identity changed after journal readback | New version events retain `manifest_binding`. `Host.versions()` validates and restores that input. Existing entries keep their old projection, without backfill. |
| Confirmation delivery/method receipt mismatch; H admission, composition and notices failed | Both confirmation writers retain `plan_binding`; the reader validates and restores the original plan. PH audit consumes that validated plan while retaining its separate shape checks. |
| PH audit rejected the new exit/assignment fields | It now shares strict exit-binding validation and reads the validated plan; all 11 supervisor cases pass. |
| First `study_host` run reported changed runtime inventory | A test file was edited while that run was in progress, so the run does not count. All 7 cases pass with frozen source; the seal check was not weakened. |

The fixes use existing `store.json_binding` for new records. Native hashing,
kernel transitions, global `store.digest` and historical stores are unchanged.

## Native, schemas and build

| Check | Result |
| --- | --- |
| Kernel / store / history | 41 / 49 / 44 tests pass |
| ProContract extension | 311 pass, 5 explicitly ignored |
| Exec server | 446 pass under root; the sole chmod-000 failure passes unchanged as UID 65534 |
| App-server protocol | 329 pass, 1 generator ignored |
| Affected package checks including tests | Extension, exec-server, protocol and CLI pass |
| Stable/experimental embedded schemas | Regenerated and committed; changes include extension workers and retained original-user-text fields |
| Bazel | Dependency resolution and actual label query pass; store library/binary collision fixed |

The CLI and six selected Linux helpers built successfully. Build: Rust 1.99.1, dev profile without debug info or
incremental compilation, verified V8 artifacts and a private extracted libcap
development sysroot. No system package installation was performed.

**The full Rust workspace suite has not passed.** Its first attempt stopped at
the voice SDK requirement: GStreamer >=1.28 is required, while this host has
1.24. The run excluding voice ran out of disk space during compilation, before
finishing the suite. Voice is untested.

## Client checks with a scripted local provider

These use private `CODEX_HOME` directories and scripted local responses, with
ProContract disabled. No model was called.

| Check | Result |
| --- | --- |
| `exec` flags, final message, `thread.started`, `turn.completed.usage` in the JSON event stream | Two controls pass |
| SessionStart, UserPromptSubmit and Stop hooks of an unchanged plugin | Each completes once; the session id on the hook's standard input matches the thread |
| Resume and queue on a copy of a closed session recorded by stock Codex | Both turns finish; the queued text reaches the provider; the original is unchanged |
| The same copy read again by stock Codex 0.170.0 after this fork used it | Listing, resume and queue work. A plain directory copy keeps absolute paths in its state index, so the copy must be mapped at its original path |
| Request bodies of stock Codex 0.170.0 and this fork with ProContract off | Two paired new turns differ only in fresh ids and times, private skill paths, and tool descriptions and model choices that changed upstream after 0.170.0 |

## Compatibility boundary

New native code-mode uses grpc+stdio. Legacy Python `FrozenToolSession` uses
framed JSON and fails its direct new-helper probe with `code-mode response exceeds
frame limit`. The new helper is paired only with the native CLI of the same
version. A successful CLI build does not qualify the old Python adapter.
