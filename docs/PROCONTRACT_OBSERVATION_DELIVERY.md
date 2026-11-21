# Observation delivery and useful continuation

This capability belongs to the evolvable harness. The responsibility kernel and
its subject, evidence and authorized-transition semantics are unchanged. Ordinary
tasks and RSI use the same interfaces. No task-specific workflow, request quota,
review count or global task deadline is added.

The motivating observations and controls are retained with
the experiment records, which are not part of this repository. The implementation
addresses general interfaces, not particular test expectations:

| Observed failure | Mechanism | Strategy remains responsible for |
|---|---|---|
| Explicit ELF-loader startup gives helper aliases the loader's identity | Preserve the actual native executable and required startup prefix; qualify helper editing and self re-execution | Which tool/action to request and how to respond to its actual result |
| Observer records appear to be executor-edited task files | Producer-declared projection namespaces bound to captured bytes | Interpreting which observed properties matter to the request |
| Real execution observations cannot be cited through their declared source | Typed, subject-bound observation sources in the existing evidence reader | Assessing completion, historical scope and semantic sufficiency |
| A formal review loses a preceding scoped diagnosis | One validated, lazily read prior assessment, including its producing event, subject, method and diagnostic context | Whether prior observations apply and whether a new check would resolve the issue |
| A selector expects execution or audit capabilities that are not configured | Capability descriptions derived from the actual check policy and computation grant | Selecting an operation that can change the next decision |
| Evidence-gathering destroys a usable result or repeats unchanged work | Existing execution, captured work and evidence readers remain available | Inspecting current state, retaining useful output, staging replacements where appropriate, and choosing focused observations |

## Host-produced projections

An optional `evaluation.capture_provider` materializes a bounded observation of
an explicitly provisioned container. The host owns its source freeze, subprocess
effects and original action grant. It cannot replace or renew task authority.
The ordinary bind-mounted workspace route does not require a provider.

The existing `procontract_capture_provider/v1` receipt can include a `projection`
description. Legacy receipts without it remain readable. A provider that emits
the new description declares the origin; filenames or model assertions do not.

```json
{
  "projection": {
    "task_files": {"prefix": "files", "task_root": "/workspace"},
    "observations": [
      {
        "path": "OBSERVATION.json",
        "sha256": "<digest of the retained bytes>",
        "kind": "artifact_observation",
        "scope": "Properties observed under the source freeze, with omitted bodies identified."
      },
      {
        "path": "execution.json",
        "sha256": "<digest of the retained bytes>",
        "kind": "execution_observation",
        "scope": "Recorded call/result pairs through this boundary; missing operations remain unknown."
      }
    ]
  }
}
```

This is an optional part of the receipt, not a complete example of its existing
container/root/observation identity fields. Sources must be distinct normalized
relative paths outside the copied task prefix, and their digests must match
regular files in the frozen manifest. The native capture receipt receives its
own `capture_metadata` origin. The admitted provider is also bound on recovery.

The whole evidence package remains hashed and retained. The harness keeps a
subject-bound origin record; it cannot reinterpret a previously recorded subject.
Task-copy comparison excludes host records and reports missing copies as not
captured, rather than claiming deletion. Original modes, binary digests, link
targets and omitted regions remain accessible in the artifact observations.
The view does not claim a complete filesystem snapshot or identify who wrote an
observed file. A genuine task file with the same basename as an observation stays
a task file. Valid POSIX filenames are retained, including punctuation and line
breaks.

Review catalogs distinguish `candidate`, `host_observation` and unmapped captured
material. Existing `file:N` handles and immutable source digests remain usable.
Only a producer-declared `execution_observation` gains eligibility as a source for
an execution obligation. This eligibility does not certify the observation's
meaning: a running call is not completion, an output assertion is not proof of
its content, and an old execution is not automatically a check of current bytes.

## Historical context and costs

Formal review may inspect one prior public assessment using the existing bounded
reader. Its responsibility, revision, generation, subject, method and producing
journal event remain explicit; the former diagnostic question stays historical.
Later references to the same evidence handle cannot rewrite its origin. A prior
verdict is not a current proof or a new obligation. An unavailable optional history
is represented locally and does not invalidate otherwise available current work.

The full prior body is not appended to every model prompt. Catalogs preview long
source scopes and point to the retained full context; ordinary workspace catalogs
do not gain unused provenance fields. Strategy chooses what to read and whether
to revisit an assessment. It may preserve a working deliverable while investigating
a separate historical-evidence gap, or select a replacement when a real defect
warrants it. None of these choices is a compulsory kernel workflow.

Cost validation reports actual charges and their completeness alongside task
quality, request/token counts, roles and elapsed time. Fewer calls alone do not
establish improvement. A missing charge remains unknown. Frozen benchmark cohorts
retain their admitted code, policies, resources and declared attempt selection;
new versions are evaluated in fresh attempts.

## Scope of qualification

The runtime alias controls cover direct execution, aliases, explicit-loader
execution with a real relative dependency, a changed working directory and a
different argv0 spelling. They do not certify every explicit-loader sandbox mode.
Actual benchmark profiles must qualify their own native/tool delivery and offline
environment. Observer provenance, contradictory/stale history, same-named task
files, failed capture/cleanup and open-ended responsibility are separate controls.

Source paths: `codex-rs/arg0/`, and the extension's
`controller/{capture_provider,capture_projection,observations,archive}.rs`,
`controller/runtime/{verification,continuation}.rs`, `workers/review_evidence.rs`.
Replaceable work choices remain in `policies/work_method.md`.
