# Working context and measured actor cost

The kernel retains responsibility, progress and authorized transitions. An
executor's conversation is a working view of that task. Replacing the view must
preserve the task, its actual grant, durable work and unresolved operations.
Selecting what to retain and when to switch remains replaceable strategy.

## Existing native capability

`config/procontract-context-windows.toml` selects the existing native context
window capability for fresh invocations. It sets no task token limit, request
ceiling, deadline or automatic reset threshold. The native feature name is
`token_budget`; this profile does not use it as a task spending budget.

This native flag also selects unsummarized rollover when the existing automatic
compaction condition fires (`core/src/session/turn.rs::run_auto_compact`). It is
not a manual-tool-only treatment. Qualification and comparisons must distinguish
explicit `new_context` calls from automatic replacements, and recover durable
state on both paths. The current two local controls qualify explicit switching;
they do not independently qualify the automatic-trigger path on this package.

Controllers can merge this TOML fragment into their native configuration before
launch. ProCodex also forwards ordinary Codex configuration overrides:

```sh
procodex -c features.token_budget.enabled=true \
  -c features.token_budget.use_history_notes_extension=true \
  --strategy /path/to/selected/policies
```

The selected `work_method.md` carries context management choices. Native
`new_context` replaces the working conversation; `get_context_remaining` reports
available context. The existing executor/file tools recover task-owned durable
state and `contract_read_evidence` retrieves public ProContract observations.
Reading a source is distinct from proving its contents correct.

Native History/Notes tools additionally require an authenticated compatible
Codex backend. They are unavailable on the custom Responses route used in the
retained ProgramBench runs. The portable recovery path qualified here uses
files and public evidence. The selected profile prevents an implicit legacy
Notes MCP fallback; it does not create a remote history capability.

The November 9 context study already qualified native switching and a bounded
recovery adapter on an earlier package. This change reuses that mechanism and
checks the current `82db02f91` native package, selected strategy and original
contract/grant across the actual transition. It is not a new memory service.

## Qualification

`scripts/test_procontract_context_native.py` uses the real native binary,
container executor, progress publication, public evidence tool and independent
review pipeline with a scripted local model provider. It checks finite and
open-ended tasks:

- The old tool-output marker is absent from the first post-switch model input.
- Reading retained work recovers the marker in a later actual input.
- The selected strategy is delivered before and after the switch.
- Contract state and succession grant are byte-identical across the switch.
- The same executor finishes with one Issue, Propose and Support; no new
  executor or task allocation is introduced.

The control establishes transport and lifecycle behavior. It does not measure
whether a model chooses good notes or improves benchmark quality/cost. Active
frozen cohorts keep their admitted configuration.

## Actor identity and cost

`scripts/procontract_actor_identity.py` supplies a passive join used by the
trajectory retro. Native `thread-id` identifies the actor. A `session-id` may
be shared by a root and several successors. It cannot overwrite thread records
or replace an unknown explicit thread. Session-only joins are accepted only
when unique; conflicting thread records are surfaced.

Worker prompts may be split into ordered native context packets. The helper
reassembles observed contiguous parts for exact delivered-policy comparison.
This does not infer total packet completeness or role authority from task text.
Successor roles come from retained native launch records. Missing charges remain
unknown, and all-role recorded charges remain the primary cost measurement.

Do not replace charged cost with request count or uncached-token count. Cached
history is still repeatedly processed and charged; cold recovery can also cost
more than keeping a useful short context. Evaluate actual decision effects and
all-role cost together.
