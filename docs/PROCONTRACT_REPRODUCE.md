# Reproducing the experiments

[Documentation](README.md) · [Quickstart](PROCONTRACT_QUICKSTART.md) ·
[Build and install](PROCONTRACT_RELEASE.md) · [Testing](PROCONTRACT_TESTING.md)

This page goes from a clone of this repository to graded ProgramBench runs
under the settings of the paper's runs: the kernel alone, the test suites, one
instance with ProContract on and off, and a seeded batch. Each step states what
it needs and what it leaves on disk.

| Step | What it shows | Needs |
| --- | --- | --- |
| 1 | The kernel's rules on one task and one self-improvement step | Rust, Python. No model, no Docker |
| 2 | The test suites pass on your machine | Rust, Python; Docker for the native fixtures |
| 3 | One ProgramBench instance, ProContract on and off, graded | A built package, Docker, a model API key, a ProgramBench checkout, a task prompt |
| 4 | A seeded batch of instances in both arms | The same, and time: each run may use its whole time limit |

## 1. The kernel alone

```sh
(cd codex-rs && cargo build --locked -p codex-pro-contract-store --bin pro-contract-store)
export PRO_CONTRACT_STORE_BIN="$PWD/codex-rs/target/debug/pro-contract-store"
python3 scripts/examples/procontract_quickstart.py
```

The [quickstart](PROCONTRACT_QUICKSTART.md) explains every line the script
prints: a worker that can only propose, a verdict bound to one frozen
candidate, a Contract that stays open after a Defeat, and an adoption that is
itself a Contract.

## 2. The tests

The [testing guide](PROCONTRACT_TESTING.md) gives the commands for the kernel
and store tests, the extension tests, the Python harness tests and the native
end-to-end fixtures. [PROCONTRACT_TEST_RECORD.md](PROCONTRACT_TEST_RECORD.md)
is one run of them on this release, with what was skipped and what was not
run.

## 3. One ProgramBench instance, on and off

Build and install the package as in [Build and install](PROCONTRACT_RELEASE.md).
You also need a checkout of ProgramBench with its command available as
`uv run programbench`, the instance's cleanroom image in the local Docker
store, a model API key, and the task prompt as a text file (`TASK_PROMPT`
below), given to both arms.

The runner reads the key from the file named by `OPENAI_API_KEY_FILE` (default
`~/.config/openai/api_key`) or from `OPENAI_API_KEY`. The key stays in the
runner's process; neither it nor the Codex home is mounted into a container.
`PROCONTRACT_MODEL_BASE_URL` selects a provider endpoint other than the
default.

```sh
cd scripts
for arm in on off; do
  python3 procontract_benchmark_runner.py prepare \
    --arm "$arm" --instance INSTANCE --image CLEANROOM_IMAGE \
    --codex-bin "$HOME/procodex-build/package/bin/codex" \
    --prompt TASK_PROMPT --model MODEL --effort EFFORT \
    --run-dir "$HOME/pc-runs/INSTANCE-$arm"
  python3 procontract_benchmark_runner.py run \
    --arm "$arm" --instance INSTANCE --image CLEANROOM_IMAGE \
    --codex-bin "$HOME/procodex-build/package/bin/codex" \
    --run-dir "$HOME/pc-runs/INSTANCE-$arm" --deadline-secs 21600
done
```

`prepare` copies the instance's workspace out of the cleanroom image, writes a
fresh Codex home for the run, and pins the prompt, model and reasoning effort
in the run's manifest; `run` uses the pinned values, so give them to
`prepare`. `run` drives the native client until the thread rests or the time
limit ends. Both arms use the same binary, prompt, model and limit: `on`
enables the `pro_contract` feature, and `off` disables it and ends when the
first turn ends. `python3 procontract_benchmark_runner.py --help` lists the
remaining options, among them the number and length of successor sessions and
`--no-reference`.

The run directory keeps the workspace, the request log with usage and cost for
every model request, the Contract's store and the final status. Grading uses
ProgramBench's own evaluator on the delivered workspace; the batch tool in the
next step packages the workspace and calls it for you.

## 4. A batch in both arms

A batch is planned once, with a seed, and then run. The plan fixes the
instances, the arm of each run, the prompt and the binary, and records their
hashes; a run whose inputs changed is refused.

```sh
cd scripts
# the tasks already used during development, then a salted split of the rest
python3 procontract_pools.py seen  --tasks-dir PROGRAMBENCH/src/programbench/data/tasks \
  --scan PATHS_ALREADY_USED --out seen.json
python3 procontract_pools.py split --tasks-dir PROGRAMBENCH/src/programbench/data/tasks \
  --seen seen.json --salt-file salt.txt --public-out pools.json --sealed-out sealed.json

python3 procontract_batch.py plan --pools pools.json --batch-dir "$HOME/pc-batch" \
  --arms both --seed 20261101 --programbench PROGRAMBENCH \
  --codex-bin "$HOME/procodex-build/package/bin/codex" --prompt TASK_PROMPT
python3 procontract_batch.py null --batch-dir "$HOME/pc-batch"
setsid nohup python3 procontract_batch.py run --batch-dir "$HOME/pc-batch" \
  --parallel 4 --deadline-secs 21600 &
python3 procontract_batch.py status --batch-dir "$HOME/pc-batch"
```

`seen` lists the tasks that appear in the files you scan, for example notes
or run directories from earlier work; an empty directory gives none. `split`
reads only the repository, language and difficulty of a task, never its tests.
It takes one language at a time (`--language`, default `rs`) and puts 40% of
those tasks in the development pool (`--ratios`, default `40,30,30`); `plan`
draws from that pool, 30 instances by default (`--n`), or the instances you
name with `--instances`. `--ratios 100,0,0` puts every task of the language in
the pool, and `--duplicates 0` gives one run per instance and arm (the default
runs four instances twice).

`null` grades, for every planned instance, a submission whose program only
exits with an error, so that evaluation errors the benchmark produces by itself
are known before any run is graded; `run` refuses to start without it. `run` keeps completed runs when it
is restarted; an interrupted attempt is recorded and charged against a fixed
budget of one rerun. The evaluator's pins are checked before and after
grading, an evaluation error that the instance's null result does not explain
is retried at most twice, and the run is otherwise marked invalid, not scored
zero.

## The settings of the paper's runs

- **Time.** One allocation per task, shared by every role of the run: six hours
  on ProgramBench (`--deadline-secs 21600`). Model requests are not capped;
  they are counted across all roles.
- **Execution.** Project tools run offline in the task's container.
- **Models.** The model and reasoning effort of each cohort are given in the
  paper's experimental setup; the controlled comparison uses GPT-6 Luna at max
  effort.
- **Counting.** A run that is invalid because of the provider or the
  configuration is not a score of zero; it is reported as invalid and its
  declared second attempt is used, whatever it scores.
- **What is reported.** The number of valid runs, the mean share of hidden
  tests passed, the tasks at 95% or more, and the tasks fully resolved.

## What differs from the paper's runs

- The paper's baseline is Codex in its `/goal` mode, in the same executable
  with ProContract disabled. The runner's `off` arm is one ordinary turn.
  [benchmark_native_goal.py](../scripts/benchmark_native_goal.py) activates
  `/goal` with no token limit, but neither the runner nor the batch tool
  drives it.
- The paper's cohorts ran the harness as it stood at each cohort; this release
  contains one version of it.
- The task prompt, the records of the paper's runs and the Terminal-Bench
  adapter are not in this release, so the Terminal-Bench numbers cannot be
  regenerated from it.
- Every step from 3 on calls a model through your own provider account, and a
  full batch costs what those calls cost.
