# Reproducing the experiments

[Documentation](README.md) · [Quickstart](PROCONTRACT_QUICKSTART.md) ·
[Build and install](PROCONTRACT_RELEASE.md) · [Testing](PROCONTRACT_TESTING.md)

This page takes you from a clone of this repository to the measurements the
paper reports on ProgramBench: one task under the kernel without a model, one
benchmark instance with ProContract on and off, and a batch of instances with
grading. Each step states what it needs and what it leaves on disk.

| Step | What it shows | Needs |
| --- | --- | --- |
| 1 | The kernel's rules on one task and one self-improvement step | Rust, Python. No model, no Docker |
| 2 | The test suites pass on your machine | Rust, Python; Docker for the native fixtures |
| 3 | One ProgramBench instance, ProContract on and off, graded | A built package, Docker, a model API key, a ProgramBench checkout |
| 4 | A seeded batch of instances in both arms | The same, and time: each run may use its whole time limit |

## 1. The kernel alone

```sh
(cd codex-rs && cargo build --locked -p codex-pro-contract-store --bin pro-contract-store)
export PRO_CONTRACT_STORE_BIN="$PWD/codex-rs/target/debug/pro-contract-store"
(cd scripts && python3 examples/procontract_quickstart.py)
```

The [quickstart](PROCONTRACT_QUICKSTART.md) explains every line the script
prints: a worker that can only propose, a verdict bound to one frozen
candidate, a Contract that stays open after a Defeat, and an adoption that is
itself a Contract.

## 2. The tests

The [testing guide](PROCONTRACT_TESTING.md) gives the commands for the kernel
and store tests, the extension tests, the Python harness tests and the native
end-to-end fixtures. [PROCONTRACT_TEST_RECORD.md](PROCONTRACT_TEST_RECORD.md)
is one complete run of them on this release, with what was skipped and what
was not run.

## 3. One ProgramBench instance, on and off

Build and install the package as in [Build and install](PROCONTRACT_RELEASE.md).
You also need a checkout of ProgramBench with its command available as
`uv run programbench`, the instance's cleanroom image in the local Docker
store, and a model API key.

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
    --run-dir "$HOME/pc-runs/INSTANCE-$arm"
  python3 procontract_benchmark_runner.py run \
    --arm "$arm" --instance INSTANCE --image CLEANROOM_IMAGE \
    --codex-bin "$HOME/procodex-build/package/bin/codex" \
    --run-dir "$HOME/pc-runs/INSTANCE-$arm" \
    --model MODEL --effort EFFORT --deadline-secs 21600
done
```

`prepare` copies the instance's workspace out of the cleanroom image and
writes a fresh Codex home for the run. `run` drives the native client with the
task prompt until the thread rests or the time limit ends. Both arms use the
same binary, prompt, model and limit; `on` enables the `pro_contract` feature
and `off` disables it. `python3 procontract_benchmark_runner.py --help` lists
the remaining options, among them the number and length of successor sessions
and `--no-reference`.

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
# which tasks have been seen during development, and a salted split of the rest
python3 procontract_pools.py seen  --tasks-dir PROGRAMBENCH/src/programbench/data/tasks \
  --scan PATHS_ALREADY_USED --out seen.json
python3 procontract_pools.py split --tasks-dir PROGRAMBENCH/src/programbench/data/tasks \
  --seen seen.json --salt-file salt.txt --public-out pools.json --sealed-out sealed.json

python3 procontract_batch.py plan --pools pools.json --batch-dir "$HOME/pc-batch" \
  --arms both --seed 20261101 --programbench PROGRAMBENCH \
  --codex-bin "$HOME/procodex-build/package/bin/codex" --prompt PROMPT_FILE
setsid nohup python3 procontract_batch.py run --batch-dir "$HOME/pc-batch" \
  --parallel 4 --deadline-secs 21600 &
python3 procontract_batch.py status --batch-dir "$HOME/pc-batch"
```

`plan` accepts `--instances` to name the instances yourself and `--n` to draw a
number of them. The split reads only the repository, language and difficulty of
a task, never its tests. `run` keeps completed runs when it is restarted; an
interrupted attempt is recorded and charged against a fixed budget of one
rerun. Grading follows a fixed protocol: the evaluator's pins are checked
before and after, an evaluation error that the instance's null result does not
explain is retried at most twice, and the run is otherwise marked invalid, not
scored zero.

## The settings of the paper's runs

- **Time.** One allocation per task, shared by every role of the run: six hours
  on ProgramBench (`--deadline-secs 21600`). Model requests are not capped;
  they are counted across all roles.
- **Execution.** Project tools run offline in the task's container.
- **Arms.** The same binary, prompt, model and reasoning effort in both arms;
  the arms differ only in the `pro_contract` feature. The model and effort of
  each cohort are given in the paper's experimental setup.
- **Counting.** A run that is invalid because of the provider or the
  configuration is not a score of zero; it is reported as invalid and its
  declared second attempt is used, whatever it scores.
- **What is reported.** The number of valid runs, the mean share of tests
  passed, the tasks at 95% or more, and the tasks fully resolved.

## What this release does not contain

- The archives of the paper's own runs. They are large, and the scores above
  can be regenerated with the steps on this page.
- The adapter used for the Terminal-Bench runs, which drives that benchmark's
  own runner. The Terminal-Bench numbers in the paper cannot be regenerated
  from this release alone.
- Access to a model. Every step from 3 on calls a model through your own
  provider account, and a full batch costs what those calls cost.
