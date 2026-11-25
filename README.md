# ProContract

**Decoupling Responsibility and Progress from an Evolvable Harness**

[Paper (PDF)](https://www.duo-zhou.com/ProContract/static/files/procontract.pdf) ·
[Project page](https://www.duo-zhou.com/ProContract/) ·
[Quickstart](docs/PROCONTRACT_QUICKSTART.md) ·
[Reproducing the experiments](docs/PROCONTRACT_REPRODUCE.md) ·
[Documentation](docs/README.md)

Long-horizon agents stop early, and their harnesses cannot be changed safely.
Both have one cause: the harness that works on a task also holds what is owed
and decides when it is done, so an agent's "done" measures its method, not the
task. ProContract takes the task out of the harness. A small deterministic
kernel holds each task's Contract and records progress only when an independent
verifier accepts the exact work submitted; what is owed and what was accepted
are the same under every version of the harness, including versions the agent
writes.

On the 200 ProgramBench tasks, with the same model and executable and six hours
per task, ProContract scores 5.07 points above Codex `/goal` (76.28% against
71.22% of hidden tests), and with GPT-6.1 Sol it reaches 89.70%, the state of
the art in mean pass rate.

This repository is the code of the paper. We build ProContract on
[OpenAI Codex](https://github.com/openai/codex); the repository is a fork of
Codex with the kernel, its store, a native extension and a Python harness added.

## The kernel in one script

One Cargo build of the store binary, then one script that runs in a few
seconds, with no model, no Docker and no network:

```sh
(cd codex-rs && cargo build --locked -p codex-pro-contract-store --bin pro-contract-store)
export PRO_CONTRACT_STORE_BIN="$PWD/codex-rs/target/debug/pro-contract-store"
python3 scripts/examples/procontract_quickstart.py
```

It needs the Rust toolchain named in `codex-rs/rust-toolchain.toml` and
Python 3.11 or later. Part of what it prints:

```text
(a) One task under the kernel
  2. the worker proposes a candidate (its captured workspace)
     standing=outstanding version=2 generation=1 candidate=f61e3f3af9 support=none
  3. the worker reports "done"
     the worker sends Support of its own candidate: Rejected(WrongRole { command: Support, required: Verifier, actual: Executor })
     the worker sends Discharge of the Contract: Rejected(WrongRole { command: Discharge, required: Settler, actual: Executor })
     standing=outstanding version=2 generation=1 candidate=f61e3f3af9 support=none   (unchanged)
  4. the verifier checks the frozen candidate and records a Defeat
     counterexample: median([1, 2, 3, 4]) returned 3, expected 2.5
     standing=outstanding version=3 generation=2 candidate=none support=none
  5. the worker proposes a repaired candidate
     standing=outstanding version=4 generation=3 candidate=3cc22711b1 support=none
  6. the verifier checks the frozen candidate and records Support
     standing=outstanding version=5 generation=3 candidate=3cc22711b1 support=reach within
     still outstanding: Support is the verifier's assessment of this exact candidate;
     closing the Contract is a separate command, Discharge, that neither worker nor verifier can send
```

The second part records one improvement to the harness as a Contract in the
same store: its terms are fixed before the candidate exists, the candidate is
judged on a measured effect (synthetic numbers), and adoption is that
Contract's `Discharge`. The [quickstart](docs/PROCONTRACT_QUICKSTART.md)
explains every line.

## What the kernel admits

A Contract binds the task's requirements by hash when it is issued and records
the current candidate, its Support and the settlement. The kernel admits nine
kinds of request, each from exactly one role. A request from another role, or
one that names a stale version, requirement or candidate, is refused and leaves
the Contract as it was.

| Request | Only from | Effect |
| --- | --- | --- |
| `Issue` | issuer, for the user | Creates the Contract; the requirements are bound by hash. |
| `Propose` | executor (the worker) | A frozen capture of the workspace becomes the candidate. |
| `Support` | verifier | Records a positive judgment of the current candidate under the current requirements. The task stays outstanding. |
| `Defeat` | verifier | A verdict against the exact candidate: it is removed and the task stays outstanding. |
| `Withdraw` | verifier | Retracts a Support or a fulfillment; the candidate is kept for a new judgment. |
| `Discharge` | settler: the user, or a rule the user fixed | Marks the task fulfilled on the current Support. On evidence within the worker's reach, only the user's explicit act. |
| `Challenge` | settler | Defeats a candidate or a fulfillment after the fact; the task is outstanding again. |
| `Release` | settler | Ends the responsibility without fulfillment. |
| `Revise` | issuer, a human act | Changes the requirements; the candidate and its judgments no longer apply. |

The worker's "done", its own checks, resting, an exhausted budget and a
replaced harness send no request, so they change nothing. A check the worker
runs is therefore not a judgment, which rules out *progress misjudgment*:
taking a passing check of one's own for completion. The requirements are bound
when the Contract is issued and only a human act revises them, which rules out
*responsibility dilution*: narrowing the task to what one's method covers.
Every `Propose`, `Defeat`, `Challenge`, `Withdraw` and `Revise` raises the
Contract's generation, and a judgment names the generation it judged, so a
judgment of one artifact never lands on another.

The kernel is one crate, [`codex-rs/pro-contract`](codex-rs/pro-contract),
that depends only on `serde`, `sha2` and `thiserror`. Its transition function,
[`src/transition.rs`](codex-rs/pro-contract/src/transition.rs), is 271 lines,
and its tests compare it with an independently written reference model on
random request sequences (`cargo test -p codex-pro-contract`, run in
`codex-rs/`).

## What is in the repository

| Path | What it is |
| --- | --- |
| [`codex-rs/pro-contract/`](codex-rs/pro-contract) | The kernel: Contract state, the nine requests and the transition function. |
| [`codex-rs/pro-contract-store/`](codex-rs/pro-contract-store) | The store: a SQLite ledger that applies the same transition function, content-addressed captures of workspaces, a hash-chained journal and atomic adoption. Its command is `pro-contract-store`. |
| [`codex-rs/ext/pro-contract/`](codex-rs/ext/pro-contract) | The native extension that runs Codex work under a Contract: capture, checks, review, progress publication and continuation in fresh sessions. [`policies/`](codex-rs/ext/pro-contract/policies) holds the task strategy files. |
| [`scripts/procodex.py`](scripts/procodex.py) | The launcher, installed as `procodex`. |
| `scripts/procontract_*.py` | The Python harness: the benchmark runner and batch tools, the store bridge, the self-improvement runtime ([`procontract_operations.py`](scripts/procontract_operations.py), [`procontract_research_runtime.py`](scripts/procontract_research_runtime.py)), the credit module ([`procontract_credit.py`](scripts/procontract_credit.py)) and decision-level measurement. |
| [`strategies/research/`](strategies/research) | The research strategy library used by self-improvement. |
| [`harness/observations/`](harness/observations) | Retained native command history, delivered to later work and review. |
| [`docs/`](docs/README.md) | The documentation, indexed in [`docs/README.md`](docs/README.md). |

Everything else is upstream Codex; the upstream files this fork modifies are
listed in [PROCONTRACT_UPSTREAM_CHANGES.md](docs/PROCONTRACT_UPSTREAM_CHANGES.md).
In the paper's terms, the first two directories are the kernel and its store;
the rest is the harness, whose six parts are execution, context, tools,
measurement, strategy and version delivery. The
[layer document](docs/PROCONTRACT_LAYERS.md) gives the boundary and the
[code map](docs/PROCONTRACT_CODE_MAP.md) the call paths.

## Running tasks

The launcher runs on Linux and executes project tools offline in a local Docker
image. There is no prebuilt package: the
[release guide](docs/PROCONTRACT_RELEASE.md) builds one from source and installs
it as `procodex`, beside any existing `codex`. Then:

```sh
cd /path/to/project
procodex --check 'python3 -m unittest discover'
```

The model provider comes from your Codex configuration, and task state is kept
under `~/.procodex/`. Tasks have no deadline unless you give one with
`--task-seconds`. The [launcher guide](scripts/PROCODEX.md) covers strategy
bundles, inputs, checks, status and recovery.

## Self-improvement

Improving the harness is one more responsibility under the same kernel. A
revision is a candidate under its own Contract, judged against terms fixed
before it exists, and adopted by the user or a rule the user fixed, never by
the harness under improvement. `procodex rsi --help` lists the research
controls; the [research runtime](docs/PROCONTRACT_RESEARCH_RUNTIME.md), the
[operations interface](docs/PROCONTRACT_OPERATIONS.md) and the
[credit module](docs/PROCONTRACT_CREDIT.md) describe the parts. Adoption is off
unless an authority explicitly allows it.

## Reproducing the paper

The paper's controlled comparison runs one executable on the 200 ProgramBench
tasks, six hours per task and no limit on model requests. With GPT-6 Luna,
ProContract raises the mean hidden-test pass rate from 71.22% under Codex
`/goal` to 76.28% (+5.07 points, 95% interval [3.70, 6.54], higher on 154 of
200 tasks). With GPT-6.1 Sol it reaches 89.70%, the state of the art in mean
pass rate on ProgramBench.

[PROCONTRACT_REPRODUCE.md](docs/PROCONTRACT_REPRODUCE.md) goes from a clone to
graded ProgramBench runs: the kernel alone, the test suites, one instance with
ProContract on and off, and a seeded batch, with the model, effort and limits
of the paper's runs. The runner's `off` arm is one ordinary turn, not the
paper's `/goal` baseline. [PROCONTRACT_TEST_RECORD.md](docs/PROCONTRACT_TEST_RECORD.md) records one
run of the tests on this release.

## Not included, and known limits

- Benchmark tasks and hidden tests. Supply your own ProgramBench checkout.
- The records of the paper's runs (trajectories, model requests, scores), the
  ProgramBench task prompt, the Terminal-Bench adapter, and the driver of the
  `/goal` baseline runs. The ProContract arm can be run and graded from this
  release once you supply a prompt.
- A model. Every benchmark step calls your own provider with your own key.
- The full Rust workspace suite has not been run to completion on this
  release; the kernel, store, extension, protocol and Python suites pass
  ([test record](docs/PROCONTRACT_TEST_RECORD.md)).
- The implementation holds one current candidate per Contract and withdraws a
  whole Contract, which is exact when a task has one acceptance. A benchmark
  run issues `Issue`, `Propose`, `Support` and `Defeat`; `Discharge`,
  `Withdraw` and the reopening of a fulfilled Contract are exercised by the
  adoption of harness versions.
- The kernel checks the role a request carries; authenticating who sent it is
  the host's job.

## Citation

```bibtex
@article{zhou2026procontract,
  title  = {ProContract: Decoupling Responsibility and Progress from an Evolvable Harness},
  author = {Zhou, Duo and Qiu, Ruizhong and Zhang, Yuji and Jin, Kaifeng and Tong, Hanghang and Zhang, Eddie and Bai, Jerry},
  year   = {2026},
  note   = {Preprint}
}
```

## License

Apache-2.0, as upstream Codex ([LICENSE](LICENSE), [NOTICE](NOTICE)).
Upstream setup and product references are listed in the
[documentation index](docs/README.md#upstream-codex).
