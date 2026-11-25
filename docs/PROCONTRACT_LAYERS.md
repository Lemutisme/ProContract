# Kernel and evolvable harness

[Documentation](README.md) · [Code map](PROCONTRACT_CODE_MAP.md) ·
[Native task lane](PROCONTRACT_NATIVE_LANE.md)

ProContract has two components. The **kernel** holds what is owed and what was
accepted; the **harness** does the work. Every harness reaches a Contract only
through requests the kernel admits, so the harness can be replaced, mid-task or
by the agent itself, while the task stays the same. Ordinary tasks and the
improvement of the harness use the same kernel.

This document states the boundary and the rule for placing new code. The
[code map](PROCONTRACT_CODE_MAP.md) says where each part is implemented.

## Ownership

| Component | Owns | Does not own |
| --- | --- | --- |
| Kernel | Contracts: the responsibility for each task, its candidate, judgments and settlement, their identities and coordinates, and which role may issue each request. Domain-independent, and the same for tasks and for improvements. | A judgment of its own, knowledge of any domain, a workflow, a sample count, a model choice or a deadline. |
| Harness | Everything that does the work: execution, context, tools, measurement, strategy and version delivery. Any part may change, including by the agent. | The Contract. It reads the Contract and sends requests; it cannot write it. |

What follows from the split: replacing an executor preserves the
responsibility; evidence has a subject and a scope; Support and settlement are
distinct; an improvement proposal uses the same requests as a task.

## The six parts of the harness

| Part | Mechanism (code) | Strategy (replaceable instructions) |
| --- | --- | --- |
| Execution | Dispatch, wait, wake, hand off, cancel and recover owned work under its original authority; enforce the resource limits actually granted. | Choose continuation, delegation, rest or another method. |
| Context | Assemble the actual inputs: the current requirements and candidate, and earlier evidence with its source and identity. | Choose which history, interpretations and counterexamples matter for the next action. |
| Tools | Run invocations and keep their identity, conditions, outputs and partial or completed state. | Choose tools, inputs and experiments; interpret what they return. |
| Measurement | Run admitted checks and reviews; keep outcomes, receipts, usage and cost with their exact subject and conditions. | Choose checks and diagnostic questions; assess what a finding implies and what remains uncertain. |
| Strategy | Store, resolve and deliver versioned instructions; keep the description of each capability aligned with its interface. | The instructions themselves: methods for solving tasks and for research, including diagnosis, verification, repair and handoff. |
| Version delivery | Keep version definitions and the inputs actually resolved; apply authorized selections and bind later execution to them. | Propose, compare and select improvements within the admitted scope. |

The verifier's authority is a kernel role; its method is part of the harness
and improves like any other part. These parts share existing state and
operation interfaces; they are not separate services and not a required
sequence of stages.

## Strategy and mechanism

Within the harness, strategy chooses a workflow and mechanisms carry it out.
Executing a handoff or a review needs code; deciding that a particular failure
should lead to that handoff or review is strategy, even where both currently
live in one module.

A verification algorithm is a tool. Choosing the check, supplying its
task-specific assertions, interpreting its limits and choosing the next action
belong to strategy. A structured invocation may carry that choice into code,
but an opaque configurable state machine does not replace a strategy written as
instructions. A delivered instruction is an observable input, not proof that it
changed a decision or improved an outcome.

The same split applied to what failed runs taught:

| Lesson | Harness mechanism | Strategy choice |
| --- | --- | --- |
| Preserve the means of verification | Keep immutable inputs and reference interfaces apart from mutable build outputs, available across sessions | Decide which new comparison would resolve an uncertainty and where to build the candidate |
| Make feedback actionable | Expose retained observations, invocation conditions and bounded diffs | Choose or construct diagnostic examples and use them to plan repairs |
| Keep behavioural progress | Store versioned work and replay runnable checks | Maintain the regression cases learned from repairs and choose which to rerun |
| Notice a stalled method | Make earlier outcomes and changes inspectable; offer review and continuation | Recognize an unproductive approach and choose diagnosis, another method or a handoff |
| Scope uncertain observations | Return partial and per-property results; isolate execution failures | Interpret behaviour and decide what further observation is useful |
| Use the budget well | Account for and enforce the shared allocation actually granted | Divide the remaining effort among implementation, experiments, review and handoffs |

## One kernel for tasks and self-improvement

An improvement is a responsibility whose subject is a harness version or
capability. It accumulates observations, keeps partial progress, continues
across executors and proposes a change through the same requests as an
ordinary task. Support, settlement and selection govern adoption; there is no
separate kernel for self-improvement and no universal research sequence.

The harness captures trajectories, makes them retrievable, runs the selected
experiments and delivers the selected versions; its strategy library directs
analysis, candidate construction and validation. These can be composed into an
end-to-end loop with the [operations interface](PROCONTRACT_OPERATIONS.md).

An observation, an interpretation, a proposed change, an authorized adoption
and the later use of a version are distinct records. Exploratory work proceeds
within existing authority without a kernel judgment for each step. The kernel
does not infer improvement from a score or prescribe sample counts, models or
review frequency. Actual use is established by the instructions, code and tools
that a later execution resolved, and adoption alone is not evidence of a gain.

## Keep the harness essential, and the kernel smaller

Give the harness the smallest coherent capabilities needed to do the work and
observe its effects. Return concrete results, errors, partial observations and
remaining resources through explicit interfaces, and keep assets and state
available across continuations, so that strategies can compose them without a
new controller for each domain.

The harness enforces explicit authorizations and actual limits, for example a
shared six-hour allocation, network isolation or a process lifetime. It does
not invent authority, and it does not decide that a hundred tests suffice,
that three successors are enough or that every task needs the same review
sequence. Do not turn an observed failure into an unconditional gate in code:
heuristics about diagnosis, progress, sufficient evidence or effort start as
replaceable strategy, and a choice that helped one benchmark instance does not
become a universal rule.

The kernel records and enforces the admitted transitions. It implements no
solver, infers no domain truth from a sample count, chooses no testing method
and prescribes no workflow. New evidence can revise earlier conclusions, so
progress is not a score that must rise after every action.

## Tasks need no deadline

A long-horizon task may have no time limit, although its user may expect
results by some time. Progress, handoff, resumption and cancellation work
without a global countdown, and the absence of a deadline is recorded as
absence, not as a large number. Three things stay distinct:

- A user's time expectation guides priorities, intermediate deliveries and the
  choice of method through strategy. It is not authority to end the task or to
  redefine completion.
- An explicit limit, from the user, the benchmark or the environment, is
  enforced by the harness. A missing time limit changes no other permission or
  allocation.
- The timeout of one action bounds that action. Ending an attempt does not end
  the responsibility or erase its completed results.

## Where to put a change

Identify the smallest capability or invariant the change needs and the
replaceable instructions that use it. Put execution and tool code in the
relevant part of the harness and workflow choices in its strategy library.
Extend the kernel only for a missing domain-independent relation or authorized
transition that ordinary work and improvement both need. Keep the interfaces
explicit, so that strategy can change without rewriting a mechanism and a
mechanism can change without changing what the strategy means.

## The code today

The code does not yet meet this boundary everywhere. The native controller
still selects part of the workflow in code (the initial drafting and probing,
the orchestration of verification, some repair policy and one review-protocol
correction) beside the continuation that strategy selects. The kernel holds one
current candidate per Contract, not a graph of acceptances. Evidence
thresholds, successor counts and publication counts, which earlier studied
versions fixed at 100 cases, three successors and eight publications, are
optional settings, and limits recorded with earlier tasks keep their meaning.
This remaining coupling is migration work, not a definition of the kernel or of
the essential harness.

The [native task lane](PROCONTRACT_NATIVE_LANE.md) describes how the current
lane handles deadlines, review, continuation, interruption and resources; the
[context and cost interface](PROCONTRACT_CONTEXT_COST.md) treats the working
conversation as a replaceable view of the task.
