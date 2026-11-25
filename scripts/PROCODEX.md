# ProContract launcher (`procodex`)

[Documentation](../docs/README.md) · [Build and install](../docs/PROCONTRACT_RELEASE.md) ·
[Code map](../docs/PROCONTRACT_CODE_MAP.md)

A separately installed Codex fork with native ProContract, host-owned evidence
references, bounded on-demand reviewer reads, and explicit durable RSI control.
It does not replace `codex`, change its config, or automatically start a benchmark.

## Start

```sh
cd /path/to/a/project
procodex
```

State is under `~/.procodex/workspaces/`, isolated by workspace, image, check and
execution-isolation choice. The current user's provider routing is inherited
from `CODEX_HOME/config.toml` (normally `~/.codex/config.toml`), not their hooks,
MCP servers, permissions or project trust. Default model is gpt-5.6-luna/max;
normal Codex model arguments remain available. Credentials stay on the host.

The executor runs in an offline Docker container. Only the selected project is
mounted writable; the installed package is read-only, capabilities are dropped,
no Docker socket or host credentials are mounted. Images are resolved locally
by content ID; startup does not pull images or install dependencies.

Default Codex policy remains workspace-write/on-request. Some hosts, for example
Ubuntu where AppArmor restricts user namespaces, cannot create the nested user namespace.
In that case startup stops before inference or asks for explicit confirmation.
To knowingly use Docker as the execution sandbox instead:

```sh
procodex --container-only
```

This mode sets Codex's internal filesystem mode to full access **inside the
container**, not on the host. Docker's project-only writable mount, no-network,
capability restrictions and on-request approvals remain. It does not change the
host's AppArmor/sysctl configuration. No silent fallback is performed.

Add an explicit check against each frozen candidate:

```sh
procodex --container-only --check 'python3 -m unittest discover'
```

Without `--check`, verification is review-only, not a test-pass guarantee. The
preview issues a contract for the first substantive request in a session; use
`/new` for a different task. Large workspaces or excluded dependencies may exceed
capture/check capabilities; an abstention/unknown is not success.

New tasks can publish progress during the current turn. ProContract pauses the
project's single provisioned executor, captures its files, resumes execution and
returns retained public feedback. Publication does not certify completion; final
handoff still receives independent review. Use `--no-progress` for a new task
without this capability. Resuming a task preserves its existing grant. Do not run
multiple writers against the same project while using this capture mode.

When supplied, the original task deadline covers preparation, execution and
verification. It does not restart when a successor takes over. On expiry, ProContract interrupts
and awaits the native executor's shutdown. Final responses and successful check
logs are retained as review evidence, with explicit truncation where necessary.

New tasks have no mandatory deadline and no default successor or publication
count ceiling. `--task-seconds N` supplies a hard limit; `--successor-sessions N`
supplies a session ceiling (`0` disables fresh sessions). Previously admitted
task grants are retained on resume. An explicit deadline is enforced even when
progress publication is disabled.

Choose a replaceable strategy context, or provide input files independently of
the writable project:

```sh
procodex --container-only --strategy /path/to/context-bundle \
  --input specification=/path/to/spec.txt --input reference=/path/to/reference
```

`work_method.md` in the bundle supplies the executor's strategy, including workflow
choices, and reaches fresh successors. Optional `drafter.md`, `prober.md`,
`reviewer.md` and `continuation.md` replace the worker and next-work contexts.
Strategy changes do not reset the task's grant. The native automatic Principal
workflow still exists; this interface does
not claim that all legacy orchestration has already moved out of code.

Each named regular input file is retained once outside the mutable project and
mounted read-only at `/inputs/NAME` for execution, checks and enabled review
computations. Original file modes are retained. Builds may replace project outputs
without replacing these inputs. Reopening the same task keeps its original input
snapshot even if the supplied source file later changes or disappears.

`publish_progress({request_id, review: true})` requests an independent diagnostic
assessment while ordinary checks fail or their method is unavailable. It records
findings and gaps under the same budget; it does not issue Support or settle the
task. Reusing the same ID reopens that same assessment. A new diagnosis freezes a
new observation basis, even if artifact bytes did not change. The response includes
the remaining task time and any explicitly supplied count ceilings.

New tasks do not invent quantitative evidence cutoffs. Explicit owner-supplied
criteria are respected, and historical issued policies retain their old values.
Absent cutoffs mean observations are assessed by the review strategy, not automatic
acceptance. Reference observations retain stable channels; one bounded timeout
does not cancel unrelated cases while the overall check budget remains.

For binary or numerical attachments, opt into bounded review computations:

```sh
procodex --container-only --review-compute
```

The reviewer can run Python over selected frozen files in a separate offline,
read-only container with writable `/tmp`. The configured image must already
provide Python and the required libraries. The default grant is 8 calls, 30 seconds
per computation and 64 KiB of reported output, within the original review deadline.
Calls and the deadline survive restart; this flag preserves the existing task home
and succession history. Input hashes, program, output and execution receipts become
task evidence. A numerical check does not establish an unrestricted theorem.

Partial review keeps supported requirements, counterexamples and scoped evidence
gaps together. An independent counterexample may direct a bounded repair while
the gap continues to forbid full Support. Unknown process shutdown prevents
further automatic execution. The
[layer document](../docs/PROCONTRACT_LAYERS.md) gives the boundary, and the
[native task lane](../docs/PROCONTRACT_NATIVE_LANE.md) its evidence and
lifecycle rules.

```sh
procodex --version
procodex doctor
procodex --container-only status       # use the same --check if one was selected
procodex rsi --help
```

The `/rsi` interface starts or resumes a durable research responsibility using an
owner-provided profile. Research methods are supplied as strategy context through
the existing operations interface. Interactive sessions do **not** automatically
mutate/promote policies or launch background research. The historical sealed
campaign CLI remains available as `procodex rsi-legacy`.

## Failure recovery through fresh sessions

After a verified defeat, fresh executor sessions can continue under the original
task's authority and any explicitly supplied limits. The same responsibility,
verification policy, frozen evidence and durable budget survive each handoff.
Successors receive the original task, current owner constraints and recent
failures, rather than copying the failed execution transcript. They may repair
or reimplement task-owned files; the host does not wipe the project or reset Git.

```sh
procodex --successor-sessions 2 --task-seconds 7200 --check 'your-test-command'
procodex --successor-sessions 0  # no fresh successors; the original deadline still applies
```

`procodex status` reports `succeeding` while a fresh executor runs. Its individual
tool calls are not expanded in the current TUI. Extra permissions are never
automatically approved; new owner input cancels and joins the successor.
Exhausted grants, repeated unchanged failed artifacts, incomplete authorization
or unknown evaluator/process state remain explicit failures, not success or
unlimited retries. Old contracts without an intake-time grant do not gain one
on resume. This is task-local recovery, not demonstrated general RSI benefit.

## Behavior to know about

- Draft requirements select host-owned human-request IDs. Workspace documentation
  does not silently acquire the authority of a user requirement.
- Reviewers use frozen, ID-only evidence capabilities without general host
  filesystem access. Bounded computation requires `--review-compute`. Required
  artifacts are indexed first. Reviewers read the complete request/terms;
  positive references are resolved by the host, including partial judgments.
- Draft/probe/review context uses existing Core contextual fragments, at most 7,000 payload
  bytes per item and 96,000 bytes per packet. History is not rewritten.
- The fixed research auditor supports host-issued citation handles. Unknown
  judgments, inventory coverage and adoption rules are not weakened.
- Harness-program schema 3 (`scripts/procontract_harness_program.py`) separates
  an active executor from a resting failed contract lane: checkpoint may run in
  that case, but convergence/check/review protection remains. Schemas 1 and 2
  retain their existing behavior.

This is a pinned fork of Codex. It does not track the latest upstream.

## Build and install

Follow the [release guide](../docs/PROCONTRACT_RELEASE.md). From a clean,
committed checkout, the package builder compiles the native binaries (`codex`,
`codex-code-mode-host`, `codex-exec-server`, `pro-contract-store`,
`pro-contract-check` and `bwrap`), assembles the Codex package layout and
records the source commit and a hash of every packaged file. The output
directory must be outside the checkout and must not exist yet:

```sh
python3 scripts/build_procodex_package.py \
  --version 0.0.0-local --output "$HOME/procodex-build"
```

Then install against an immutable local execution image:

```sh
python3 scripts/install_procodex.py \
  --package "$HOME/procodex-build/package" --image sha256:IMAGE_ID
```

Installations are kept by source commit under
`~/.local/share/procodex/releases/`; `current` selects one release. An existing
Codex executable and its state remain untouched.
