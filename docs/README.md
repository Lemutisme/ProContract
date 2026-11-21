# ProContract-Codex documentation

[Project overview](../README.md) · [Quickstart](PROCONTRACT_QUICKSTART.md) ·
[Code map](PROCONTRACT_CODE_MAP.md)

## Start here

| Goal | Read |
| --- | --- |
| See the kernel and one self-improvement step run, without a model or Docker | [Quickstart](PROCONTRACT_QUICKSTART.md) |
| Build the fork from source and install the launcher | [Release and installation](PROCONTRACT_RELEASE.md) |
| Run tasks, supply context and inputs, inspect progress | [ProCodex launcher](../scripts/PROCODEX.md) |
| Understand what the kernel owns and what the harness owns | [Kernel and harness layers](PROCONTRACT_LAYERS.md) |
| Find the implementation and trace a call | [Code map](PROCONTRACT_CODE_MAP.md) |
| Choose and run tests | [Testing](PROCONTRACT_TESTING.md) |
| Start, inspect or resume a self-improvement (RSI) responsibility | [Research runtime](PROCONTRACT_RESEARCH_RUNTIME.md) |
| Integrate execution, evidence or version selection | [Operations interface](PROCONTRACT_OPERATIONS.md) |

The architecture has two components: a responsibility kernel and an evolvable
harness. Strategy is the harness's replaceable context and instruction
component. The [code map](PROCONTRACT_CODE_MAP.md) relates this ownership model
to the Rust crates, the Python runtime and the inherited Codex capabilities.

## All ProContract documents

Entry documents:

| Document | What it covers |
| --- | --- |
| [PROCONTRACT_QUICKSTART.md](PROCONTRACT_QUICKSTART.md) | Build the store binary and run one script: a task under the kernel and one self-improvement step. |
| [PROCONTRACT_RELEASE.md](PROCONTRACT_RELEASE.md) | Build the `procodex` package from source, install it and run it. |
| [PROCONTRACT_REPRODUCE.md](PROCONTRACT_REPRODUCE.md) | From a clone to the paper's ProgramBench measurements: the kernel alone, one instance in both arms, a graded batch, and the settings of the paper's runs. |
| [PROCONTRACT_TESTING.md](PROCONTRACT_TESTING.md) | The test commands in the repository and what each one needs. |
| [PROCONTRACT_TEST_RECORD.md](PROCONTRACT_TEST_RECORD.md) | One run of the tests on this release: what passed, what was skipped, what was not run. |
| [PROCODEX_STANDALONE.md](PROCODEX_STANDALONE.md) | Install the native client beside an existing Codex, under its own command name and state directory. |
| [PROCONTRACT_CODE_MAP.md](PROCONTRACT_CODE_MAP.md) | Where the kernel, store, native extension, launcher and RSI runtime are implemented, with call paths. |
| [PROCONTRACT_LAYERS.md](PROCONTRACT_LAYERS.md) | What belongs to the kernel and what to the evolvable harness, and the development rule that follows. |

Runtime interfaces:

| Document | What it covers |
| --- | --- |
| [PROCONTRACT_OPERATIONS.md](PROCONTRACT_OPERATIONS.md) | The `Operations` interface: durable execution and evidence operations, capability authority, and version selection and adoption through the store. |
| [PROCONTRACT_RESEARCH_RUNTIME.md](PROCONTRACT_RESEARCH_RUNTIME.md) | `ResearchRuntime`: provisioning, owner control, actor capabilities, and candidate projects and measurements. |
| [PROCONTRACT_PUBLICATION_RECOVERY.md](PROCONTRACT_PUBLICATION_RECOVERY.md) | Progress publication as an operation that outlives the caller that requested it: status, rejoining, no repeated effects. |
| [PROCONTRACT_OBSERVATION_DELIVERY.md](PROCONTRACT_OBSERVATION_DELIVERY.md) | How execution observations are retained as typed sources and delivered to later work and review. |
| [PROCONTRACT_CONTEXT_COST.md](PROCONTRACT_CONTEXT_COST.md) | The working context as a replaceable view of a task: the native context-window profile, and actor identity and cost. |
| [PROCONTRACT_CONTEXT_OBSERVATION.md](PROCONTRACT_CONTEXT_OBSERVATION.md) | `source_context`: reading the context that a retained native request actually delivered. |

Credit and decision-level measurement:

| Document | What it covers |
| --- | --- |
| [PROCONTRACT_CREDIT.md](PROCONTRACT_CREDIT.md) | The credit module: credit as the difference between two measured frozen states, the reach rule, an improvement as one Contract, judgments by receipts, samples and series, and store migration. |
| [PROCONTRACT_STEP_CREDIT.md](PROCONTRACT_STEP_CREDIT.md) | Step-level credit: attributing an outcome to single decisions and choosing the comparison that tests them. |
| [PROCONTRACT_DECISION_REPLAY.md](PROCONTRACT_DECISION_REPLAY.md) | Sending one retained model request again, once, as an Operations measurement. A request-level diagnostic, not a restored run. |
| [PROCONTRACT_DECISION_STUDY.md](PROCONTRACT_DECISION_STUDY.md) | Decision studies as an opt-in research measurement: registration, execution and recovery, and how to read the observation. |
| [PROCONTRACT_FROZEN_EVIDENCE.md](PROCONTRACT_FROZEN_EVIDENCE.md) | Capturing and reopening, read-only, the public evidence view of a selector state. |
| [PROCONTRACT_SELECTOR_CAPTURE.md](PROCONTRACT_SELECTOR_CAPTURE.md) | An opt-in hook that records an actual initial selector exchange together with its evidence view and final request bytes. |
| [PROCONTRACT_SELECTOR_EXCHANGE.md](PROCONTRACT_SELECTOR_EXCHANGE.md) | The parts of a selector exchange run from a capture (native archive export, isolated reader, Operations runner, receipts) and what each has been tested with. |
| [PROCONTRACT_POLICY_SPLICE.md](PROCONTRACT_POLICY_SPLICE.md) | Replacing only the strategy text inside a retained selector request, with a proof of what changed. |
| [PROCONTRACT_RECORD_EVOLUTION.md](PROCONTRACT_RECORD_EVOLUTION.md) | Proposed record-level semantics for proposals, judgments and acceptances. A design, not the deployed kernel. |

Design notes on the harness and on upstream Codex:

| Document | What it covers |
| --- | --- |
| [PROCONTRACT_HARNESS_EVOLUTION.md](PROCONTRACT_HARNESS_EVOLUTION.md) | Harness interfaces to evolve, derived from retained trajectories, and the choices that stay with strategy. |
| [PROCONTRACT_HARNESS_EVOLUTION_ORDER.md](PROCONTRACT_HARNESS_EVOLUTION_ORDER.md) | The order chosen for those harness changes and the reason for each. |
| [PROCONTRACT_UPSTREAM_INTEGRATION.md](PROCONTRACT_UPSTREAM_INTEGRATION.md) | How an upstream Codex merge was integrated and which upstream interfaces the harness should use. |
| [PROCONTRACT_UPSTREAM_CHANGES.md](PROCONTRACT_UPSTREAM_CHANGES.md) | The upstream Codex files that this fork modifies. |

## Strategy

- [Task strategy bundle](../codex-rs/ext/pro-contract/policies/): the native
  loader resolves `work_method.md`, `drafter.md`, `prober.md`, `reviewer.md` and
  `continuation.md`.
- [Research strategy library](../strategies/research/): replaceable
  instructions for continuous research, trajectory analysis, decision
  attribution, credit, and the operations and evidence capabilities.

## Earlier designs

These documents describe earlier designs and study protocols. Their limits and
workflow rules belong to those versions. Use the layer document and the source
code for current behavior.

- [Earlier responsibility-kernel audit](../scripts/PROCONTRACT_RESPONSIBILITY_KERNEL.md).
- [Earlier RSI design](../scripts/PROCONTRACT_RSI.md) and
  [finite acceptance study](../scripts/PROCONTRACT_RSI_ACCEPTANCE.md).

The earlier campaign entry point remains available as `procodex rsi-legacy`.

## Upstream Codex

The fork keeps Codex's core, tool execution and frontend infrastructure.
These references describe upstream Codex or its inherited developer tooling:

- [Upstream Codex repository](https://github.com/openai/codex).
- [Official Codex documentation](https://developers.openai.com/codex).
- [Inherited build guide](install.md) and [contributing guide](contributing.md).
- [Configuration](config.md), [authentication](authentication.md),
  [sandbox](sandbox.md) and [skills](skills.md) references.
- [Retained upstream CI recipes](../ci/README.md).

Install this fork through the release guide above; upstream distribution
commands install OpenAI's Codex package.
