# Test record for this release

[Documentation](README.md) · [Testing](PROCONTRACT_TESTING.md)

This records one run of the tests on the source of this release, on one Linux
host: what passed, what was skipped and what was not run. The source is
upstream Codex at `583ec348cf` with the ProContract changes on top. Native
binaries were built from the Rust tree of this release; later changes are
Python, tests and documents and leave that tree unchanged. No model was called.

## Python

All **164 top-level `scripts/test*.py` modules pass**: 1,455 cases, of which
159 are skipped and 1,296 run. Another 10 cases in `codex_package.test_layout`
and `codex_package.test_cargo` pass. This is not a recursive discovery of every
upstream Python test.

The result combines one full sweep with one corrected test. In the sweep, 163
modules passed and one assertion in `test_procontract_guarded_recovery` failed:
it expected the version-event fields of an earlier store format. The test now
also expects the `manifest_binding` field that new version events carry, and its
9 cases pass. No product code changed after the sweep, and the 362 inventoried
script files had the same hashes before and after it.

Conditions of the sweep: the store binary built from this tree (SHA-256
`cdb19040c164d7f41a3fe3a9ad30f8157b59e93a14d9ef546b35d5ad55729b3e`), an
isolated temporary directory per module, `python3 -B -m unittest MODULE`, two
modules at a time and a 600-second limit per module. Fixtures that need a
native package, Docker or a model keep their declared skips; a passing module
does not qualify the capabilities it skipped. Permission fixtures run as an
unprivileged user (UID 65534): as root, a file with mode 000 stays readable and
the capability launcher refuses to start.

`test_procodex_standalone` was added after the sweep, with the side-by-side
installer it tests. When this record was refreshed it passed its 5 cases, the
store and credit modules (`test_procontract_store`, `test_procontract_fork`,
`test_procontract_credit`, `test_procontract_credit_receipts`) passed their 124
cases again with the same store binary, and the
[quickstart](PROCONTRACT_QUICKSTART.md) printed exactly the output its document
shows.

## Native code, schemas and build

| Check | Result |
| --- | --- |
| Kernel / store / retained history | 41 / 49 / 44 tests pass |
| ProContract extension | 311 pass, 5 explicitly ignored |
| Exec server | 446 pass; the one test of a mode-000 file passes as UID 65534 |
| App-server protocol | 329 pass, 1 generator ignored |
| Checks of the affected packages, including tests | Extension, exec server, protocol and CLI pass |
| Stable and experimental embedded schemas | Regenerated and committed |
| Bazel | Dependency resolution and label query pass |

The CLI and six Linux helpers were built with Rust 1.99.1 in the dev profile,
without debug information or incremental compilation.

**The full Rust workspace suite has not been run to completion.** The voice
crate needs GStreamer 1.28 or later, which the host did not have, and the run
without voice did not finish. Voice is untested.

## The client with a scripted local provider

These checks use separate `CODEX_HOME` directories, scripted local responses
and ProContract disabled.

| Check | Result |
| --- | --- |
| `exec` flags, final message, `thread.started` and `turn.completed.usage` in the JSON event stream | Two controls pass |
| SessionStart, UserPromptSubmit and Stop hooks of an unchanged plugin | Each runs once; the session id the hook receives matches the thread |
| Resume and queue on a copy of a closed session recorded by stock Codex | Both turns finish, the queued text reaches the provider, and the original is unchanged |
| The same copy read again by stock Codex 0.170.0 after this fork used it | Listing, resume and queue work. A copied directory keeps absolute paths in its state index, so the copy must be placed at its original path |
| Request bodies of stock Codex 0.170.0 and this fork with ProContract off | Two paired new turns differ only in fresh ids and times, skill paths, and the tool descriptions and model choices that changed upstream after 0.170.0 |

## Compatibility

Native code mode now talks to its helper over gRPC on standard I/O. The older
Python `FrozenToolSession` uses framed JSON and cannot drive the new helper
(its probe fails with `code-mode response exceeds frame limit`), so the helper
is paired only with the native CLI of the same version.
