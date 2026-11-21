"""What two executions must record to count as a fork of one state.

`effect` in procontract_credit compares two measured states that continue one recorded state under
inputs that differ in one thing. Whether they really do is the harness's to establish, by
restoring a state and running it twice. This module is the other half: what the credit side reads
from the harness's receipts, and when it refuses them. It runs nothing and records nothing.

  checkpoint     a frozen state: every component of it is either captured, by digest, or named as
                 not captured, with the reason. Nothing is absent in silence.
  continuation   one new execution from a checkpoint: what it restored, what it was given, how it
                 closed, what it cost.
  measurement    what a registered evaluator found for one frozen subject, by an execution that
                 closed.

`pair` takes one checkpoint and two continuations and returns what they share and where they
differ, or refuses. A fork differs in exactly the inputs it declares; a null fork in none. What
neither side restored is returned, never hidden: whether a fork that does not share some part of
the state may still settle a judgment is for the improvement's terms, through `admit`. `measured`
adds each side's outcome, read only from a measurement of what that side produced.

Two values are the same exactly when their digests are (`procontract_credit.same`): an absent
input is not a null one, and 1 is not true. A field of another type than the one named here is
refused, never read as something close to it.

The receipts' producers are the harness's checkpoint, restore and measurement mechanisms. These
shapes are the reader's requirements on them, not an implementation of them, and passing them
proves no restore: that an execution is new, was allocated, closed and was admitted is the
host's to bind to its own records.
"""

from __future__ import annotations

import math

import procontract_credit as credit

# What a resumable state consists of. A checkpoint accounts for each; it may add others.
COMPONENTS = ("files", "context", "contract", "evidence", "exposure", "environment", "pending", "resources")


def _names(body: dict, names, what: str) -> None:
    """References and digests are non-empty strings."""
    wrong = [name for name in names if not (isinstance(body.get(name), str) and body[name])]
    if wrong:
        raise ValueError(f"{what} names, as non-empty strings: {', '.join(wrong)}")


def _strings(value, what: str) -> dict:
    """An object from names to non-empty strings: digests, or reasons."""
    if not isinstance(value, dict) or not all(
            isinstance(name, str) and name and isinstance(text, str) and text for name, text in value.items()):
        raise ValueError(f"{what} is an object from names to non-empty strings")
    return value


def _closed(body: dict, what: str) -> None:
    closure = body.get("closure")
    if not isinstance(closure, dict) or closure.get("completed") is not True or closure.get("shutdown_confirmed") is not True:
        raise ValueError(f"{what} did not close: a state still running is not a frozen state")


def _collection(names, what: str) -> set:
    if isinstance(names, (str, bytes)) or not isinstance(names, (list, tuple, set, frozenset)) \
            or not all(isinstance(name, str) and name for name in names):
        raise ValueError(f"{what} is a list of names")
    return set(names)


def checkpoint(body: dict) -> str:
    """The digest of a checkpoint receipt, if it accounts for the whole state.

    `captured` maps a component to the digest of what was frozen; `omitted` maps a component to
    why it was not. Every component in COMPONENTS is in exactly one of them. `closure` confirms
    that the execution which produced the state had finished and released its handles."""
    if not isinstance(body, dict):
        raise ValueError("a checkpoint is an object")
    _names(body, ("record", "subject", "execution", "allocation"), "a checkpoint")
    captured = _strings(body.get("captured"), "what a checkpoint captured")
    omitted = _strings(body.get("omitted", {}), "what a checkpoint omitted")
    both = sorted(set(captured) & set(omitted))
    unaccounted = [name for name in COMPONENTS if name not in captured and name not in omitted]
    if both or unaccounted:
        raise ValueError("a checkpoint accounts for each component once: "
                         + ", ".join(both + unaccounted) + " is captured and omitted, or neither")
    _closed(body, "the execution a checkpoint was taken from")
    return credit._digest("checkpoint", body)


def continuation(body: dict) -> str:
    """The digest of a continuation receipt: a new execution from one checkpoint, closed, with
    what it restored, the inputs it was actually given, and its cost."""
    if not isinstance(body, dict):
        raise ValueError("a continuation is an object")
    _names(body, ("parent", "execution", "allocation", "result"), "a continuation")
    inputs = body.get("resolved_inputs")
    if not (isinstance(inputs, dict) and inputs):
        raise ValueError("a continuation states the inputs it was actually given")
    restored = _strings(body.get("restored"), "what a continuation restored")
    omitted = _strings(body.get("omitted", {}), "what a continuation did not restore")
    if set(omitted) & set(restored):
        raise ValueError("a component is restored or omitted with a reason, not both")
    costs = body.get("costs")
    if not isinstance(costs, dict) or not all(
            isinstance(unit, str) and unit and isinstance(amount, (int, float)) and not isinstance(amount, bool)
            and math.isfinite(amount) and amount >= 0 for unit, amount in costs.items()):
        raise ValueError("a continuation states its costs as amounts by unit, even if none is known")
    _closed(body, "a continuation")
    return credit._digest("continuation", body)


def _given(inputs: dict, name: str) -> dict:
    """One input as a side was given it: its value, or that it was absent. Absent is not null."""
    return {"value": inputs[name]} if name in inputs else {"absent": True}


def pair(parent: dict, left: dict, right: dict, differs=()) -> dict:
    """What two continuations of one checkpoint share and where they differ.

    Refused unless: both name this checkpoint; each is its own execution under its own
    allocation, neither being the checkpoint's nor the other's; each restored every captured
    component to the captured digest or says it did not; both left the same components
    unrestored; and their resolved inputs differ in exactly `differs` (nothing, for a null fork).
    An input differs when one side has it and the other does not, or their values are not the
    same record."""
    declared = _collection(differs, "what a fork declares to differ")
    origin, sides = checkpoint(parent), (left, right)
    digests = [continuation(side) for side in sides]
    if any(side["parent"] != origin for side in sides):
        raise ValueError("both sides continue this checkpoint")
    for name in ("execution", "allocation"):  # a fork renews nothing: not the other side, not the original
        if len({parent[name], left[name], right[name]}) != 3:
            raise ValueError("each side is a new execution under its own allocation")
    captured, unrestored = parent["captured"], []
    for side in sides:
        omitted = side.get("omitted", {})
        for name in side["restored"]:
            if name not in captured:
                raise ValueError(f"{name} was restored but the checkpoint did not capture it")
        for name, digest in captured.items():
            if name not in omitted and side["restored"].get(name) != digest:
                raise ValueError(f"{name} was not restored to what the checkpoint captured")
        unrestored.append(sorted(omitted))
    if unrestored[0] != unrestored[1]:
        raise ValueError("the two sides did not restore the same state")
    a, b = left["resolved_inputs"], right["resolved_inputs"]
    different = sorted(name for name in set(a) | set(b)
                       if (name in a) != (name in b) or not credit.same(a[name], b[name]))
    if set(different) != declared:
        raise ValueError("the sides differ in " + (", ".join(different) or "nothing")
                         + "; this fork declares " + (", ".join(sorted(declared)) or "nothing"))
    return {"parent": origin, "continuations": digests,
            "differs": {name: [_given(a, name), _given(b, name)] for name in different},
            "not_shared": sorted(set(parent.get("omitted", {})) | set(unrestored[0]))}


def admit(pairing: dict, tolerated=()) -> dict:
    """The pairing, if every component it did not share is one the terms tolerate, by name."""
    allowed = _collection(tolerated, "what the terms tolerate")
    beyond = [name for name in pairing["not_shared"] if name not in allowed]
    if beyond:
        raise ValueError("the sides do not share: " + ", ".join(beyond))
    return pairing


def measurement(body: dict, registration: dict, subject: str, unit: str | None = None) -> dict:
    """The outcome of a measurement receipt, if it measures `subject` under the registered
    measure: its evaluator, environment and case set, a definite result for each registered case
    and no other, by an execution that closed and that the host admitted. A measurement is of one
    subject, so reading one means naming the subject expected; a receipt for another subject is
    refused however well formed. Under a measure by unit, `unit` is the unit the state is measured
    as, and a receipt of another unit is refused. The caller's own description of a measurement is
    not read."""
    if not isinstance(body, dict):
        raise ValueError("a measurement is an object")
    _names(body, ("subject", "execution"), "a measurement")
    if not (isinstance(subject, str) and subject) or body["subject"] != subject:
        raise ValueError("the measurement is of another subject than the one it is read for")
    cases = registration.get("cases")
    if "units" in registration:  # the case set is the measured unit's own
        named = body.get("unit")
        if not isinstance(named, str) or named not in registration["units"]:
            raise ValueError("the measurement names no unit of the registered measure")
        if unit is not None and named != unit:
            raise ValueError("the measurement is of another unit than the state is measured as")
        cases = registration["units"][named]["cases"]
    for name, expected in (("measure", credit.measure_id(registration)), ("evaluator", registration["evaluator"]),
                           ("environment", registration["environment"]), ("cases", cases)):
        if name not in body or not credit.same(body[name], expected):
            raise ValueError(f"the measurement's {name} is not the registered one")
    _closed(body, "a measurement")
    if body.get("admissible") is not True:
        raise ValueError("the host did not admit this measurement")
    return credit.check_outcome(registration, body.get("outcome"), unit=body.get("unit") if "units" in registration else None)


def measured(parent: dict, left: dict, right: dict, measurements, registration: dict, differs=()) -> dict:
    """A pairing together with each side's outcome: `measurements` holds two receipts, the first
    of what the left continuation produced and the second of what the right one produced. A
    receipt of anything else than that side's result is refused, so an outcome cannot be attached
    to a side it was not measured on.

    When both sides produced the same subject, one measurement of it can serve both. That is one
    measurement, not two: `measurement_reused` says so, and it is no independent sample of the
    evaluator's noise. Whether a judgment needs independent measurements is for its terms."""
    if not isinstance(measurements, (list, tuple)) or len(measurements) != 2:
        raise ValueError("a fork has one measurement for each side")
    pairing = pair(parent, left, right, differs)
    expected = None
    if "units" in registration:  # which cases apply: the unit the checkpoint says its state is measured as
        expected = parent.get("measurement_unit")
        if not (isinstance(expected, str) and expected in registration["units"]):
            raise ValueError("under a measure by unit a checkpoint names the unit its state is measured as")
    outcomes = [measurement(receipt, registration, side["result"], expected)
                for side, receipt in zip((left, right), measurements)]
    digests = [credit._digest("measurement", receipt) for receipt in measurements]
    return dict(pairing, results=[left["result"], right["result"]], outcomes=outcomes,
                measurements=digests, measurement_reused=digests[0] == digests[1])
