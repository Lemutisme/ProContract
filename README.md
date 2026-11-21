# ProContract for Codex

ProContract keeps the responsibility for a task, and the progress made on it,
in a small kernel that is separate from the agent harness doing the work. This
repository is the code for the paper *ProContract: Decoupling Responsibility
and Progress from an Evolvable Harness*. It is built on
[OpenAI Codex](https://github.com/openai/codex) and is a fork of that
repository.

[Project page and paper](https://lemutisme.github.io/ProContract/) ·
[Quickstart](docs/PROCONTRACT_QUICKSTART.md) ·
[Reproducing the experiments](docs/PROCONTRACT_REPRODUCE.md) ·
[Release guide](docs/PROCONTRACT_RELEASE.md) ·
[Testing](docs/PROCONTRACT_TESTING.md) ·
[Code map](docs/PROCONTRACT_CODE_MAP.md) ·
[Kernel and harness layers](docs/PROCONTRACT_LAYERS.md) ·
[All documentation](docs/README.md)

## What ProContract adds to Codex

- **A kernel and a store.** Every task has a Contract. A deterministic kernel
  (`codex-rs/pro-contract`) decides which commands change it: a worker can only
  propose a candidate, a verifier records Support or Defeat for that exact
  candidate, and the Contract is closed by a separate settling command that
  neither of them can send. The store
  (`codex-rs/pro-contract-store`) persists Contracts, captured files and an
  append-only journal.
- **A native extension and controller** (`codex-rs/ext/pro-contract`) that run
  Codex work under a Contract: capture, checks, review, progress publication
  and continuation in fresh sessions.
- **A launcher and harness in Python** (`scripts/procodex.py` and
  `scripts/procontract_*.py`). The installed command is `procodex`.
- **A research and self-improvement runtime**
  (`scripts/procontract_operations.py`,
  `scripts/procontract_research_runtime.py`, `strategies/research/`). An
  improvement to the harness is a responsibility under the same kernel as an
  ordinary task.
- **A credit module** (`scripts/procontract_credit.py`,
  `scripts/procontract_fork.py`) that records a candidate improvement as a
  Contract with terms fixed beforehand, a measured effect and an adoption
  rule. See [PROCONTRACT_CREDIT.md](docs/PROCONTRACT_CREDIT.md).
- **Decision-level measurement tools** that replay or vary one retained model
  request instead of a whole run. The [documentation index](docs/README.md)
  lists them.

| Component | Responsibility |
| --- | --- |
| **ProContract kernel** | Domain-independent Contract state, authority, evidence coordinates and authorized transitions. Ordinary tasks and self-improvement use the same kernel. |
| **Evolvable harness** | Execution, context, tools, measurement, persistence, version delivery and the replaceable strategy and instruction library. |

Strategy lives inside the harness as prompts, skills and workflow instructions.
The kernel does not choose a research method and does not require a deadline.
The [layer document](docs/PROCONTRACT_LAYERS.md) defines the boundary and names
the migration work that remains in the code.

## Try it

The [quickstart](docs/PROCONTRACT_QUICKSTART.md) builds one binary and runs one
script. It shows a task under the kernel and one self-improvement step, without
a model, Docker or any other service.

### The kernel alone

The kernel is one small crate with no dependency on the rest of the repository:
`codex-rs/pro-contract` (the transition function is 271 lines, in
`src/transition.rs`; the crate depends only on `serde`, `sha2` and `thiserror`).
Its tests include a comparison against an independently written reference model
on random command sequences: `cargo test -p codex-pro-contract`, run in
`codex-rs/`.

## Build, install and run

There is no prebuilt package in this repository. The
[release guide](docs/PROCONTRACT_RELEASE.md) describes the build from source and
the installation. The launcher runs on Linux and needs Docker.

After installation:

```sh
cd /path/to/project
procodex --check 'python3 -m unittest discover'
```

The launcher uses an execution image that is already present locally and runs
project tools offline. The model provider comes from your Codex configuration;
task state is kept under `~/.procodex/`. Use `--container-only` to choose
Docker as the execution sandbox instead of the nested sandbox. The
[launcher guide](scripts/PROCODEX.md) describes configuration, inputs, checks
and recovery.

Tasks have no deadline by default. Supply one when the task has one:

```sh
procodex --task-seconds 21600 --check 'your-test-command'
procodex --strategy /path/to/context-bundle --input specification=/path/to/spec.txt
procodex --check 'your-test-command' status
```

Use the same check and isolation options when inspecting an existing task.
Resuming a task keeps its original authority and limits.

## Self-improvement (RSI)

Self-improvement is a research responsibility that the owner starts
explicitly. The harness can import experience, keep source-backed
observations, select investigations and measurements, build candidates and
record an authorized adoption. Research methods are supplied through the
strategy library.

```sh
procodex rsi --help
```

A research profile provided by the owner enables the `/rsi` commands in the
terminal interface:

```text
/rsi Investigate the retained trajectories and improve the selected harness capability
/rsi status
/rsi input Preserve the existing task and measurement contracts
/rsi cancel
/rsi resume
```

See the [research runtime](docs/PROCONTRACT_RESEARCH_RUNTIME.md), the
[operations interface](docs/PROCONTRACT_OPERATIONS.md) and the
[research strategy library](strategies/research/).

## What is not included

- Benchmark task data and hidden tests. The benchmark adapters in `scripts/`
  expect you to supply the benchmark.
- Trajectories: the recorded runs and model requests from our experiments.
- A hosted model. You configure a model provider and supply your own key.
- Automatic adoption. It is off by default: interactive sessions do not start
  research or adopt a version on their own, and the operations interface
  adopts only under an authority that explicitly allows it.

## Develop

Start with the [code map](docs/PROCONTRACT_CODE_MAP.md) for the call paths,
module responsibilities and tests. The [testing guide](docs/PROCONTRACT_TESTING.md)
lists the test commands and what each one needs.

## Upstream and license

Built on [OpenAI Codex](https://github.com/openai/codex). Upstream-specific setup
and product references are listed in the [documentation index](docs/README.md#upstream-codex).
The repository is licensed under [Apache-2.0](LICENSE); see [NOTICE](NOTICE).
