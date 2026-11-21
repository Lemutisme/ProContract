# Codex upstream integration and the next harness changes

Selected upstream: `openai/codex` main at
`69231e119bfca0b14453515e97d99688ce1c47c9` (2026-11-13 08:13 UTC).
Fork parent: `c64a87b8feab16f1562a542cf736ac05c26965eb`.
Common base: `5ad689169645a953ec7380762c4ae596712057ee`.
This brings 1,651 upstream commits into the isolated integration branch.
The source merge and its qualification are recorded separately with
the experiment records, which are not part of this repository.

The kernel remains the responsibility/progress/evidence/authorized-transition
component. Changes below concern the harness and its replaceable strategy.
Existing benchmark attempts keep their admitted packages and grants.

The subsequent seven-failure/eight-pass trajectory audit refines these priorities
in [Harness evolution order](PROCONTRACT_HARNESS_EVOLUTION_ORDER.md), including
the still-live publication reattachment gap and capture-before-view ordering.

## Compatibility before further evolution

The upstream interfaces changed while the extension remained on the October 5
baseline. Integration preserves the fork's complete original-user-text capture
while using the upstream item-ID generator; carries the new root-turn metadata;
converts resolved executor environment selections into the new request API; and
keeps explicit-loader helper identity and declared process limits alongside the
upstream executor changes. `/rsi` remains available alongside the revised slash
dispatcher. Generated protocol exports must include the retained ExtensionWorker
variant as well as the new upstream protocol.

Qualification must cover the kernel/store, retained history, helper dispatch,
process limits, extension tests and an actual native package. Test scripted native
progress, review, successor, context replacement and cancellation using that
package before calling the integrated runtime qualified. Old-package controls do
not establish compatibility with the new source. Record build failures and their
repairs, and keep a direct path back to the preceding candidate.

## Use the upstream interfaces in this order

1. **Bind actual capabilities, execution and evidence delivery.** First resolve
   the task's selected environment, identity and tools at the actual invocation.
   Replace broad capability guesses with explicit bindings when adding network,
   MCP or image workflows; keep their owner and observation scope visible.
   Upstream tool lifecycle now
   exposes command-start, tool completion/cancellation, originating code-cell
   identity, separate handler/host timing, and yielded/finished/unavailable cell
   observations. Compose these with the existing ProContract capture projection
   and public evidence archive. Preserve identities and scope, then add historical
   indexing and bounded retrieval. The lifecycle API explicitly disallows logging
   sensitive raw command/payload values: generic telemetry should retain its safe
   identity/outcome facts, while task-authorized evidence uses explicit capture
   sources. Do not turn all tool arguments into a global event transcript.

2. **Provide a portable retained-work view.** Upstream history/notes now separates
   request contracts from backend execution. Its current backend still requires
   supported Codex authentication. Reuse that separation to design source-addressed
   local retrieval for the custom Responses route, rather than assuming a backend
   flag makes remote history available. Keep task artifacts, observed facts, model
   interpretations, current instructions and outstanding effects distinguishable.

3. **Select economical context transitions.** Compare deliberate fresh windows
   with retained-work recovery against existing summarizing compaction, after the
   source/operation recovery path works. Upstream asynchronous compaction is a
   separate optional mechanism: it requires RemoteCompactionSupport::V2 and is
   disabled with TokenBudget. It may improve latency while spending on a discarded
   summary. Measure billed work and actual recovery, not only smaller input or
   configured flags. Do not enable incompatible or unavailable mechanisms together.

4. **Improve tool composition and work selection.** Reuse incremental tool catalogs,
   retained command sessions and per-operation outcomes. Strategy chooses batching,
   focused diagnosis, repair, a different method or rest according to the task and
   the information each action can add. Preserve useful working outputs and learned
   regression cases. No universal batch size, review quota or reset frequency.

5. **Connect selected insights to ordinary research responsibilities.** Use the
   existing inbox/wake, action assignment, version binding and adoption operations.
   A new evidence source must actually be selected and delivered to its consumer;
   storage alone is not consumption. Evaluate one material intervention at a time
   when possible, including a favorable trajectory and a counterexample. Record
   rejected candidates as useful evidence. Keep authorization and actual adoption
   separate from a proposed patch, a successful build or a promising score.

This is a dependency order for the current gaps, not a mandatory research workflow
or another RSI kernel. Networked tools, images, MCP and GPU tasks remain supported
through their selected execution profiles; ProgramBench's offline six-hour setup
does not define the general harness.

## Upstream sources to reuse

| Capability | Selected upstream evidence |
|---|---|
| History/notes backend abstraction | `aa5f492031` and `codex-rs/ext/history-notes/src/backend.rs` |
| Typed/persistent context and item identities | `4f8205c808`, `340a18ed7c`, `77cbcdb4a6` |
| Async compaction, cancellation and charged discarded work | `codex-rs/core/src/session/async_compaction.rs` |
| Tool/command/code-cell observations | `codex-rs/ext/extension-api/src/contributors/tool_lifecycle.rs` |
| Incremental tool delivery and retained command sessions | `33b3f80a09`, `d4902cedbf`, `aa9b9cc94e` |
| Trace storage and its consumer contract | `b8dde1eb40`, `e73744b3f4`, `codex-rs/rollout-trace/AGENTS.md` |

Diagnostic traces have an exact-writer-binary reducer requirement upstream.
Their reduced `state.json` is the consumer interface. Preserve older trace
artifacts with their original writer/reducer; do not use the new runtime to
reinterpret them as if raw trace formats were version-independent.

## Completed integration checks

The actual source merge is `35c2f4ed419e9afa6c671a42d518feb7c3364062`, with
the fork parent and selected upstream as its two parents. The complete native
package builds using Rust 1.99.1 and the upstream checksum-verified V8 152.2.0
archive/binding pair. Its package and check receipts live in the integration
record directory above.

Passed checks: 36 kernel, 42 store, 43 retained-history, 326 protocol/schema,
270 ProContract extension, 16 helper unit tests, and two actual process-limit
tests (735 distinct Rust tests). Twenty local Python configuration/identity
checks and four direct/loader/spoofed-launch controls pass. Formatting and Bazel
dependency-lock generation pass; upstream snapshot/patch whitespace is retained
byte-for-byte instead of altering inherited fixtures.

Eight real native/container controls with a scripted local provider pass: basic
support, same-turn progress feedback, open-ended successor, open-ended context
switch, finite context switch, owner cancellation/resumption, a large worker
packet, and a long original request delivered unchanged to its successor.
All owned native processes and containers are closed. These are integration
controls with zero paid model calls, not new benchmark quality/cost measurements.
