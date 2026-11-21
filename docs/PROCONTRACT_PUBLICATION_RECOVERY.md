# Publication ownership after caller loss

Publication is an existing harness operation under the original task authority.
It does not propose a candidate, issue Support, or settle a responsibility. The
kernel, task allocation and publication allowance retain their original meaning.

`publish_progress` admits one operation bound to its request ID, assessment
options, executor epoch, original turn and action deadline. Its task owner is
independent of the requesting tool/code-mode transport. If that caller is lost,
the operation continues while its thread remains alive.

`progress_status` is a read-only view of that operation. With no `request_id`, it
returns the pending/latest operation, including the last durable completed
publication after runtime reconstruction. A running response retains the original
assessment options and deadline. Repeating `publish_progress` with the same ID
and exact options joins that owner, without capturing again, repeating checks or
review, spending another reservation, or renewing an action/task deadline.
Changed options require a new ID after previous effects resolve. Completed
receipts remain readable after allocation expiry; this grants no new work.

Live operation ownership and durable pending evidence are distinct. A known
owner is not treated as an unknown-effect crash. Completed journal evidence can
repair an interrupted final grant update, but unknown pending work cannot be
cleared or replayed. Status exposes that uncertainty. Caller cancellation is not
worker cancellation; thread teardown closes new publication admission before
cancelling owned tasks. Shutdown does not manufacture evidence that unavailable
external effects have resolved.

A completed or failed executor turn drains its known publication before handoff
changes phase or proposes work. The drain retains the original epoch and action
deadline and rechecks owner state afterward. It does not force another review or
choose a strategy action. A new root publication is not admitted after that
executor's idle event; a later authorized executor has its own current scope.

Task deadlines can be absent. Per-action allocations remain explicit, and a
reattachment never creates a global task countdown. Status, result recall and
unknown-effect reporting remain available independently of a finite task lifetime.

The mechanism was motivated by the retained TB4.1 math trajectory: a publication
outlived its code-mode caller, while repeated calls had no status/join route. The
baseline gated-worker control reproduces the failure. Candidate controls cover
live caller loss, changed options/epoch, completion races, cold pending ownership,
handoff, teardown and finite/absent/expired task deadlines. A scripted-provider
native control also loses a real code-mode waiter, queries status, restores exact
options and joins the same live operation. These controls establish lifecycle
behavior, not a benchmark-score or cost improvement.
