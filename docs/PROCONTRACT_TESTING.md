# ProContract testing

[Documentation](README.md) · [Quickstart](PROCONTRACT_QUICKSTART.md) ·
[Code map](PROCONTRACT_CODE_MAP.md) · [Release guide](PROCONTRACT_RELEASE.md)

The repository has four kinds of test: Rust tests of the kernel, store and
native extension; Python tests of the harness that need at most the store
binary; native end-to-end fixtures that need a built package and Docker; and
qualification scripts that call a real model. Run the ones that cover the code
you changed.

| Tests | What they need |
| --- | --- |
| [Kernel and store (Rust)](#kernel-and-store) | The Rust toolchain. |
| [Native extension (Rust)](#native-extension) | The Rust toolchain. Some tests also use a local Docker image when one is named. |
| [Credit, fork receipts and store bridge (Python)](#credit-and-store-bridge) | The `pro-contract-store` binary. |
| [Operations, research runtime and launcher (Python)](#harness-and-rsi) | The `pro-contract-store` binary. The launcher and installer tests need nothing. |
| [Decision-level tools (Python)](#decision-level-tools) | Nothing, the store binary, or a fixture you supply, depending on the module. |
| [Captured native history (Python)](#captured-native-history) | Nothing. |
| [Native end-to-end fixtures (Python)](#native-end-to-end-fixtures) | A built package, Docker and a local image. No model key. |
| [Real-model qualification scripts](#real-model-qualification) | A built package, Docker, a local image and a model provider with your key. |

No test in this list downloads benchmark data. The sections below say, for each
command, whether its tests skip or fail when something they need is missing.

## The store binary

The Python tests call the store through the `pro-contract-store` binary and
find it through `PRO_CONTRACT_STORE_BIN`. Build it once, from the repository
root:

```sh
(cd codex-rs && cargo build --locked -p codex-pro-contract-store --bin pro-contract-store)
export PRO_CONTRACT_STORE_BIN="$PWD/codex-rs/target/debug/pro-contract-store"
```

The path above is Cargo's default target directory; use the real path if you
build elsewhere. A package built with the [release guide](PROCONTRACT_RELEASE.md)
also contains this binary, as `package/bin/pro-contract-store`.

## Kernel and store

```sh
(cd codex-rs && cargo test --locked -p codex-pro-contract -p codex-pro-contract-store)
```

Needs the Rust toolchain named in `codex-rs/rust-toolchain.toml` and nothing
else. These tests cover the transition rules (roles, versions, coordinates and
targets), the reference model, persistence, replay, capture and atomic
adoption.

## Native extension

```sh
(cd codex-rs && cargo test --locked -p codex-pro-contract-extension)
```

Needs the Rust toolchain. Tests that run a container do so only when
`PRO_CONTRACT_TEST_IMAGE` names a local Docker image; without it they return
without checking anything. A few tests are marked `#[ignore]` and name the
fixture they need in the attribute.

## Credit and store bridge

```sh
cd scripts
python3 -m unittest \
  test_procontract_store test_procontract_fork \
  test_procontract_credit test_procontract_credit_receipts
```

Needs `PRO_CONTRACT_STORE_BIN`. No model, grader or container runs: evaluator
and continuation results are scripted. Without the binary the credit tests that
use it are skipped, and the store and fork tests fail.

`test_procontract_migration` also needs `PRO_CONTRACT_LEGACY_BIN`, a store
binary built from a revision older than the store's acceptance records. Its
tests are skipped without one.

The [quickstart script](../scripts/examples/procontract_quickstart.py) uses the
same binary and the same modules and is a quick end-to-end check of them.

## Harness and RSI

```sh
cd scripts
python3 -m unittest \
  test_procontract_operations test_procontract_evidence \
  test_procontract_research_runtime test_procontract_research_control \
  test_procontract_operations_adapter
python3 -m unittest test_install_procodex test_procodex test_procodex_cli_research
```

The first command needs `PRO_CONTRACT_STORE_BIN`; without the binary its tests
fail instead of skipping. The second command needs nothing. Both use local,
scripted inputs: no model call, and Docker is not started.

The first group exercises execution custody, retained evidence, owner-context
generations, selected work and its returns, and version delivery. The second
covers the launcher's configuration and the installer.

## Decision-level tools

```sh
cd scripts
python3 -m unittest \
  test_procontract_policy_splice test_procontract_replay_transport \
  test_procontract_frozen_evidence test_procontract_selector_capture \
  test_procontract_decision_study_spec
python3 -m unittest test_procontract_decision_study
python3 -m unittest \
  test_procontract_decision_replay \
  test_procontract_selector_tools test_procontract_selector_exchange
```

- The first command needs nothing.
- `test_procontract_decision_study` needs `PRO_CONTRACT_STORE_BIN`. Its tests
  that send a request also need `REPLAY_PROVIDER_FIXTURE` and are skipped
  without it.
- `test_procontract_decision_replay` needs `REPLAY_PROVIDER_FIXTURE`: the path
  of a Python module that starts a loopback stand-in for the model provider.
  This repository does not include one, so these tests are skipped unless you
  write it.
- `test_procontract_selector_tools` and `test_procontract_selector_exchange`
  need `SELECTOR_CODE_MODE_HOST`, the path of a built `codex-code-mode-host`
  binary, and `SELECTOR_CODE_MODE_HOST_SHA256`, its SHA-256. They are skipped
  otherwise.

None of these calls a real model.

## Captured native history

From the repository root:

```sh
python3 -m unittest discover -s harness/observations/tests -v
```

Needs nothing.

## Native end-to-end fixtures

These run the real native binary with Docker tools, review, progress
publication, interruption and recovery against a scripted provider on
localhost. They need a built package (or an installed release) and a local
Docker image, and no model key. From the repository root:

```sh
export PROCODEX_NATIVE_PACKAGE=/absolute/path/to/package-or-installed-release
export PROCODEX_TEST_IMAGE=sha256:YOUR_LOCAL_IMAGE_ID
cd scripts
python3 -m unittest \
  test_procodex_native.NativeEvidenceTest.test_real_executor_and_reviewer_use_frozen_handles \
  test_procontract_continuous_native.ContinuousNativeTests.test_open_ended_successor_can_publish_under_the_same_responsibility \
  test_procontract_continuous_native.ContinuousNativeTests.test_open_ended_owner_interrupts_active_execution_and_resumes_without_a_new_grant \
  test_procontract_continuous_native.ContinuousNativeTests.test_readonly_inputs_and_strategy_survive_a_successor \
  test_procontract_continuous_native.ContinuousNativeTests.test_review_interruption_resumes_the_original_responsibility_after_restart
```

Both variables must be set; otherwise the native tests are skipped. The
package comes from the [release guide](PROCONTRACT_RELEASE.md), and the image
can be the one built there from `docker/procodex`. Set `PROCODEX_TEST_REPORT`
to a directory to keep a fixture's evidence; use a different directory for
each fixture.

These fixtures show that the mechanics work. They say nothing about the
quality of a real model's work on a task. The other `test_*_native.py` modules
in `scripts/` are gated by `PROCODEX_NATIVE_PACKAGE` and, most of them, by
`PROCODEX_TEST_IMAGE` as well.

## Real-model qualification

[qualify_procontract_operations.py](../scripts/qualify_procontract_operations.py)
and [qualify_procontract_insights.py](../scripts/qualify_procontract_insights.py)
run selected research flows with a real model. Each takes `--root`, `--package`
and `--image`. The second also needs an evidence bundle (`--bundle`) and a
result file kept from a completed run, neither of which is in this repository.
Both spend model requests on your account. Read each script's arguments and
set up the provider, package, image and resources before running it.

Benchmark runs are not part of the test suite. The benchmark adapters in
`scripts/` need benchmark tasks and tests that this repository does not
contain.

## What a result means

A passing mechanism test, a delivered strategy, an adoption decision and a
measured gain in task quality are four different results. Report the one you
have.

For a package you built, `SHA256SUMS` covers the archive and
`package/build-source.json` lists the source commit and a SHA-256 for every
packaged file; the installer checks both the commit and the files before it
installs.

## Recorded run

[PROCONTRACT_TEST_RECORD.md](PROCONTRACT_TEST_RECORD.md) records one run of the Python test modules on this release, including the modules that do not pass.
