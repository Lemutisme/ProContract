# ProContract quickstart

[Documentation](README.md) · [Release guide](PROCONTRACT_RELEASE.md) ·
[Testing](PROCONTRACT_TESTING.md) · [Code map](PROCONTRACT_CODE_MAP.md)

This quickstart runs the kernel and the credit module on your machine in a few
seconds. It needs one binary built from this repository and Python 3.11 or
later. It uses no model, no Docker and, after the build, no network.

The script is [scripts/examples/procontract_quickstart.py](../scripts/examples/procontract_quickstart.py).
It works in a temporary directory and shows two things:

- **(a) One task under the kernel.** A Contract is issued, a worker proposes a
  candidate, a verifier records a Defeat with a counterexample, the worker
  proposes a repaired candidate, and the verifier records Support. The
  Contract's state is printed after each step. The worker saying "done"
  changes nothing.
- **(b) One self-improvement step.** A candidate change to a harness version is
  recorded as a Contract whose terms are fixed before the candidate exists. It
  gets a measured effect on six units, is judged against the terms, and is
  adopted. The numbers in this part are synthetic: the script generates them.

## 1. Build the store binary

The store is the Cargo package `codex-pro-contract-store` in
`codex-rs/pro-contract-store`; its binary is `pro-contract-store`. Install the
Rust toolchain named in `codex-rs/rust-toolchain.toml`, then, from the
repository root:

```sh
(cd codex-rs && cargo build --locked -p codex-pro-contract-store --bin pro-contract-store)
```

Cargo downloads the crate dependencies on the first build. With Cargo's default
target directory the binary is `codex-rs/target/debug/pro-contract-store`.

## 2. Run the script

From the repository root:

```sh
export PRO_CONTRACT_STORE_BIN="$PWD/codex-rs/target/debug/pro-contract-store"
python3 scripts/examples/procontract_quickstart.py
```

The script reads the binary's path from `PRO_CONTRACT_STORE_BIN` and stops with
a message if the variable is unset or does not name an executable file. If you
build into another target directory, use that path.

Options:

- `--keep` keeps the temporary directory and prints its path, so you can
  inspect the store with the same binary:
  `pro-contract-store contract --store <directory>/store --id task.median`
  prints the Contract of part (a) as JSON, `events` prints the journal of
  part (b), and `verify` checks its hash chain.
- `--seed N` changes the synthetic numbers of part (b).
- `--no-effect` makes the synthetic candidate no better than the incumbent. The
  same terms then give a Defeat and nothing is adopted.

## 3. What it prints

Output of the default run, with three of the six unit rows left out:

```text
ProContract quickstart: no model, no container, no network

(a) One task under the kernel
    task: Write median(values) in median.py: the median of a non-empty list of numbers.
  1. the user issues a Contract
     standing=outstanding version=1 generation=0 candidate=none support=none
  2. the worker proposes a candidate (its captured workspace)
     standing=outstanding version=2 generation=1 candidate=f61e3f3af9 support=none
  3. the worker reports "done"
     the report is text: no kernel command carries it, and a worker's only command is Propose
     the worker sends Support of its own candidate: Rejected(WrongRole { command: Support, required: Verifier, actual: Executor })
     the worker sends Discharge of the Contract: Rejected(WrongRole { command: Discharge, required: Settler, actual: Executor })
     standing=outstanding version=2 generation=1 candidate=f61e3f3af9 support=none   (unchanged)
  4. the verifier checks the frozen candidate and records a Defeat
     counterexample: median([1, 2, 3, 4]) returned 3, expected 2.5
     standing=outstanding version=3 generation=2 candidate=none support=none
  5. the worker proposes a repaired candidate
     standing=outstanding version=4 generation=3 candidate=3cc22711b1 support=none
  6. the verifier checks the frozen candidate and records Support
     checks passed: 4 of 4
     standing=outstanding version=5 generation=3 candidate=3cc22711b1 support=reach within
     still outstanding: Support is the verifier's assessment of this exact candidate;
     closing the Contract is a separate command, Discharge, that neither worker nor verifier can send

(b) One self-improvement step, as a Contract in the same store
  1. the principal installs version v0 for the scope
     incumbent: v0
  2. a defect is located and its terms are issued, before any candidate exists
     criterion: at least 6 units, mean effect >= 0.1, interval above 0, cost ratio <= 1.5
     convention: adopt when the criterion is met on cases beyond the improver's reach
     Contract improvement.5a9664976f31...: standing=outstanding version=1 generation=0 candidate=none support=none
     terms that name a version are refused: terms are written before candidates; they name no version
  3. the improver proposes candidate v1: one change to the incumbent v0
     standing=outstanding version=2 generation=1 candidate=4c590eea08 support=none
  4. measured effect on 6 units (SYNTHETIC numbers generated by this script, seed 0)
     unit     v0    v1    difference
     unit-1   0.50  0.75  +0.25
     unit-2   0.50  1.00  +0.50
     ...
     unit-6   0.25  0.75  +0.50
  5. the judgment is computed from the recorded states and measurements
     effect: mean +0.333, 95% interval [+0.167, +0.458], 6 units, 5 better, 0 worse, cost ratio 1.20
     unmet criteria: none
     reach: beyond (the improver has not read these cases or drawn a verdict from them)
     standing=outstanding version=3 generation=1 candidate=4c590eea08 support=reach beyond
  6. adoption: the Contract's Discharge and the new incumbent, in one store transaction
     decision: presumed under the convention in the terms (no explicit act by the principal)
     standing=discharged version=4 generation=1 candidate=4c590eea08 support=reach beyond settlement=presumed
     incumbent: v1, adopted by Contract improvement.5a9664976f31...
     improvements still owed: 0
     history: candidate v1 by improver, 1 judgment, adopted at journal entry 38

The store's journal verifies as one hash chain: 18 capture, 1 exposure, 1 insight, 2 issue, 12 label, 2 selection, 2 version
  task.median               standing=outstanding version=5 generation=3 candidate=3cc22711b1 support=reach within
  improvement.5a9664976f31  standing=discharged version=4 generation=1 candidate=4c590eea08 support=reach beyond settlement=presumed
```

## How to read part (a)

Each line of state is what the store returns for the Contract: its standing,
its version (one more after every accepted command), its generation, the
current candidate and whether that candidate has Support.

- Every command reaches the kernel with a role. The worker acts as the
  executor, whose only command is `Propose`; `Support` and `Defeat` belong to
  the verifier, `Discharge` to the settler.
  The rule is in [command.rs](../codex-rs/pro-contract/src/command.rs) and
  [transition.rs](../codex-rs/pro-contract/src/transition.rs).
- In step 3 the worker's report is only text. When the worker sends `Support`
  or `Discharge` itself, the kernel rejects the command and the Contract keeps
  the same version.
- The candidate is a captured copy of the worker's files, named by its hash. In
  steps 4 and 6 the verifier runs its checks on a copy the store writes out
  from that capture, not on the worker's directory.
- A Defeat removes the candidate and raises the generation, so a judgment of
  the old candidate cannot be applied to a later one.
- After Support the Contract is still outstanding. Support is a judgment of one
  exact candidate. Closing the Contract is a separate command, `Discharge`,
  which belongs to the settler role.

The script plays the host: it decides which role each command is sent under. In
the native harness the host authenticates the actor; the kernel checks the role
it is given and cannot check who sent a command.

## How to read part (b)

Part (b) uses [scripts/procontract_credit.py](../scripts/procontract_credit.py)
on the same store and the same kernel commands as part (a).

- The improvement is a Contract. Its terms state the defect, the measure and
  the criterion a candidate must meet, and they are bound into the Contract
  when it is issued, before any candidate exists.
- A candidate is one change to the version that currently serves (the
  incumbent).
- For each unit the script records one state and two continuations of it, one
  under v0 and one under v1, with an outcome for each. The effect is the mean
  difference over units, with an interval from resampling the units.
- `judge` reads the recorded states and outcomes and compares the effect with
  the criterion. The caller cannot report a verdict. If the criterion is met
  the kernel receives a Support; if not, a Defeat.
- Reach says whether the people or agents who shaped the candidate had read
  the cases it is judged on. Here they had not, so the Support is recorded as
  beyond reach, and the terms' convention lets the adoption proceed without a
  separate act by the principal. With evidence within reach the kernel refuses
  that and only the principal's explicit acceptance settles.
- Adoption is the Contract's `Discharge` and the selection of v1 as the new
  incumbent, committed together by the store.

[PROCONTRACT_CREDIT.md](PROCONTRACT_CREDIT.md) describes the module, including
judgments that read the harness's own receipts of each execution.

## What this does not show

- No model runs and no task is executed by an agent. The worker and the
  verifier are a few lines of the script.
- The outcomes of part (b) are made-up numbers. They show how a measured effect
  is recorded and judged, not that any change improves anything.
- The execution receipts in part (b) are placeholder digests. Producing real
  receipts (checkpoint, restore, measurement) is the harness's work and is not
  part of the credit module.
- `procontract_credit.adopt` is called here by the script. In a deployment it
  is a host interface and is not offered to a model as a tool.

To run real tasks, build and install the launcher with the
[release guide](PROCONTRACT_RELEASE.md). The [testing guide](PROCONTRACT_TESTING.md)
lists the test commands, and the [layer document](PROCONTRACT_LAYERS.md) explains
what belongs to the kernel and what to the harness.
