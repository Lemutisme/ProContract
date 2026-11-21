# Credit assignment for test-time RSI

`scripts/procontract_credit.py` (tests: `scripts/test_procontract_credit.py`). It adds no kernel
transition, no second record and no prescribed sequence. It uses the kernel store as it is:
Issue, Propose, Support with its reach and basis, Defeat, Discharge, Withdraw, the atomic
adoption, and the hash-chained journal. It is not wired into `Operations` or the research
runtime; those keep their own adoption path until the owners decide how to join them.

## One definition

**Credit is a difference between two measured frozen states that share everything before them.**

A state is a content-addressed subject bound to a Contract. A measure is registered once: the
pool its cases come from (an identity of their content and source), the complete set of case
names and their number, its evaluator and its environment. An outcome is what that evaluator
found for one state, case by case, over exactly those cases; an outcome over other cases, of
equal count or not, or written by another evaluator or environment, does not count.

A measure's case set is one for every state, or one for each unit (`units`: a unit's id to its
own case set), as when every task has its own tests or every forecast question its own
resolution; an outcome then names the unit it measured. A state names its `measurement_unit`,
the unit of the measure it is measured as, and only an outcome of that unit counts for it. This
is not the unit a statistic counts the state under (`task`, or a checkpoint's `analysis_unit`):
three questions of one date are measured each on its own cases and counted as one date. Both
are recorded facts of a state; under a receipt protocol the checkpoint names both and the
journal and the measurement must agree with it. A case's result is true or false, or, under `scale: "score"`, a number from 0 to 1 where
more is better; how a raw quantity becomes that number belongs to the registered evaluator, so
it is part of the measure's identity. What a result is comes from the registered scale and
never from how a value looks: a number under a pass-or-fail measure is unknown, as it was before
scores existed, and true or false under a score is unknown too; `value`, `cases` and `ledger`
take the scale they read under. A state's value is the share of its cases that pass or the mean
of their scores, so every difference between two states lies in [-1, 1] whatever the measure.
One frozen subject can be measured as several units; the measurement a judgment takes for a
state by default is the latest that names the state's own unit, and a newer label that names no
unit of the measure is refused, not passed over.

| | What is compared | What it says | Adds up? |
|---|---|---|---|
| `ledger` | consecutive measured states of one Contract's record | what each segment acquired, kept and lost, at what cost, under which harness and action | yes, to the record's total change |
| `effect` | two continuations of one recorded state that differ in one input | the measured effect of that input, against the noise of identical continuations | no; effects interact |

The ledger is accounting. It says where value moved and claims no cause. It also shows what
the last state no longer holds from the best one (`forgone`). A state is one occurrence in the
record: the same artifact reached twice keeps what was observed each time. Which cases moved
is reported only when both outcomes kept them, and is unknown otherwise. Cost is conserved:
what was spent before the first measured state, in each segment and after the last add up to
everything the record carried, and a cost that was not recorded stays unknown, never zero.
Each window reports its coverage, per unit: a subtotal is a whole cost only where every state
of the window reported that unit. The effect is the interventional
comparison of `docs/PROCONTRACT_STEP_CREDIT.md`, written as an operation whose inputs and
results are read from the journal.

The same two functions serve three levels: steps of work inside a task (a segment, an action,
a piece of feedback), a change to the harness (a version), and the improver itself (an
improvement Contract is a record too; `history` lists its candidates, judgments and adoption).

## One rule: reach

Cases lie within the reach of every view that has read them, and of a view that has drawn as
many verdicts from them as the terms allow. Exposure belongs to the pool of cases, not to a
measure's registration: the same cases under another evaluator or environment stay read, and a
pool that includes another inherits what was read of it. It holds in the other direction too:
whoever read a pool that terms in this store register as including these cases has read them,
whichever registration the judgment is made under (`_overlapping`). That two pools are both
registered under a larger one does not show that they share a case, so such a pool is not
counted; a pool that includes something this pool also includes is. An overlap that no terms
register is the host's to state.

Which pools a judgment read is part of what it is. When the store's record adds pools to the
measure's own, they go into the judgment's request and its statement (`pools`), so a judgment
retained before the store knew of them is not finished as the same judgment: it is made again
over the pools known now, and the earlier statement stays in the journal unconcluded. A
judgment with nothing added keeps the request and the statement it always had. And a presumed
adoption is refused when the pools known now are not the ones its judgment read
(`_reach_stands`): the Support is withdrawn and the candidate judged again, or the principal
accepts explicitly. A candidate is registered with every
view that shaped it: its author and whoever selected it among alternatives, people included;
views can be added before the candidate is judged and never removed. If the cases are within
reach of any of them, they are within reach for that candidate. Exposure is recorded in the
journal and only accumulates. The host names the views and records every read; this module
computes reach from that record and cannot verify the host's honesty.

A verdict is counted for a view from the moment its judgment's statement is retained, not
from the moment the judgment concludes and charges it: an interrupted judgment has still given
its verdict, and whoever shaped the candidate can read it. So a second judgment for the same
author is not beyond reach merely because the first has not concluded; and when a retained
judgment is made again under other inputs (wider views, more pools), that is a second verdict.

A statement can have been retained by a writer that counted less than this module does. So a
retained answer is checked before it is used. Its reach is derived again from the record as it
stood before the statement (what its views had read and drawn then, over the pools known now);
nothing later is read, not its own conclusion and not what was read afterwards, so a judgment
the record bears out is finished exactly as it was. A statement that says beyond where that
derivation says within is not concluded and not returned for the same call: the judgment is
made again under its own request, naming the one it `replaces`, and the earlier statement
stays in the journal. The two are one verdict in every count, including when the earlier
conclusion wrote some views' exposure before it was interrupted: those charges are retained
but do not charge the replacement again. Unkeyed legacy charges keep their original count.
If it already carries a Support, that Support is not adopted without the
principal and `judge` says to withdraw it first.

Counting verdicts by view is enough when views share nothing. In a system that improves itself
they usually share something: the next candidate is built on the adoption the last verdict
produced, and what was learned from a Defeat is written where later authors read it. So the
terms may also state `max_pool_verdicts`: the cases are within reach, for any candidate, once
the pool has given that many verdicts to anyone. The count is read from the journal
(`verdicts`): each judgment once, by its retained statement or by the verdict it charged, in
any Contract of the store and under any view's name. With `max_pool_verdicts: 1` a pool
settles one judgment without the principal and no more; a later judgment on it is still made
and recorded, as credit from within reach. The count covers one store: a pool also used under
another store is the host's to account for.

Credit from within reach guides work. Only credit from beyond reach can settle an adoption
without the principal: the kernel already rejects a presumed Discharge on a Support whose
reach is within, and the principal's explicit acceptance remains available. So the cases used
to find and fix a defect cannot be the cases that adopt the fix.

## An improvement is one Contract

| Statement | Code | Mechanical control (test) |
|---|---|---|
| A defect the strategy chose to act on is a responsibility; its terms carry scope, defect, evidence and criterion, and name no version | `locate` | `test_terms_precede_candidates` |
| A candidate is one change to the incumbent and names the exact acceptance it was built on | `propose` | `test_one_change_to_the_incumbent` |
| A judgment is computed from recorded forks; the caller names forks and cannot report a verdict or name an evaluator | `judge`, `outcomes` | `test_only_the_registered_evaluator_measures`, `test_the_support_is_reproducible_from_the_journal` |
| A fork's sides are recorded states that continue one recorded state of one task, differ in the version only, and each carry the harness's receipt of its execution | `_sides`, `_fork`, `_null` | `test_a_fork_continues_one_recorded_state_of_one_task` |
| Two inputs are the same exactly when their digests are: an absent input is not a null one, 1 is not true; each side names its version | `same`, `_text`, `_fork`, `_null` | `test_the_same_inputs_are_the_same_record_not_python_equals` |
| An outcome is a definite result, true or false, for each registered case and no other, checked by one function wherever an outcome is written or read; a truthy value is not a pass | `check_outcome`, `measure`, `value` | same test; `test_the_outcome_itself_is_over_the_registered_cases_with_definite_results` (fork) |
| The measurement a judgment uses is checked where it is used. A label left by a writer that accepted other values is not a result: it is unknown, not a fail; the judgment refuses it by name and reason, the label stays as it was, and a label of a state no fork names is not read | `_chosen`, `cases`, `value` | `test_a_label_an_earlier_writer_left_is_not_a_result`, `test_a_label_over_other_cases_is_not_a_result_either` |
| A null fork's sides name their version as a fork's do | `_null` | `test_a_null_fork_names_its_version_too` |
| Terms may name a receipt protocol with what a fork may differ in and leave unshared; then a judgment reads the host's receipts of every pair through the host's reader, joins them to the journal, and is refused without them | `_check`, `_read`, `_bind`, `judge(reader=)`, `measure(receipt=)` | `test_procontract_credit_receipts.py` |
| A sample drawn with unequal probabilities is estimated with its design weights: the terms name the population and the probabilities; the host's draw, made for this candidate on a fixed frame, gives the seed; the sample follows from them; every drawn state is judged; the estimate is for the population | `_design`, `population_id`, `population`, `sample`, `_sampled`, `_sampled_effect`, `judge(sampling=)` | `test_a_sampled_estimate_is_for_the_whole_population`, `test_a_stratum_that_was_not_drawn_is_not_certainty`, `test_the_interval_covers_whatever_the_outcomes_are`, `test_a_sampled_judgment_settles_only_through_its_design_interval`, `test_a_sample_follows_from_the_design_the_frame_and_the_draws_seed`, `test_a_public_seed_would_let_strata_steer_the_sample`, `test_a_sampled_judgment_weights_its_forks_by_the_design`, `test_a_draw_is_for_this_candidate_and_this_frame`, `test_a_judged_state_is_the_population_s_state_not_a_receipt_digest`, `test_a_named_draw_is_never_ignored` |
| Units that come in an order and may depend on their neighbours are estimated over a roster the terms name: every state of it is judged, a unit counts once and a state once, and the interval uses the larger of the plain standard error and one that lets units up to `lag` apart move together | `_series_design`, `roster_id`, `_series_effect`, `_student_95`, `judge(roster=)` | `test_an_estimate_over_a_series_is_the_stated_formula`, `test_the_students_point_is_the_tabulated_one`, `test_order_matters_to_a_series_and_not_to_resampling`, `test_a_fixed_lag_understates_dependence_that_lies_within_it`, `test_a_series_estimate_is_over_the_whole_roster_and_nothing_else`, `test_a_judgment_over_a_series_reads_the_roster_the_terms_name`, `test_a_series_design_is_stated_whole_and_a_roster_is_never_ignored` |
| Under a protocol a task is the host's analysis unit named by the checkpoint, not a journal label; the same two executions count once; a shared execution needs the terms and its cost is counted once | `_bind`, `_shared`, `effect` | `test_a_task_is_the_hosts_unit_not_a_label_in_the_journal`, `test_states_of_one_unit_are_one_task_and_new_executions_are_new_forks`, `test_a_shared_execution_needs_the_terms_and_is_counted_once`, `test_the_cost_of_an_execution_several_forks_name_is_counted_once` |
| A judgment is identified by its complete input: revision and terms, candidate and generation, forks, null controls and the exact measurements used. The same input replays; another input on a judged candidate, new measurements included, is refused until that judgment is withdrawn; old forks do not answer for a new candidate | `judge(receipts=)`, `withdraw` | `test_a_judgment_is_its_complete_input`, `test_new_measurements_are_another_judgment`, `test_old_forks_do_not_answer_for_a_new_candidate` |
| A measure's outcomes are over its registered case set, case by case; an equal count is not the same cases | `measure_id`, `measure` | `test_an_equal_count_is_not_the_same_cases` |
| A measure may give each unit its own case set and grade each case from 0 to 1; an outcome names its unit; a state names the unit it is measured as, which is not the unit it is counted under; true or false is not a score and a number is not a pass | `measure_id`, `check_outcome`, `cases`, `value`, `state(measurement_unit=)`, `_chosen`, `_sides`, `_bind`, `fork.measurement`, `fork.measured` | `MeasureTest`, `test_a_judgment_under_a_graded_measure_by_unit`, `test_a_measurement_by_unit_names_its_unit_and_that_unit_s_cases`, `test_a_fork_under_a_measure_by_unit_is_measured_as_the_checkpoint_s_unit`, `test_under_a_measure_by_unit_a_state_is_measured_as_its_checkpoint_says` |
| Reading a pool that the store's terms register as including these cases is reading them, through any number of inclusions; being registered under the same larger pool is not sharing a case | `_overlapping`, `judge` | `test_reading_a_pool_that_includes_these_cases_is_reading_them` |
| The pools a judgment read are part of its input when the record adds any: a judgment retained before an inclusion was known, or by a writer that did not derive it, is not finished as beyond reach, and its Support is not adopted without the principal | `judge`, `_reach_stands`, `adopt` | `test_a_judgment_retained_before_a_pool_was_known_is_not_finished_as_beyond_reach`, `test_a_support_is_not_presumed_once_another_pool_is_known_to_share_its_cases` |
| Read cases stay read under another registration of the same pool | `expose`, `reach`, `_pools` | `test_read_cases_stay_read_under_another_registration` |
| Disclosing another selector extends a candidate's views before judgment; a changed registration is refused | `propose` | `test_disclosing_another_selector_extends_the_views` |
| An empty or mistyped receipt is refused, never read as no receipt | `_receipt` | `test_an_empty_receipt_is_not_authority` |
| Writers of this module exclude each other across threads and processes | `_exclusive` | `test_two_threads_do_not_both_draw_the_last_verdict` |
| After a revision the Contract's current terms govern, reproduced from their retained digest input | `revise`, `_terms` | `test_revised_terms_govern_the_next_judgment` |
| The noise floor is computed for the effect's own statistic: as many tasks, of the same families, with as many forks per task | `_floor` | `test_the_floor_is_for_the_effects_own_statistic` |
| Cost is compared in the one unit the terms name; a missing cost is unknown, not zero | `_fork`, `effect`, `met` | `test_cost_is_compared_in_the_unit_the_terms_name`, `test_unknown_cost_is_not_zero` |
| Unmet criterion: Defeat; the defect stays owed | `judge` | `test_unmet_criterion_defeats_and_the_defect_stays_owed` |
| Adoption is the improvement's Discharge and the selection, in one transaction, fenced by the latest selection | `adopt` | `test_beyond_reach_effect_is_adopted_and_serves` |
| The store commits an adoption only while the acceptance it was built on is live, checked under the same write lock as the settlement | store `Ledger::adopt` with `supports`, `first_not_live` | Rust `an_adoption_commits_only_while_its_supports_are_live`; `test_the_store_refuses_an_adoption_whose_support_was_withdrawn_meanwhile` |
| Selecting a standing acceptance again is one fenced transaction in the store | store `Ledger::select`; `restore` | Rust `selecting_again_is_fenced_and_names_a_live_acceptance` |
| Only the adoption transaction creates an acceptance; an appended selection or a plain re-settlement creates none and revives nothing | store `acceptances`, `first_not_live` | Rust `an_appended_selection_is_not_an_acceptance`, `settling_again_outside_adoption_revives_nothing` |
| The reading a call checked is the condition it sends: `adopt` reads what is selected once, for its comparison and for the fence; `reconcile` withdraws exactly the settlement it checked, and a Contract settled anew meanwhile is left alone | `adopt`, `withdraw(observed=)`, `reconcile`, `_apply` | `test_the_reading_that_was_checked_is_the_fence_that_is_sent`, `test_reconcile_takes_back_only_the_settlement_it_checked` |
| Only the store's own transactions select an incumbent or move the fence; what serves, and what `reconcile` reopens, are read from the store's records, so an appended entry neither names a version nor withdraws a healthy adoption | store `incumbents`, `Ledger::incumbent`, `Ledger::acceptances`; `incumbent`, `reconcile` | Rust `appended_selections_neither_select_nor_move_the_fence`; `test_entries_appended_to_the_journal_select_nothing_and_withdraw_nothing` |
| A judgment left pending is not finished under views that have since grown | `judge` (the registration is part of the request) | `test_a_pending_judgment_is_not_finished_under_wider_views` |
| Presumed adoption needs beyond-reach evidence; within reach needs the principal | kernel `WithinReach`, `adopt` | `test_within_reach_effect_needs_the_principal` |
| Reach covers the author and whoever selected the candidate | `propose(selectors=)`, `reach` | `test_reach_covers_the_author_and_whoever_selected` |
| Authority is given, never implied: an explicit acceptance needs the host's receipt of the principal's act; a presumed one needs a convention the principal wrote into the terms; another authority on a settled Contract is refused | `adopt`, `install`, `restore` | `test_authority_is_given_never_implied`, `test_restoring_needs_the_principal_or_a_convention_named_in_advance` |
| A judgment interrupted after its statement was retained is finished, not redone, and charges one verdict | `judge`, `_conclude`, `expose(request=)` | `test_an_interrupted_judgment_is_finished_not_redone` |
| Each verdict spends reach | `reach`, `judge` | `test_verdicts_spend_reach` |
| A judgment whose statement is retained has given its verdict to the views that shaped its candidate, before it concludes and charges them; finished later it is not counted twice | `_seen`, `reach`, `judge` | `test_a_retained_judgment_has_given_its_verdict_before_it_concludes`, `test_a_judgment_made_again_under_wider_views_has_the_first_ones_verdict` |
| A retained answer is checked before it is used: a statement that recorded its cases as beyond reach although the record before it, by this module's rules, puts them within is not concluded, not returned and not presumed; the judgment is made again as one that replaces it, and a judgment the record bears out is finished as it was | `_rederived`, `_stale`, `judge`, `_reach_stands` | `test_a_retained_beyond_that_the_record_before_it_contradicts_is_not_concluded`, `test_a_support_that_the_record_before_it_contradicts_is_not_presumed` |
| A pool gives as many verdicts as the terms allow, counted on the pool and not by the name of a view | `verdicts`, `reach(max_pool_verdicts=)`, `judge` | `test_a_pool_gives_as_many_verdicts_as_the_terms_allow_whoever_asks` |
| A stale candidate cannot be adopted | `adopt` | `test_one_change_to_the_incumbent` |
| Withdrawal reopens the Contract with its candidate kept; the selection built on it is void at once; nothing is chosen in its place | `withdraw`, `incumbent`, `_stands` | `test_withdrawal_voids_the_selection_and_restores_the_backlog` |
| Serving again is a new recorded selection of a standing acceptance | `restore` | same test |
| Accepting the same content again is a new acceptance and revives nothing built on the old one | `_stands` | `test_accepting_the_same_content_again_revives_nothing_built_on_the_old_acceptance` |
| Repeated calls return the recorded result | `propose`, `judge`, `adopt`, `withdraw` | `test_repeated_calls_return_the_recorded_result` |
| Earlier candidates and their judgments are never erased | `history` | `test_candidates_are_never_erased` |

An acceptance is identified by the journal sequence of its adopting selection, never by the
content hash of the version. It is live while its Contract is still settled by that same
settlement, named by its generation and subject, and every acceptance it names as support is
live. The store records each acceptance in the adoption transaction that settled it and answers
liveness from that record (`pro-contract-store live`); this module asks the store and keeps no
rule of its own.

## What changed in the store

`codex-rs/pro-contract-store` (no change to the kernel crate or to the reducer):

- An `acceptances` table, append-only, written only by the adoption transaction: the adopting
  selection, the kernel event that settled it, the settlement's generation and subject, and its
  supports. A selection is an acceptance exactly when it has a row. The journal accepts events
  appended by other writers, so a selection that merely looks like an adoption confers nothing,
  and a Contract settled again through the plain command path has no acceptance.
- `AdoptionRequest.supports`: adopting-selection sequence numbers that must be live when the
  adoption commits; otherwise `SupportNotLive` and nothing changes. A request that names none
  serializes exactly as before, so earlier digests and retries are unaffected, and its
  selection is written to the journal exactly as before.
- An `incumbents` table, append-only, written only by the adoption and re-selection
  transactions: each selection of an incumbent and the acceptance it selects. **The incumbent
  fence is now the latest row of this table**, no longer the latest journal entry shaped like
  an incumbent selection: an entry another writer appends to the journal selects nothing and
  does not move the fence. This changes an earlier behaviour, and the existing test
  `a_stale_incumbent_fence_changes_nothing` now changes the incumbent by a real adoption.
- `Ledger::select` and the `select` subcommand: a new incumbent selection of a live acceptance,
  under the incumbent fence, with its own idempotency key. It accepts nothing.
- `Ledger::incumbent`, `Ledger::acceptances`, `Ledger::live` and their subcommands: what is
  selected now, every recorded acceptance with its liveness, and one acceptance's liveness.
  This module reads selections and acceptances only through them, never from the journal.

Opening a store with this binary creates the two empty tables. An adoption made by an earlier
binary has no rows: it is not treated as an acceptance or as the current selection, so it
cannot be named as a support, selected again or reported live, and the first adoption by this
binary on such a store expects no incumbent. An existing runtime therefore cannot be pointed
at this binary without an explicit migration that records its standing adoptions, which is not
written. Nothing is inferred from the journal's older entries.

This is not the record-level migration of `docs/PROCONTRACT_RECORD_EVOLUTION.md`. It adds typed
support edges between acceptances and a commit-time check to the existing store. Liveness of a
dependent is derived from the edges; dependents are not rewritten when a support is withdrawn,
so `reconcile` remains a separate step that reopens their Contracts.

Views, computed from the kernel and the journal:

- `backlog()`: improvements still owed. A defeated candidate or a withdrawn adoption leaves the
  defect here.
- `incumbent()`: the selected version, when the acceptance behind the selection stands;
  otherwise no version, with the last standing acceptance named for `restore`.
- `serving(trial=None)`: what a default execution binds. With `trial`, the candidate of that
  outstanding improvement; this resolves which version a declared trial means. Permission to
  execute is the host's and is recorded separately.

The criterion is data in the terms (`measure`, `min_tasks`, `min_effect`, `above_noise`,
`lower_bound`, `families_nonnegative`, `cost_unit`, `max_cost_ratio`, `max_verdicts`); `met`
compares and sets no threshold of its own. With fewer than two tasks there is no interval, and
a criterion that asks for one is unmet. A judgment's statement is retained in the journal with
its request, revision, generation, terms hash, forks, null controls, measurement and execution
receipts, effect, the exposure it read and its reach; its digest is the Support's evidence and
can be recomputed from the journal. Every digest is taken over the value with object keys
sorted, because the journal returns objects sorted. No deadline appears anywhere.

## An older store: an explicit migration

`scripts/procontract_migration.py` (tests: `scripts/test_procontract_migration.py`, which need
`PRO_CONTRACT_LEGACY_BIN`, a store binary built before acceptances were recorded, and for the
apply tests also the binary of this branch as `PRO_CONTRACT_STORE_BIN`). Planning is read-only;
applying migrates a new target store made from a snapshot, never the source.

- `snapshot(source, destination)` copies the database and its write-ahead log without opening
  them, refuses the copy if the source changed meanwhile, and leaves a receipt naming the source
  and the files' digests. `plan(snapshot)` takes only such a snapshot: a directory without the
  receipt is refused, so it cannot be pointed at a store. It reads a private copy of the
  snapshot's files and gives no manifest if the snapshot differs from its receipt or changed
  while it was read. The receipt is read once: those bytes are hashed, parsed and reported, and
  checked unchanged at the end with the files. The manifest's stated input is what was read.
- **Source rule.** An old adoption is verified by the request the old store retained, not by
  the journal's shape. For every adoption that succeeded, the old store kept an idempotency row
  whose command hash is the digest, in the `adoption` domain, of the settling command, the
  incumbent fence it expected and the identities it recorded. `plan` rebuilds that request from
  the kernel event, the journal entry and the fence, and accepts the entry only if the digest
  is retained together with the Contract state that kernel event produced. A plain settlement
  is hashed in another domain and an appended entry leaves no row, so neither verifies.
- The manifest gives, per incumbent-shaped journal entry: verified or the reason not; the
  evidence; whether it still settles its Contract; and its supports. These are
  `no_incumbent_expected` when the adoption expected no incumbent in this store, which says only
  that it was built on no earlier adoption of this store, and `unknown` otherwise, since the old
  request named none.
- `historical_current` is what the old store's latest entry selected. `would_register` lists
  the acceptance rows, each marked whether its supports are known, and separately a
  `selection_to_activate`, present only when the historical current is a verified adoption that
  still settles its Contract and has known supports. `unknown` lists everything else with its
  reason. Nothing unknown is registered, selected or withdrawn, and no earlier version is chosen
  in place of one that cannot be activated.
- A manifest is `applicable` only when the kernel event chain, the journal chain and every
  Contract's current state were verified; otherwise it names what was not and is a diagnostic
  only. The check of each retained result against the state its settling event produced is
  reported as enabled, checked, passed and failed; an entry that fails it is listed unknown with
  that reason and does not make the other entries diagnostic.
- `state` gives what a target store must hold, exactly, for the manifest to be applied to it:
  the number and head of the kernel events and of the journal.
- The manifest carries the snapshot's receipt and file digests, the source's schema digest,
  the planner's version and the digest of the store binary whose hashing it used, its own
  digest, and an empty `authorization`, which the principal supplies when a manifest is applied.

### Applying a manifest

`target(snapshot, destination)` makes a new store directory from a snapshot, with its own
receipt naming that snapshot, and `apply(manifest, target, authorization=...)` migrates only
such a directory.

- Before the store is opened, `apply` refuses a manifest that does not match its own digest, a
  diagnostic manifest, an empty authorization, and any directory that is not physically the
  target made from the very snapshot the manifest was planned on. A receipt alone proves
  nothing, since it can be copied elsewhere and the ledger beside it can be replaced by a link.
  So the target's receipt records where the target was made and which file its ledger is, and
  `apply` requires all of:
  - the receipt names the manifest's snapshot receipt, source and file digests;
  - the directory's real location is the one the receipt records, and is not the source;
  - nothing in the directory is a symbolic link or has a second name (a hard link), and no
    database, log, index or journal file in it is the source's file;
  - the ledger is the file the target was made with.

  Content is not compared, because a migration changes it and a repeated call must still
  return the same result. A source store or a snapshot is never opened, directly, through a
  copied receipt or through a link.
- The check and the opening are two steps. Keeping other writers and renames out of the target
  directory while a migration runs is the host's control and is not established here.
- The request names only which journal selections and settling events to look at. The store
  does the rest in one transaction (`Ledger::migrate`, subcommand `migrate`) and derives
  everything else from its own records:
  - it must hold exactly the kernel and journal state the manifest was planned for, and no
    acceptance yet;
  - the fence an adoption carried is read from the journal (the incumbent-shaped entry before
    it), never taken from the request, and the earlier adoption request is rebuilt with it; its
    `adoption`-domain digest must be retained with the state the settling event produced. A
    plain settlement, an appended entry, or a retained request offered at another position of
    the journal is refused as not an adoption;
  - only an adoption that expected no incumbent is registered, because only it is known to
    rest on no earlier adoption of the store. A later adoption is real, but what it was built
    on was never recorded: it is refused, whatever a manifest claims, and stays unknown;
  - a selection is activated only if it is the store's own latest incumbent-shaped entry. A
    migration restores what was current and does not choose; selecting an earlier registered
    acceptance instead is a new selection, made by the principal through `select`.
  It then writes the acceptance rows, the current selection if one is restored, and a journal
  entry recording the manifest's digest, the authorization and what was registered. Any failure
  changes nothing; the same request repeated returns the same result; a store that already
  records acceptances is not migrated again.
- So today a migration registers a store's first adoption, and restores it as current only
  when it is the store's only one. Everything later stays unknown until its supports can be
  shown edge by edge, which nothing yet does.

What this does not do: point a runtime at the migrated store, or keep older writers away from
it. An older binary that writes to a migrated store adds journal entries and settlements that
this branch does not recognise as acceptances or selections; nothing it writes is selected or
relied on, but it leaves unknowns. Closing the old writers, or fencing them in the store, is
the host's separate step and is not implemented. No real store has been migrated.

## What the host supplies

Two capabilities of the harness, neither implemented here:

1. **Measure a frozen subject.** Run an admitted evaluator on a materialized subject and hand
   the outcome to `measure`. Labelling exact subjects exists (`procontract_evaluation.py`).
2. **Continue a recorded state under a named input.** Restore the state, continue under the
   incumbent and under the candidate (or twice under the same input for the noise), and write
   each result with `state(parent=..., inputs=...)`. This is the checkpoint continuation of
   `docs/PROCONTRACT_STEP_CREDIT.md`.

### What a fork's receipts must say

`scripts/procontract_fork.py` is the reader's side of capability 2: what the credit side
requires of the harness's receipts before two executions count as a fork of one state. It runs
nothing and records nothing; producing the receipts is the harness's checkpoint, restore and
measurement mechanisms.

| Receipt | It states | Refused when |
|---|---|---|
| checkpoint | the Contract and subject, the execution and allocation that produced the state, that this execution closed, and for each component of the state (files, context, contract, evidence, exposure, environment, pending, resources; others may be added) either the digest captured or why it was not | a component is in neither list or in both; the execution had not closed |
| continuation | the checkpoint's digest, its own execution and allocation, the inputs it was actually given, what it restored and what it did not, how it closed, its costs, the resulting subject | it did not close; it gives no inputs; a component is both restored and omitted |
| measurement | the subject, the measure, evaluator, environment and case set, the outcome, that the execution closed and the host admitted it | any of the four is not the registered one; the outcome is not a true-or-false result for each registered case and no other; not closed; not admitted |

References and digests are non-empty strings, captured and restored components map names to
digests, omissions map names to reasons, costs are finite non-negative amounts by unit, and
closure is the literal `true`. A field of another type is refused, not read as something close
to it.

`pair(checkpoint, left, right, differs)` returns what two continuations share and where they
differ, or refuses: both must name this checkpoint; each is a new execution under a new
allocation, not the other's and not the original's, so a fork renews nothing; each restored
every captured component to the captured digest or says it did not; both left the same
components unrestored; and their resolved inputs differ in exactly what the fork declares
(nothing, for a null fork). An input differs when one side has it and the other does not, or
when the two values are not the same record (`procontract_credit.same`: equal exactly when
their digests are); each difference is returned as the value each side was given, or that it
was absent. What the sides do not share, because the checkpoint never captured
it or neither side restored it, is returned as `not_shared`; `admit(pairing, tolerated)` refuses
a pairing that does not share something the improvement's terms do not tolerate. `tolerated`
is a list of exact component names.

A measurement is of one subject, so `measurement(receipt, registration, subject)` is read for a
named subject and refuses a receipt of any other, however well formed.
`measured(checkpoint, left, right, [of_left, of_right], registration, differs)` is a pairing with
each side's outcome, read only from the measurement of what that side produced; a receipt of
another subject is refused, whether it was swapped or belongs to something else. When both
sides produced the same subject, one measurement of it can serve both: that is one measurement,
marked `measurement_reused`, and not an independent sample of the evaluator's noise. Whether a
judgment requires independent measurements is for its terms and the host's evidence to state.

Passing these checks proves no restore. That an execution is new, was allocated, closed and was
admitted is what the receipts say; binding those statements to the host's own allocation,
execution and measurement records is the producer's and the host's.
Tests: `scripts/test_procontract_fork.py`.

### A judgment under a receipt protocol

An improvement's terms may name `criterion.receipt_protocol = "operations-fork/v1"`, with
`criterion.differs` (the inputs a fork's sides may differ in; it includes the version),
`criterion.tolerated` (the components a fork may leave unshared) and optionally
`criterion.shared_executions` (whether one execution may serve several pairs). They are part of the terms,
so they are fixed before any candidate. Terms without a protocol are judged as before.

Under a protocol:

- The parent state's `receipt` is the host's object id of a `checkpoint_receipt`, each side's
  `receipt` the id of a `continuation_receipt`, and each measurement label carries its own
  `receipt`, the id of a `measurement_receipt` (`measure(..., receipt=)`; `attempt` keeps its
  meaning). An object id and a receipt's content digest stay distinct.
- `judge(..., reader=)` takes the host's reader, a capability that resolves `(object id,
  expected kind)` to the body the host retains. It is supplied by the host, never by a model.
- For every fork and every null fork, `_bind` reads the three kinds of receipt and joins them
  to the journal: the checkpoint is of the parent state's record and subject; each
  continuation's result, resolved inputs and costs are those recorded for the state; each
  measurement receipt's outcome is the chosen label's outcome. Then `procontract_fork.measured`
  and `admit` decide with the terms' `differs` and `tolerated`.
- The unit a statistic counts is the host's. A checkpoint receipt names its `analysis_unit`
  (an `id` and a `family`, bound by the host when the work was assigned or the comparison was
  registered), and the parent state and both sides must be recorded under that task and family.
  A label in the journal cannot make a task: the same receipts recorded under other task names
  are refused. Several parent states of one unit are one task. Tasks, families and the noise
  floor are computed from these units.
- Pairs are distinct executions. Over all forks and null forks of one judgment, the same two
  executions named twice are one pair and are refused. An execution named by more than one pair
  is refused unless the terms set `criterion.shared_executions`; then it is listed in the
  statement's `shared`, with measurements used by more than one pair, and its cost is counted
  once. Units are still what is resampled, so pairs that share an execution inside a unit add no
  task. No threshold is set here.
- What was read is part of the judgment: the statement carries `receipt_protocol`, `shared` and,
  per pair, the unit, the executions, each object id with the digest of the body read, what
  differed, what was not shared and whether one measurement served both sides; the same go into
  the judgment's request. `measurement_reused` and `shared.measurements` are records of reuse;
  the effect and the noise floor do not read them, and they neither remove a measurement's cost
  nor make a sample independent. The cost an effect compares is each continuation's execution
  cost only; the cost of the research and of the measurements is not bound here.
- No reader, a state or label without a receipt, an id the host does not hold, an object of
  another kind or schema: the judgment is refused. It never falls back to treating a receipt as
  an opaque string. Every path that judges, finishes or repeats a judgment of a standing
  candidate reads the receipts again. The one path that does not is the repeat of a completed
  Defeat after its candidate is gone: it returns the retained statement and writes nothing.

### A judgment over a sample

Continuing a state is expensive, so forks may be run on a sample of a larger population of
states, drawn with unequal probabilities: for example every state where a cheap first stage
shows the candidate changes the decision, and a fraction of those where it does not. The plain
mean over such a sample is not the population's. Terms may therefore state, before any
candidate, `criterion.sampling = {"estimand", "method", "population", "strata": {name: probability}}`:

- `method` names how the estimate and its interval are computed, with a version
  (`horvitz-thompson/chebyshev-95/1`). Being in the terms, it is part of every judgment's
  identity: a judgment begun under one method is finished under that method or not at all. A
  statement an earlier writer left under terms that name no method this module implements is
  not finished by it: such terms can be read back, and judging them is refused. Changing the
  method is a revision of the terms. A Support that is known to rest on a method since found
  invalid is corrected by the host with the kernel's Withdraw; nothing is rewritten.

- `estimand` is `state_mean` (the mean over the population's states) or `task_mean` (the mean
  over units of each unit's own mean over its states). `population` is the identity of the
  whole population: its states and the unit each belongs to (`population_id`), whatever stratum
  a comparison later puts them in. Every probability is above zero, so no stratum is assumed to
  contribute nothing. The design holds no seed.
- After the candidate's first stage, the host retains a `sampling_frame`: one row per state of
  the population, drawn or not, valid or not, with the state's identity, its unit and the
  stratum the first stage put it in; the candidate it was measured for; and the record of that
  first stage.
- Only then does the host draw, once for the Contract's candidate, and retain a `sampling_draw`:
  the Contract, the candidate and its generation, the frame by id and digest, and the seed. A
  state is drawn when a number fixed by that seed and the state's identity falls under its
  stratum's probability (`sample`), so anyone can recompute the sample from the terms, the
  frame and the draw.
- Recomputable is not the same as unchosen. With the seed known in advance, whoever sets the
  strata could put in a low-probability stratum exactly the states that would not be drawn
  (`test_a_public_seed_would_let_strata_steer_the_sample`). What makes the sample unchosen is
  the order: the seed must not exist, or not be known, while a stratum or a state's identity
  can still be set, and there is one draw per candidate. The module checks what a record can
  show: the draw names this Contract, candidate and generation and the exact frame; the frame
  is the terms' population and was measured for this candidate. The order and the single draw
  are the host's to guarantee and to show in its own records.
- `judge(..., sampling=<draw object id>)` requires the judged forks to be forks of exactly the
  drawn states. A checkpoint receipt names the `state` it is in the population, so a state's
  identity does not depend on other fields of a receipt. Each fork is recorded under the unit
  the frame lists. A drawn state left out refuses the judgment; an unmeasured state is never
  filled in. Forks of one drawn state share its weight. A draw named under terms without a
  design is refused on every path, and a completed Defeat is read back only for the draw it
  used.
- The estimate is for the whole population (`_sampled_effect`). Each fork carries its state's
  share of the population over the probability with which the state was drawn; the estimate is
  their weighted sum over the population's known total (Horvitz-Thompson), whose expectation
  over the draw is the population's mean. A ratio over the drawn weights is not used: its
  denominator is random and it is biased.
- The interval comes from the design, not from the drawn units. Resampling what was drawn says
  nothing about a stratum that happened not to be drawn: with 140 units always drawn at +0.05
  and seven drawn one time in ten at -1, the population's mean is zero, nearly half the draws
  hold none of the seven, and resampling then reports +0.05 with no spread
  (`test_a_stratum_that_was_not_drawn_is_not_certainty`). Instead: a fork's difference lies in
  [-1, 1]; a state is drawn with a known probability, independently of the others and of what
  its forks return; so the estimate's variance is at most the sum over every state of the
  frame, drawn or not, of share squared over probability, divided by the total squared
  (`population`), and by Chebyshev's inequality the population's mean lies within sqrt(20)
  standard deviations of the estimate at least 95 times in 100. This holds for any outcomes and
  includes the forks' own randomness, under conditions the statement lists: differences in
  [-1, 1]; independent draws with the design's probabilities; which states are drawn not
  depending on what their forks return; forks of different states independent given the
  states. Distinct execution ids do not prove the last two; they are the host's to establish.
  It is wide: about 0.14 either side for a thousand states all drawn. That is this method's
  resolution, not a threshold for any task and not a statement about what other methods could
  resolve; a narrower method is another named method with its own qualification. The interval
  is for the effect over this population: it gives no assurance about the cost ratio and none
  about tasks outside the population.
- Not defined for a sampled estimate, and refused in terms that state a design: a noise floor
  (`above_noise`; null forks are not read), per-family estimates (`families_nonnegative`) and
  shared executions.
- Without a design nothing changes: every task once, its forks equally, tasks resampled.
- Cost under a design is two things. `cost_base` and `cost_candidate` estimate the mean cost of
  continuing one state of the population, in the same way as the effect, and `max_cost_ratio`
  compares those. `paid_base` and `paid_candidate` are what the sampled executions actually
  cost. Neither includes the first stage, the research or the measurements.
- The statement and the request carry the draw and the frame by id and digest, the design, the
  first stage's record, and per stratum how many states the frame holds and how many were drawn;
  each fork's row also carries its state. These are added only under a sampling design. A
  judgment without one has exactly the request it had before sampling existed, so repeating or
  finishing an older judgment finds that judgment and does not make a second statement.

What this does not do: it does not say how the strata were measured (that is the host's first
stage and its own receipts), it does not provide for shared executions in a sampled estimate,
and it sets no sample size.

### A judgment over a series

Some units come in an order and depend on their neighbours: days, releases, successive rounds.
Resampling them as independent uses none of that order: sixteen differences, eight of 0.07 and
eight of 0.01, give the same resampled interval whether they come in two blocks or alternate
(`test_order_matters_to_a_series_and_not_to_resampling`). For such evidence the terms state,
before any candidate, `criterion.series = {"method", "roster", "lag"}`.

- A roster lists the units in their order and, for each, the states measured in it:
  `[{"unit": ..., "states": [...]}, ...]`. The terms hold only its identity (`roster_id`), so
  they do not show which units or states it has. An identity is not a secret: where names can
  be guessed, so can a roster, and the host chooses the names it uses accordingly. `judge(..., roster=<the roster>)` checks the
  roster it is given against that identity, so it does not matter who supplies it. Another
  order, a dropped unit or an added state is another roster and is refused.
- The judged forks cover exactly the roster's states. Each fork's checkpoint receipt names the
  `state` it continues, as under a sample, and its analysis unit, which must be the roster's
  unit for that state. A state without a fork is refused and nothing is filled in. An execution
  that failed and was measured has a result and counts as that result. One that was never
  measured blocks the judgment until it is measured or the principal revises the terms.
- Forks of one state are averaged, then the states of a unit, then the units. A unit counts
  once however many states it has, and a state once however often it was continued.
- With b units, their differences x_1 .. x_b in the roster's order and mean m, the method
  `ordered-mean/larger-of-plain-and-bartlett/student-t-95/1` computes two standard errors and
  uses the larger:
  - plain: s / sqrt(b), with s the sample standard deviation;
  - Bartlett: sqrt((g_0 + 2 * sum over l = 1 .. lag of (1 - l / (lag + 1)) * g_l) / (b - 1)),
    with g_l = sum over i > l of (x_i - m)(x_(i-l) - m) / b. At lag 0 it equals the plain one.
    The weights keep the variance from being negative; one under zero by more than rounding
    (1e-15) is refused, not read as zero.

  The interval is m plus and minus that standard error times the two-sided 95% point of
  Student's t with b - 1 degrees of freedom, computed here in floating point from the finite
  sum that holds for whole degrees of freedom (`_student_95`).
- It is a working interval at a nominal level, and `effect.interval` says so: `nominal: 0.95`,
  both standard errors, the lag, the degrees of freedom, the point used, and its `limits`. It
  is not a coverage guarantee, for few units or for many. A fixed lag's weights count each
  covariance only in part, so the Bartlett error stays below the true one even when units
  further apart than the lag are independent and the mean is close to normal: for units
  e_t + e_(t-1) it tends to 7/8 of the true variance at lag 3 and the interval covers about
  93.3% in the limit, 94.6% at lag 15
  (`test_a_fixed_lag_understates_dependence_that_lies_within_it`). A lag does not cover
  dependence that reaches further. And the interval is not always wider than resampling: when
  neighbours move against each other the plain error is the larger and is the one used.
- What naming this method gives the terms is one stated arithmetic, applied the same way to
  every candidate. That it equals another implementation of the same formula shows the
  arithmetic; it does not show that the nominal level is calibrated for the terms' data.
- `min_tasks`, `min_effect`, `lower_bound`, `families_nonnegative` and `max_cost_ratio` are
  read as elsewhere; a family's value is the mean of its units. Cost is the executions' total
  on each side, a shared execution counted once. No noise floor is defined: `above_noise` in
  the terms and null forks in the judgment are refused. A series is not combined with a sample.
- The statement and the request carry the design and the roster's number of units and states,
  and each fork's row carries its state, only under a series design. Judgments under other
  terms keep the request and the statement they had.
- The method is named in the terms, so it is part of every such judgment's identity. Terms
  that name a method this module does not implement are refused when issued and when judged.
  `judge` checks the terms it reads by the same rule that issues them (`_check`), so terms an
  earlier writer accepted and this module would not are read back, never judged.

Tests: `scripts/test_procontract_credit_receipts.py`, with a stand-in host of typed immutable
objects. They show what the judgment reads and refuses. They restore nothing and run nothing:
the host's reader and the producers of these receipts (checkpoint, restore, measurement) are the
harness's, and that a receipt's statements are true is established there, against the host's
own allocation, execution and closure records.

States written by the native task harness use its own journal bodies. `native_record` and
`native_outcomes` read them for `ledger` without writing: captures that name the Contract become
occurrences, and in-run sealed tallies are grouped by the evaluator, environment and number of
qualified cases they were taken under, since states are comparable only inside one group. A
tally belongs to the occurrence it was taken on (`as_observed`); `corrected` instead gives every
occurrence of an artifact that artifact's latest tally, which is a re-estimate. `native_coverage`
reports how many captures named the Contract, how many named none, and how many tallies found
an occurrence. The native journal records no key from a verification to the capture it
measured, so that tie is inferred from subject and order and every outcome is labelled so. A
judgment or a fork never takes these as measurements. The
native capture carries no cost and is not a restorable checkpoint; cost stays unknown, and these
projections are for reading a record, not for a judgment.

## Known gaps

- Reopening the Contracts built on a withdrawn adoption (`reconcile`) is one kernel command
  each, not one transaction with the withdrawal. Their selection is already void and the store
  will not commit anything on them; what is not atomic is putting their defects back in the
  backlog. Doing that in the withdrawal's own transaction belongs to the record-level store.
- The module serializes its own writers on one host with a file lock; it does not fence a
  writer that reaches the store by other means, and it is not a multi-host lock. The store's
  own transactions (adoption with supports, re-selection) do not depend on that lock.
- The tests need a store binary built from this branch; an older binary has no `live`,
  `select` or `supports`.
- Under a receipt protocol `judge` reads a fork's receipts through the host's reader and checks
  them; the reader itself, and producing the receipts (capturing a state, restoring it, running
  it, confirming closure, measuring, naming the analysis unit), are the harness's and do not
  exist in this module. Terms without a protocol still require only that each side carries a
  receipt string, and their tasks and families are the labels the host wrote with `state`,
  which nothing here verifies: such a judgment's count of tasks is only as good as those labels.
- A module revision older than a key of the terms does not know that key. Given terms that
  state `series`, or `max_pool_verdicts`, such a revision would not refuse them: it would
  resample the units, or count verdicts by view only. Whoever holds the store's authority runs a
  revision that implements what the terms it issues state. The statement records the method it
  used (`effect.method`), and `adopt` in this module refuses a Support whose retained judgment
  did not use the method the terms' design names (`_as_stated`,
  `test_a_support_made_without_the_stated_method_is_not_adopted`); an older `adopt` does not
  check this, and nothing here checks a wrong verdict count after the fact. Before a new
  adoption, a present sampling or series design is validated by the same rule used to issue
  and judge it; missing method names cannot match one another, and an empty or malformed
  design is not treated as absence. Already settled adoption replay retains its original
  behavior and does not create another acceptance.
- A criterion that is complete and mechanical (a proof under its checker) is beyond reach
  vacuously in the kernel's definition; `judge` does not model that case.
- `adopt`, `install` and `restore` take the host's receipt of the principal's act as a string
  and bind it into the record. They cannot authenticate it. They are a host interface and must
  not be offered to a model as a tool.
- `state` and `measure` append without the module's lock: they record what the host observed
  and do not read a snapshot. `expose`, `propose`, `judge`, `adopt`, `restore`, `withdraw`,
  `revise`, `locate` and `reconcile` hold it.
- Selections made by other tooling in the same store carry no registration of what they were
  built on; `incumbent` here reads only stores whose versions were put forward by `propose`.
- It does not decide what is a defect, which change to try or how many forks to run; those are
  strategy (`strategies/research/credit.md`). It does not make a measure valid: a measure every
  state passes moves no credit, and the ledger shows that as hours at the measure's ceiling.

## Run

```sh
PRO_CONTRACT_STORE_BIN=<built pro-contract-store> python3 -m unittest scripts/test_procontract_credit.py
```

Most of the tests run against the real store binary; the rest check the module's pure functions.
Build only the two crates, offline, into a target directory of your own:
`cargo test --offline --locked -p codex-pro-contract -p codex-pro-contract-store`. No model, grader or container runs; evaluator and
continuation results are scripted.
