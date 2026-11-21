"""Credit assignment for test-time RSI, on the existing kernel and journal.

Credit is a difference between two measured frozen states that share everything before them.

  ledger   along one Contract's record: what each segment of work acquired, kept and lost under
           one measure, at what cost and under which harness. Exact accounting: the segments add
           up to the record's total change. It says where value moved, not why.
  effect   across a fork: one state continued under two inputs that differ in one thing. A
           measured effect of that one thing, against the noise of two identical continuations.
  reach    a measure lies within the reach of every view that has read its cases. Credit within
           reach guides work; only credit from beyond reach can settle an adoption.

An improvement is one Contract from a located defect to its adoption. Its terms carry the evidence
and the criterion before any candidate exists; a candidate is one change to the incumbent; its
Discharge is the adoption; its Withdraw is the rollback and puts the defect back in the backlog.
Backlog, trials and incumbent are views of the kernel and its journal. No second record is kept,
no kernel transition is added, and no step is prescribed: these are capabilities a strategy calls.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import hashlib
import json
import math
import random
import statistics
import threading
from pathlib import Path

import procontract_store as kernel

IMPLEMENTATION = "procontract_credit/2"
# Protocols under which a judgment reads the harness's own receipts of a fork (procontract_fork).
RECEIPT_PROTOCOLS = ("operations-fork/v1",)


def _canonical(value):
    """The same value with object keys in sorted order at every depth. The journal returns
    objects sorted, and the native digest follows the order it is given, so every digest here
    is taken over this form and can be recomputed from what the journal returns."""
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    return [_canonical(item) for item in value] if isinstance(value, (list, tuple)) else value


def _text(value) -> str:
    """The canonical JSON text of a value, which is what every digest here is taken over. A value
    that is not JSON (a key that is not a string, a number that is not finite) is refused."""
    def check(item):
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ValueError("an object's keys are strings")
            for inner in item.values():
                check(inner)
        elif isinstance(item, (list, tuple)):
            for inner in item:
                check(inner)
        elif not (item is None or isinstance(item, (str, bool, int, float))):
            raise ValueError("not a JSON value: " + type(item).__name__)
    check(value)
    return json.dumps(_canonical(value), allow_nan=False)


def same(a, b) -> bool:
    """Whether two values are the same record: equal exactly when their digests are. Python's own
    equality is wider (1 == True, 1 == 1.0) and must not decide what two executions were given."""
    return _text(a) == _text(b)


def _digest(domain: str, value) -> str:
    return kernel.digest_input(domain, _text(value))


_LOCKS: dict = {}
_GUARD = threading.Lock()


@contextlib.contextmanager
def _exclusive(store: Path):
    """One writer of this module at a time on one host: a lock per store that a thread may
    re-enter, held together with a file lock against other processes. It does not fence writers
    that reach the store by other means."""
    key = str(Path(store).resolve())
    with _GUARD:
        entry = _LOCKS.setdefault(key, {"lock": threading.RLock(), "depth": 0, "handle": None})
    with entry["lock"]:
        if entry["depth"] == 0:
            Path(store).mkdir(parents=True, exist_ok=True)
            entry["handle"] = open(Path(store) / "credit.lock", "w")
            fcntl.flock(entry["handle"], fcntl.LOCK_EX)
        entry["depth"] += 1
        try:
            yield
        finally:
            entry["depth"] -= 1
            if entry["depth"] == 0:
                entry["handle"].close()
                entry["handle"] = None


def _receipt(value, what: str) -> str | None:
    """The host's receipt of the principal's act: absent, or a non-empty string. An empty or
    mistyped value is refused, never read as "no receipt"."""
    if value is None or (isinstance(value, str) and value.strip()):
        return value
    raise ValueError(f"{what} is the host's receipt of the principal's act: a non-empty string")


def _locked(function):
    @functools.wraps(function)
    def wrapper(store, *args, **kwargs):
        with _exclusive(store):
            return function(store, *args, **kwargs)
    return wrapper


# ------------------------------------------------------------------ measured states (pure)


def _score(result) -> bool:
    """A graded result: a number from 0 to 1, more being better. True and false are not numbers."""
    return isinstance(result, (int, float)) and not isinstance(result, bool) and math.isfinite(result) and 0 <= result <= 1


def cases(outcome: dict | None, scale: str | None = None) -> dict | None:
    """Per-case results of an outcome, when it kept them and each is definite under the measure's
    scale: exactly true or false, or, under scale "score", a number from 0 to 1. What a result is
    comes from the registered scale and never from how a value looks: a number under a pass/fail
    measure is not a result, and neither is true or false under a score. Anything else is unknown."""
    results = outcome.get("cases") if isinstance(outcome, dict) else None
    if not (isinstance(results, dict) and results):
        return None
    definite = _score if scale == "score" else (lambda result: result is True or result is False)
    return results if all(definite(result) for result in results.values()) else None


def value(outcome: dict | None, scale: str | None = None) -> float | None:
    """What a state reaches under a measure, from 0 to 1: the share of its cases it passes, or
    under scale "score" the mean of their scores. None when the state was not measured, or when a
    case it kept has no definite result under that scale."""
    if outcome is None:
        return None
    if "cases" in outcome:
        results = cases(outcome, scale)
        return None if results is None else sum(float(result) for result in results.values()) / len(results)
    return outcome["passed"] / outcome["total"] if outcome.get("total") else None


def _add(total: dict, cost: dict | None) -> dict:
    for name, amount in (cost or {}).items():
        total[name] = total.get(name, 0) + amount
    return total


def _cover(coverage: dict, cost: dict | None) -> dict:
    """Count, per unit, how many states of a window reported a cost in it. A unit's subtotal is
    the window's whole cost in that unit only when every state reported it; a state that
    reported nothing is unknown, which a recorded zero is not."""
    coverage["states"] = coverage.get("states", 0) + 1
    units = coverage.setdefault("reported", {})
    for name in cost or {}:
        units[name] = units.get(name, 0) + 1
    return coverage


def ledger(states: list[dict], outcomes: dict, scale: str | None = None) -> dict:
    """Account for one record's measured change, segment by segment.

    `states` are the record's frozen states in order, each {"subject", "by", "cost"} where `cost`
    is what was spent since the previous state, and optionally "id": the identity of that
    occurrence in the record. `outcomes` maps an occurrence's id (the subject when no id is
    given) to its outcome under one measure, so the same artifact reached twice keeps what was
    observed each time. A segment runs between two consecutive measured states; unmeasured
    states in between add their cost and their `by` to it.

    `acquired` and `lost` are None when either outcome kept only counts: unknown, not empty.
    `scale` is the measure's registered scale; under "score" they are None as well.
    Recorded cost is conserved: `prefix` (up to the first measured state), the segments and
    `tail` (after the last) add up to everything the states carried. Each window also reports its
    coverage: how many states it holds and, per unit, how many of them reported a cost. A
    subtotal is a whole cost only where every state reported that unit.
    """
    segments, previous, pending_cost, pending_by, prefix = [], None, {}, [], {}
    pending_cover, prefix_cover, measured = {}, {}, []
    for state in states:
        _add(pending_cost, state.get("cost"))
        _cover(pending_cover, state.get("cost"))
        pending_by.append(state.get("by") or {})
        outcome = outcomes.get(state.get("id", state["subject"]))
        if value(outcome, scale) is None:
            continue
        measured.append((state, value(outcome, scale)))
        if previous is None:
            prefix, prefix_cover = pending_cost, pending_cover
        else:
            before, after = cases(previous[1], scale), cases(outcome, scale)
            # Which cases moved is known only for pass and fail; scores change by degree.
            shared = sorted(set(before) & set(after)) if before and after and scale is None else None
            segments.append({
                "from": previous[0].get("id", previous[0]["subject"]), "to": state.get("id", state["subject"]),
                "change": value(outcome, scale) - value(previous[1], scale),
                "acquired": None if shared is None else [c for c in shared if after[c] and not before[c]],
                "lost": None if shared is None else [c for c in shared if before[c] and not after[c]],
                "cost": pending_cost, "coverage": pending_cover, "by": pending_by,
            })
        previous, pending_cost, pending_cover, pending_by = (state, outcome), {}, {}, []
    if not measured:
        return {"segments": [], "change": None, "forgone": None, "best": None, "last": None,
                "prefix": {}, "tail": pending_cost, "prefix_coverage": {}, "tail_coverage": pending_cover}
    best = max(measured, key=lambda pair: pair[1])
    return {
        "segments": segments,
        "change": measured[-1][1] - measured[0][1],
        # What the record held at its best measured state and no longer holds at its last one.
        "forgone": best[1] - measured[-1][1],
        "best": best[0].get("id", best[0]["subject"]), "last": measured[-1][0].get("id", measured[-1][0]["subject"]),
        "prefix": prefix, "tail": pending_cost, "prefix_coverage": prefix_cover, "tail_coverage": pending_cover,
    }


def attribute(segments: list[dict], key) -> dict:
    """Sum change and cost over segments that share `key(by)`; a segment whose states disagree is
    kept apart as "mixed". Sums of one record's rows equal that record's total change."""
    rows: dict = {}
    for segment in segments:
        names = {key(by) for by in segment["by"]}
        name = names.pop() if len(names) == 1 else "mixed"
        row = rows.setdefault(name, {"segments": 0, "change": 0.0, "acquired": 0, "lost": 0, "cost": {},
                                     "segments_without_cases": 0, "coverage": {"states": 0, "reported": {}}})
        row["coverage"]["states"] += segment["coverage"].get("states", 0)
        _add(row["coverage"]["reported"], segment["coverage"].get("reported"))
        row["segments"] += 1
        row["change"] += segment["change"]
        if segment["acquired"] is None:
            row["segments_without_cases"] += 1  # counts only: which cases moved is unknown
        else:
            row["acquired"] += len(segment["acquired"])
            row["lost"] += len(segment["lost"])
        _add(row["cost"], segment["cost"])
    return rows


def _by_task(forks: list[dict]) -> dict:
    tasks: dict = {}
    for fork in forks:
        tasks.setdefault(fork["task"], []).append(fork)
    return tasks


def _means(forks: list[dict]) -> dict:
    return {task: sum(f["candidate"] - f["base"] for f in group) / len(group)
            for task, group in _by_task(forks).items()}


def _resampled(differences: list[float], resamples: int, seed: int) -> list[float]:
    rng, n = random.Random(seed), len(differences)
    return sorted(sum(rng.choice(differences) for _ in range(n)) / n for _ in range(resamples))


def _floor(forks: list[dict], null: list[dict], resamples: int, seed: int) -> float:
    """The absolute value that the effect's own statistic stays under 95% of the time when both
    sides of every fork had the same input: a mean over as many tasks, of the same families, with
    as many forks per task, drawn from the null forks."""
    rng = random.Random(seed)
    shape = [(group[0].get("family"), len(group)) for group in _by_task(forks).values()]
    pool: dict = {}
    for task, group in _by_task(null).items():
        pool.setdefault(group[0].get("family"), []).append([f["candidate"] - f["base"] for f in group])
    for family, _ in shape:
        if family not in pool:
            raise ValueError(f"null forks do not cover task family {family!r}")
    draws = []
    for _ in range(resamples):
        total = 0.0
        for family, count in shape:
            differences = rng.choice(pool[family])
            total += sum(rng.choice(differences) for _ in range(count)) / count
        draws.append(abs(total / len(shape)))
    return sorted(draws)[min(resamples - 1, int(0.95 * resamples))]


def _weights(forks: list[dict]) -> list[float] | None:
    """The forks' design weights, when they carry them: all or none, each a positive number."""
    given = [fork.get("weight") for fork in forks]
    if all(weight is None for weight in given):
        return None
    if not all(isinstance(weight, (int, float)) and not isinstance(weight, bool) and math.isfinite(weight) and weight > 0
               for weight in given):
        raise ValueError("every fork of a weighted estimate carries a positive weight")
    return given


CHEBYSHEV_95 = math.sqrt(20)  # P(|X - E X| >= k sd) <= 1 / k^2 for any distribution; 1/20 at k = sqrt(20)


def _sampled_effect(forks: list[dict], population: dict) -> dict:
    """The mean effect over a whole population, from forks on a sample of its states.

    Each fork's weight is its state's share of the population divided by the probability with
    which the state was drawn and by the number of forks run on it; `population` gives the
    shares' total over every state, drawn or not, and a bound on the estimate's variance that
    follows from the design alone (`_sampled`). The estimate is the weighted sum over that known
    total (Horvitz-Thompson): its expectation over the draw is the population's mean, which a
    ratio over the drawn weights is not.

    The interval does not come from the drawn units: a stratum that happens not to be drawn says
    nothing about itself, and resampling what was drawn would report certainty. It comes from the
    design. A fork's difference lies in [-1, 1], a state is in the sample with a known
    probability, independently of the others and of what its forks return; so the variance of
    the estimate is at most `variance_bound`, whatever the undrawn states hold, and by
    Chebyshev's inequality the population's mean is within sqrt(20) standard deviations of the
    estimate at least 95 times in 100. It is wide. It is valid for any outcomes, drawn or not,
    and for the forks' own randomness; nothing narrower is claimed here.

    No noise floor and no per-family estimate are defined for a sampled estimate."""
    weights = _weights(forks)
    if population.get("method") not in SAMPLED_METHODS:
        raise ValueError("a sampled estimate is computed by a method the terms name")
    total, bound = population.get("total"), population.get("variance_bound")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0 for x in (total, bound)):
        raise ValueError("a sampled estimate needs the population's total and its variance bound")
    executions = [fork.get(side + "_execution") for fork in forks for side in ("base", "candidate")]
    named = [name for name in executions if name is not None]
    if len(set(named)) != len(named):
        raise ValueError("a sampled estimate does not define the cost of an execution that several forks share")
    mean = sum(w * (fork["candidate"] - fork["base"]) for w, fork in zip(weights, forks)) / total
    half = CHEBYSHEV_95 * math.sqrt(bound)
    per_task: dict = {}
    for w, fork in zip(weights, forks):
        per_task[fork["task"]] = per_task.get(fork["task"], 0.0) + w * (fork["candidate"] - fork["base"])
    known = all(fork.get("base_cost") is not None and fork.get("candidate_cost") is not None for fork in forks)
    base_cost = sum(w * fork["base_cost"] for w, fork in zip(weights, forks)) / total if known else None
    candidate_cost = sum(w * fork["candidate_cost"] for w, fork in zip(weights, forks)) / total if known else None
    return {
        "tasks": len(per_task), "forks": len(forks), "sampled": True, "method": population["method"],
        "mean": mean, "low": mean - half, "high": mean + half,
        # The interval is for the effect over this population. It says nothing about the cost
        # ratio and nothing about tasks outside the population.
        "interval": {"coverage": 0.95, "variance_bound": bound,
                     "sampling_variance_at_most": population.get("sampling_variance_bound"),
                     "assumes": ["each difference lies in [-1, 1]",
                                 "states are drawn independently with the design's probabilities",
                                 "which states are drawn does not depend on what their forks return",
                                 "forks of different states are independent given the states"]},
        "wins": sum(d > 0 for d in per_task.values()), "losses": sum(d < 0 for d in per_task.values()),
        "families": {},
        # Two different things. `cost_*` estimate the mean cost of continuing one state of the
        # population: what a cost criterion compares. `paid_*` are what the sampled executions
        # actually cost, which is not scaled to anything.
        "cost_base": base_cost, "cost_candidate": candidate_cost,
        "cost_ratio": candidate_cost / base_cost if known and base_cost > 0 else None,
        "paid_base": sum(fork["base_cost"] for fork in forks) if known else None,
        "paid_candidate": sum(fork["candidate_cost"] for fork in forks) if known else None,
        "floor": None,
    }


def _spent(forks: list[dict], side: str):
    """A side's cost over its executions, or None when a fork's cost is unknown: an execution
    named by several forks is counted once."""
    if not all(fork.get("base_cost") is not None and fork.get("candidate_cost") is not None for fork in forks):
        return None
    seen, total = set(), 0
    for index, fork in enumerate(forks):
        execution = fork.get(side + "_execution", ("fork", index))
        if execution not in seen:
            seen.add(execution)
            total += fork[side + "_cost"]
    return total


def _two_sided(t: float, df: int) -> float:
    """P(|T| <= t) for Student's t with a whole number of degrees of freedom. For whole numbers it
    is a finite sum (Abramowitz and Stegun 26.7.3 and 26.7.4), evaluated here in floating point."""
    theta = math.atan(t / math.sqrt(df))
    sine, square = math.sin(theta), math.cos(theta) ** 2
    if df % 2:  # (2 / pi) (theta + sin (cos + 2/3 cos^3 + 2.4/3.5 cos^5 + ... up to cos^(df-2)))
        total, term = 0.0, math.cos(theta)
        for j in range((df - 1) // 2):
            total += term
            term *= (2 * j + 2) / (2 * j + 3) * square
        return 2 / math.pi * (theta + sine * total)
    total, term = 0.0, 1.0  # sin (1 + 1/2 cos^2 + 1.3/2.4 cos^4 + ... up to cos^(df-2))
    for j in range(df // 2):
        total += term
        term *= (2 * j + 1) / (2 * j + 2) * square
    return sine * total


def _student_95(df: int) -> float:
    """The point t with P(|T| <= t) = 0.95 for Student's t with `df` degrees of freedom, found by
    halving an interval until no number lies between its ends. A floating-point result: it agrees
    with tabulated points to about one part in 10^15 (tests), and is not claimed to be the same
    bit for bit on every platform."""
    low, high = 0.0, 1.0
    while _two_sided(high, df) < 0.95:
        low, high = high, 2 * high
    while True:
        middle = (low + high) / 2
        if not low < middle < high:
            return high
        low, high = (middle, high) if _two_sided(middle, df) < 0.95 else (low, middle)


# How an estimate over ordered units and its interval are computed, by name and version, as for
# SAMPLED_METHODS: terms name one before any candidate.
SERIES_METHODS = ("ordered-mean/larger-of-plain-and-bartlett/student-t-95/1",)


def _series_design(design) -> dict:
    """A series design, as terms state it before any candidate: the method (`SERIES_METHODS`),
    the roster the estimate is over (by `roster_id`), and how many positions apart two units may
    still move together (`lag`, zero or more)."""
    if isinstance(design, dict) and design.get("method") not in SERIES_METHODS:
        raise ValueError("the series design names no estimation method this module implements ("
                         + ", ".join(SERIES_METHODS) + "): a judgment under it can be read back, not made")
    if (not isinstance(design, dict) or set(design) != {"method", "roster", "lag"}
            or not (isinstance(design["roster"], str) and design["roster"])
            or type(design["lag"]) is not int or design["lag"] < 0):
        raise ValueError("a series design states its method, its roster and its lag, a whole number from zero; "
                         "nothing else")
    return design


def _roster(roster) -> list:
    if not isinstance(roster, list) or not roster or not all(
            isinstance(row, dict) and set(row) == {"unit", "states"} and isinstance(row["unit"], str) and row["unit"]
            and isinstance(row["states"], list) and row["states"]
            and all(isinstance(state, str) and state for state in row["states"]) for row in roster):
        raise ValueError("a roster lists units in their order, each with the states measured in it")
    units, states = [row["unit"] for row in roster], [state for row in roster for state in row["states"]]
    if len(set(units)) != len(units) or len(set(states)) != len(states):
        raise ValueError("a roster lists each unit once and each state once")
    return roster


def roster_id(roster: list) -> str:
    """The identity of a roster: its units in their order and the states measured in each. Terms
    name it before any candidate exists, and whoever holds the roster can show it is that one."""
    return _digest("roster", [[row["unit"], sorted(row["states"])] for row in _roster(roster)])


def _bartlett(points: list[float], mean: float, lag: int) -> float:
    """The variance of a series' mean with covariances up to `lag` positions apart, each weighted
    1 - l / (lag + 1), over b - 1."""
    b = len(points)
    gamma = [math.fsum((points[i] - mean) * (points[i - l] - mean) for i in range(l, b)) / b for l in range(lag + 1)]
    return (gamma[0] + 2 * math.fsum((1 - l / (lag + 1)) * gamma[l] for l in range(1, lag + 1))) / (b - 1)


def _series_effect(forks: list[dict], series: dict) -> dict:
    """The mean effect over units that come in an order, with an interval that lets neighbouring
    units move together.

    `series` is the terms' design with the roster itself in place of its identity. The roster is
    everything the estimate is over: each unit in its order, with the states measured in it. Forks
    of one state are averaged, then the states of a unit, then the units; a unit counts once
    however many states it has, and a state once however often it was continued. Every state of
    the roster has a fork, and every fork is of a state of the roster and is counted under the
    roster's unit for it. A state without a fork is not a missing value to fill: there is no
    estimate.

    With b units, their differences x_1 .. x_b in the roster's order and mean m, two standard
    errors are computed and the larger is used:

      plain     s / sqrt(b), with s the sample standard deviation: what independent units give;
      bartlett  sqrt((g_0 + 2 * sum over l = 1 .. lag of (1 - l / (lag + 1)) * g_l) / (b - 1)),
                with g_l = sum over i > l of (x_i - m) * (x_(i-l) - m) / b: the same when units up
                to `lag` positions apart move together. At lag 0 it is the plain one.

    The interval is the mean plus and minus that standard error times the two-sided 95% point of
    Student's t with b - 1 degrees of freedom. It is a working interval at a nominal level, not a
    coverage guarantee, for few units or for many. With a fixed lag the weights count each
    covariance only in part, so the second standard error stays below the true one even when units
    further apart than the lag are independent and the mean is close to normal: for units
    e_t + e_(t-1) it tends to 7/8 of the true variance at lag 3, and the interval then covers
    about 93.3% in the limit, 94.6% at lag 15. It is also not always wider than resampling the
    units. Terms that name this method get this arithmetic, the same for every candidate. That
    it equals another implementation of the same formula shows the arithmetic; how well its
    level is calibrated for the terms' data is not established here.

    No noise floor is defined for this estimate."""
    if series.get("method") not in SERIES_METHODS:
        raise ValueError("an estimate over a series is computed by a method the terms name")
    lag, roster = series.get("lag"), _roster(series.get("roster"))
    if type(lag) is not int or lag < 0:
        raise ValueError("a series has a lag: a whole number from zero")
    unit_of = {state: row["unit"] for row in roster for state in row["states"]}
    found: dict = {}
    for fork in forks:
        state = fork.get("state")
        if state not in unit_of:
            raise ValueError(f"a fork is of state {state!r}, which the roster does not list")
        if fork["task"] != unit_of[state]:
            raise ValueError(f"state {state} is counted under another unit than the roster lists it under")
        found.setdefault(state, []).append(fork["candidate"] - fork["base"])
    left_out = sorted(set(unit_of) - set(found))
    if left_out:
        raise ValueError(f"{len(left_out)} of {len(unit_of)} states of the roster have no fork: " + ", ".join(left_out[:3]))
    points = [statistics.mean(statistics.mean(found[state]) for state in row["states"]) for row in roster]
    b, mean = len(points), statistics.mean(points)
    interval = low = high = None
    if b > 1:
        plain = statistics.stdev(points) / math.sqrt(b)
        variance = _bartlett(points, mean, lag)
        # Bartlett's weights keep it from being negative; rounding can leave it a hair under zero.
        # Further under is not rounding, and is refused, not read as zero.
        if variance < -1e-15:
            raise ValueError("the Bartlett variance is negative beyond rounding")
        bartlett = math.sqrt(max(0.0, variance))
        error, critical = max(plain, bartlett), _student_95(b - 1)
        low, high = mean - critical * error, mean + critical * error
        interval = {"nominal": 0.95, "plain_se": plain, "bartlett_se": bartlett, "se": error, "lag": lag,
                    "df": b - 1, "critical": critical,
                    # Not `coverage` and not `assumes`: nothing here makes 0.95 hold.
                    "limits": ["0.95 is a nominal level, not a coverage guarantee, for few units or for many",
                               "the roster's order is taken as the order in which units depend on each other",
                               "units more than `lag` positions apart are treated as independent",
                               "within the lag, a fixed lag's weights understate how far units move together"]}
    family: dict = {}
    for fork in forks:
        if family.setdefault(fork["task"], fork.get("family")) != fork.get("family"):
            raise ValueError(f"unit {fork['task']} is given more than one family")
    families: dict = {}
    for row, point in zip(roster, points):
        families.setdefault(str(family[row["unit"]]), []).append(point)
    base_cost, candidate_cost = _spent(forks, "base"), _spent(forks, "candidate")
    return {
        "tasks": b, "forks": len(forks), "method": series["method"],
        "mean": mean, "low": low, "high": high, "interval": interval,
        "wins": sum(point > 0 for point in points), "losses": sum(point < 0 for point in points),
        "families": {name: statistics.mean(values) for name, values in families.items()},
        "cost_base": base_cost, "cost_candidate": candidate_cost,
        "cost_ratio": candidate_cost / base_cost if base_cost is not None and base_cost > 0 else None,
        "floor": None,
    }


def effect(forks: list[dict], null: list[dict] | None = None, *, resamples: int = 2000, seed: int = 0,
           population: dict | None = None, series: dict | None = None) -> dict:
    """Measured effect of the one input that differs between two continuations of the same state.

    A fork is {"task", "family"?, "base", "candidate", "base_cost"?, "candidate_cost"?}: the values
    reached from one state without and with the change, and the cost of each side in one unit, or
    None when unknown. Tasks are the independent units: forks of a task are averaged and tasks are
    resampled. With fewer than two tasks there is no interval. `null` holds forks whose two sides
    had the same input; `floor` is computed from them for this effect's own statistic. A fork may
    name its sides' executions ("base_execution", "candidate_execution"); the cost of an execution
    that several forks name is then counted once.

    Forks run on a sample of a larger population carry a "weight" each, and `population` gives
    that population's total and variance bound; the estimate and its interval are then for the
    whole population (`_sampled_effect`). Weights and `population` come together or not at all.
    Without them the forks are all there is: every task counts once, its forks equally, and tasks
    are resampled.

    When the units come in an order and may depend on their neighbours, `series` gives the method,
    the lag and the roster, each fork names the "state" it continues, and the estimate and its
    interval are those of `_series_effect`. It is not combined with a sample.
    """
    if series is not None:  # before anything else: a roster with no forks at all is incomplete, not empty
        if population is not None or _weights(forks) is not None:
            raise ValueError("an estimate is over a sample or over a series, not both")
        if null:
            raise ValueError("no noise floor is defined for an estimate over a series")
        return _series_effect(forks, series)
    if not forks:
        return {"tasks": 0}
    if (_weights(forks) is None) != (population is None):
        raise ValueError("a sampled estimate has both its forks' weights and its population; another has neither")
    if population is not None:
        if null:
            raise ValueError("no noise floor is defined for a sampled estimate")
        return _sampled_effect(forks, population)
    per_task = _means(forks)
    differences = list(per_task.values())
    draws = _resampled(differences, resamples, seed) if len(differences) > 1 else None
    families: dict = {}
    for fork in forks:
        families.setdefault(fork.get("family"), []).append(fork)
    base_cost, candidate_cost = _spent(forks, "base"), _spent(forks, "candidate")
    known = base_cost is not None
    return {
        "tasks": len(differences),
        "mean": sum(differences) / len(differences),
        "low": draws[int(0.025 * resamples)] if draws else None,
        "high": draws[min(resamples - 1, int(0.975 * resamples))] if draws else None,
        "wins": sum(d > 0 for d in differences), "losses": sum(d < 0 for d in differences),
        "families": {str(name): sum(_means(group).values()) / len(_by_task(group))
                     for name, group in families.items()},
        # Known costs are kept as they are: zero on both sides is known and equal, not unknown.
        "cost_base": base_cost, "cost_candidate": candidate_cost,
        "cost_ratio": candidate_cost / base_cost if known and base_cost > 0 else None,
        "floor": _floor(forks, null, resamples, seed + 1) if null else None,
    }


ESTIMANDS = ("state_mean", "task_mean")
# How a sampled estimate and its interval are computed, by name and version. Terms name one
# before any candidate, so it is part of every judgment's identity: a judgment begun under one
# method is finished under that method or not at all, never under another that shares its inputs.
SAMPLED_METHODS = ("horvitz-thompson/chebyshev-95/1",)


def _design(design) -> dict:
    """A sampling design, as terms state it before any candidate: what is estimated, by which
    method (`SAMPLED_METHODS`), over which population (by the identity of its states and units,
    `population_id`), and for each stratum the probability, above zero, with which one of its
    states is drawn. It holds no seed: the random draw is made by the host once the population and
    every state's stratum are fixed."""
    strata = design.get("strata") if isinstance(design, dict) else None
    if isinstance(design, dict) and design.get("method") not in SAMPLED_METHODS:
        raise ValueError("the sampling design names no estimation method this module implements ("
                         + ", ".join(SAMPLED_METHODS) + "): a judgment under it can be read back, not made")
    if (not isinstance(design, dict) or set(design) != {"estimand", "method", "population", "strata"}
            or design["estimand"] not in ESTIMANDS
            or not (isinstance(design["population"], str) and design["population"])
            or not isinstance(strata, dict) or not strata
            or not all(isinstance(name, str) and name and isinstance(p, (int, float)) and not isinstance(p, bool)
                       and 0 < p <= 1 for name, p in strata.items())):
        raise ValueError("a sampling design states its estimand (" + " or ".join(ESTIMANDS) + "), its method, its "
                         "population, and for each stratum a probability above zero and at most one; nothing else")
    return design


def _frame(frame) -> list:
    if not isinstance(frame, list) or not all(
            isinstance(row, dict) and all(isinstance(row.get(name), str) and row[name] for name in ("state", "unit", "stratum"))
            for row in frame):
        raise ValueError("a frame lists each state with its unit and stratum")
    states = [row["state"] for row in frame]
    if len(set(states)) != len(states):
        raise ValueError("a frame lists each state once")
    return frame


def population_id(frame: list) -> str:
    """The identity of a population: its states and the unit each belongs to, whatever stratum a
    comparison later puts them in. Terms name it before any candidate exists."""
    return _digest("population", sorted([row["state"], row["unit"]] for row in _frame(frame)))


def _draw(seed: str, state: str) -> float:
    """A number in [0, 1) fixed by a design's seed and a state's identity: nobody chooses it."""
    return int(hashlib.sha256((seed + "\x00" + state).encode()).hexdigest()[:13], 16) / 16 ** 13


def _shares(design: dict, frame: list[dict]) -> dict:
    """Each state's share of what is estimated: one for the mean over states; one over the number
    of its unit's states for the mean over units of each unit's own mean."""
    size: dict = {}
    for row in frame:
        size[row["unit"]] = size.get(row["unit"], 0) + 1
    return {row["state"]: 1.0 if design["estimand"] == "state_mean" else 1.0 / size[row["unit"]] for row in frame}


def population(design: dict, frame: list[dict]) -> dict:
    """What a design and a frame fix before anything is drawn or run: the total of the states'
    shares, and a bound on the variance of the estimate that holds whatever the states' effects
    are. A state with share a, drawn with probability p, contributes at most a * a / p: its fork's
    difference lies in [-1, 1], so its squared value is at most one. `sampling_variance_bound` is
    the part that would remain if every state's effect were known exactly, a * a * (1 / p - 1)."""
    _design(design)
    shares = _shares(design, _frame(frame))
    total = sum(shares.values())
    probability = {row["state"]: design["strata"][row["stratum"]] for row in frame}
    return {"method": design["method"], "total": total,
            "variance_bound": sum(a * a / probability[state] for state, a in shares.items()) / total ** 2,
            "sampling_variance_bound": sum(a * a * (1 / probability[state] - 1) for state, a in shares.items()) / total ** 2}


def sample(design: dict, frame: list[dict], seed: str) -> dict:
    """The states of a frame that a design draws under one seed, each with the weight it stands
    for.

    `frame` lists the whole population once: {"state", "unit", "stratum"} for every state, drawn
    or not, valid or not. A state is drawn when a number fixed by the seed and the state's
    identity is under its stratum's probability, so the sample can be recomputed by anyone. That
    makes it checkable, not unchosen: whoever could set a state's stratum or identity after
    learning the seed could steer it. The seed therefore belongs to a draw the host makes once,
    after the population and every stratum are fixed (`_sampled`). A drawn state's weight is its
    share of what is estimated (`_shares`) over the probability with which it was drawn."""
    _design(design)
    _frame(frame)
    if not (isinstance(seed, str) and seed):
        raise ValueError("a draw has a seed")
    if population_id(frame) != design["population"]:
        raise ValueError("the frame is not the population the design names")
    unknown = sorted({row["stratum"] for row in frame} - set(design["strata"]))
    if unknown:
        raise ValueError("the design gives no probability for stratum " + ", ".join(unknown))
    shares = _shares(design, frame)
    return {row["state"]: shares[row["state"]] / design["strata"][row["stratum"]] for row in frame
            if _draw(seed, row["state"]) < design["strata"][row["stratum"]]}


def met(criterion: dict, measured: dict) -> list[str]:
    """Reasons a measured effect does not meet a criterion; empty when it does.

    The criterion is data in an improvement's terms, written before any candidate exists. Every
    key is optional: min_tasks, min_effect, above_noise, lower_bound, families_nonnegative,
    max_cost_ratio (in the terms' cost_unit). This function compares; it sets no threshold.
    """
    reasons = []
    if measured.get("tasks", 0) < criterion.get("min_tasks", 1):
        return ["too few tasks"]
    if "min_effect" in criterion and measured["mean"] < criterion["min_effect"]:
        reasons.append("effect below the stated minimum")
    if criterion.get("above_noise") and (measured["floor"] is None or measured["mean"] <= measured["floor"]):
        reasons.append("effect not above the noise of identical continuations")
    if "lower_bound" in criterion and (measured["low"] is None or measured["low"] <= criterion["lower_bound"]):
        reasons.append("interval reaches the stated bound or is undefined")
    if criterion.get("families_nonnegative") and any(m < 0 for m in measured["families"].values()):
        reasons.append("negative in a task family")
    if "max_cost_ratio" in criterion:
        base, candidate = measured.get("cost_base"), measured.get("cost_candidate")
        if base is None or candidate is None:
            reasons.append("cost unknown in the stated unit")
        elif candidate > criterion["max_cost_ratio"] * base:
            reasons.append("cost above the stated ratio")
    return reasons


# ------------------------------------------------------------------ the journal


def _events(source) -> list[dict]:
    return kernel.events(source) if isinstance(source, (str, Path)) else source


def _of(events, kind: str):
    for row in _events(events):
        if row["event"]["kind"] == kind:
            yield row["seq"], row["event"]["body"]


def state(store: Path, identities: dict, contract_id: str, subject: str, *, task: str | None = None,
          family: str | None = None, parent: int | None = None, inputs: dict | None = None,
          receipt: str | None = None, by: dict | None = None, cost: dict | None = None,
          measurement_unit: str | None = None) -> int:
    """Record a frozen state of a Contract's record. `parent` is the journal sequence of the state
    it was continued from when that is not the record's previous state, as in a fork; `inputs`
    names what the continuation was given (version, action, context), by digest; `receipt` is the
    digest of the harness's own record of that execution: what it restored, ran and closed.

    `task` and `family` say which unit a statistic counts the state under. `measurement_unit` says
    which of a measure's units the state is measured as, when the measure has units. The two are
    different things: several questions of one date are measured each on its own cases and
    counted as one date."""
    body = {"record": contract_id, "subject": subject, "task": task, "family": family, "parent": parent,
            "inputs": inputs or {}, "receipt": receipt, "by": by or {}, "cost": cost or {}}
    if measurement_unit is not None:
        if not (isinstance(measurement_unit, str) and measurement_unit):
            raise ValueError("a measurement unit is named by a non-empty string")
        body["measurement_unit"] = measurement_unit
    return kernel.append(store, "capture", identities, body)["seq"]


def record(events, contract_id: str) -> list[dict]:
    """The frozen states written for one Contract, in order, each with its journal sequence."""
    return [dict(body, seq=seq) for seq, body in _of(events, "capture") if body.get("record") == contract_id]


SCALES = ("score",)  # absent: each case passes or fails


def _case_set(entry) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get("cases"), str) and bool(entry["cases"])
            and isinstance(entry.get("total"), int) and not isinstance(entry["total"], bool) and entry["total"] > 0)


def measure_id(registration: dict) -> str:
    """A measure is registered once: the pool its cases come from (an identity of their content
    and source), its evaluator, its environment, and its complete case set. `includes` may name
    pools whose cases this pool contains.

    The case set is either one for every state (`cases`, the digest of the case names, and
    `total`), or one for each unit (`units`: a unit's id to its own `cases` and `total`), as when
    every task has its own tests or every question its own resolution. `scale` says what a case's
    result is: absent, true or false; "score", a number from 0 to 1 where more is better. How a
    raw quantity becomes that number belongs to the registered evaluator."""
    if not {"pool", "evaluator", "environment"} <= set(registration):
        raise ValueError("a measure names its pool, its complete case set, its evaluator and its environment")
    one, each = {"cases", "total"} <= set(registration), "units" in registration
    if one == each or (not one and ({"cases", "total"} & set(registration))):
        raise ValueError("a measure names its pool, its complete case set, its evaluator and its environment: "
                         "one case set for every state, or one for each unit")
    if each and not (isinstance(registration["units"], dict) and registration["units"]
                     and all(isinstance(unit, str) and unit and _case_set(entry)
                             for unit, entry in registration["units"].items())):
        raise ValueError("a measure by unit gives each unit its case set and its number of cases")
    if "scale" in registration and registration["scale"] not in SCALES:
        raise ValueError("unknown scale: " + repr(registration["scale"]))
    if "includes" in registration and not (isinstance(registration["includes"], list) and all(
            isinstance(pool, str) and pool for pool in registration["includes"])):
        raise ValueError("includes is a list of pools")
    return _digest("measure", registration)


def case_set(names) -> str:
    return _digest("cases", sorted(names))


def check_outcome(registration: dict, outcome, unit: str | None = None) -> dict:
    """The outcome, if it is a result for each of the measure's registered cases and for no other.

    Under a measure by unit the outcome names the unit it measured, the case set is that unit's,
    and `unit`, when given, is the unit of the state the outcome is read for: an outcome of
    another unit is refused. Each result is exactly true or false, or under scale "score" a
    number from 0 to 1. A case with no definite result makes the outcome not a measurement of
    this measure; it is refused, not counted either way. Used wherever an outcome is written or
    read."""
    results = outcome.get("cases") if isinstance(outcome, dict) else None
    expected = registration
    if "units" in registration:
        named = outcome.get("unit") if isinstance(outcome, dict) else None
        if named not in registration["units"] or not isinstance(named, str):
            raise ValueError("the outcome names no unit of the measure")
        if unit is not None and named != unit:
            raise ValueError(f"the outcome is of unit {named}, not of the state's unit {unit}")
        expected = registration["units"][named]
    if (not isinstance(results, dict) or len(results) != expected["total"]
            or not all(isinstance(name, str) for name in results) or case_set(results) != expected["cases"]):
        raise ValueError("the outcome is not over the measure's registered cases")
    if registration.get("scale") == "score":
        if not all(_score(result) for result in results.values()):
            raise ValueError("each case's result is a score from 0 to 1")
    elif not all(result is True or result is False for result in results.values()):
        raise ValueError("each case's result is exactly true or false")
    return outcome


def _pools(registration: dict) -> list:
    return [registration["pool"], *registration.get("includes", [])]


def _overlapping(events, registration: dict) -> list:
    """Every pool whose cases are, wholly or in part, cases of this measure's pool, as far as the
    store's own record says: the pool, the pools it includes, the pools those include, and every
    pool that terms retained here register as including one of them. Reading a larger pool's
    cases is reading the cases it includes, whichever registration the judgment is made under.

    That two pools are both registered under a larger one does not show that they share a case,
    so such a pool is not among them; a pool that includes something this pool also includes is.
    An overlap that no terms register is the host's to state. An inclusion that some writer
    appended without cause can only add pools, which can only put cases within reach, never
    beyond it."""
    includes: dict = {}
    for _, body in _of(events, "issue"):
        terms = body.get("terms")
        measure = terms.get("criterion", {}).get("measure") if isinstance(terms, dict) and isinstance(
            terms.get("criterion"), dict) else None
        if isinstance(measure, dict) and isinstance(measure.get("pool"), str) and isinstance(measure.get("includes"), list):
            includes.setdefault(measure["pool"], set()).update(pool for pool in measure["includes"] if isinstance(pool, str))
    includes.setdefault(registration["pool"], set()).update(registration.get("includes", []))
    within, queue = [], [registration["pool"]]
    while queue:  # what this pool contains
        pool = queue.pop(0)
        if pool not in within:
            within.append(pool)
            queue.extend(sorted(includes.get(pool, ())))
    around, grew = set(), True
    while grew:  # what contains any of that
        grew = False
        for pool, parts in includes.items():
            if pool not in within and pool not in around and parts & (set(within) | around):
                around.add(pool)
                grew = True
    return within + sorted(around)


def measure(store: Path, registration: dict, subject: str, outcome: dict, *, attempt: str | None = None,
            receipt: str | None = None) -> int:
    """Retain what the registered evaluator found for one frozen subject. Running the evaluator is
    the host's measurement capability; this binds its result to the subject, the evaluator, the
    environment and the measure's complete case set. The outcome keeps each case's result, and
    one over other cases is refused: an equal count is not the same measure. Under a measure by
    unit the outcome names the unit it measured. `receipt` names the host's own receipt of that
    measurement, which a judgment under a receipt protocol reads."""
    check_outcome(registration, outcome)
    body = {"subject": subject, "measure": measure_id(registration), "environment": registration["environment"],
            "attempt": attempt, "outcome": outcome}
    if receipt is not None:  # the host's measurement receipt, by its object id; `attempt` keeps its meaning
        if not (isinstance(receipt, str) and receipt):
            raise ValueError("a measurement's receipt is referenced by a non-empty id")
        body["receipt"] = receipt
    return kernel.append(store, "label", kernel.identities(evaluator_epoch=registration["evaluator"]), body)["seq"]


def outcomes(events, registration: dict) -> dict:
    """Subject -> {receipt sequence: outcome} under one registered measure. Only labels written
    by the registered evaluator in the registered environment count."""
    wanted, found = measure_id(registration), {}
    for row in _events(events):
        event = row["event"]
        if (event["kind"] == "label" and event["body"].get("measure") == wanted
                and event["identities"].get("evaluator_epoch") == registration["evaluator"]
                and event["body"].get("environment") == registration["environment"]):
            found.setdefault(event["body"]["subject"], {})[row["seq"]] = event["body"]["outcome"]
    return found


def expose(store: Path, view: str, pool: str, grain: str = "cases", request: str | None = None) -> int:
    """Record that a view has read a pool's cases, or one verdict drawn from them. Exposure only
    accumulates and belongs to the cases, not to a measure's registration: registering the same
    cases under another evaluator or environment does not make them unread. A view is whoever
    could shape a candidate: its author and who selected it, people included. A verdict is
    charged once per judgment, named by `request`."""
    with _exclusive(store):
        if request is not None:
            for seq, body in _of(store, "exposure"):
                if (body.get("view"), body.get("pool"), body.get("request")) == (view, pool, request):
                    return seq
        return kernel.append(store, "exposure", kernel.identities(),
                             {"view": view, "pool": pool, "grain": grain, "request": request})["seq"]


def verdicts(events, pools) -> int:
    """How many judgments have drawn a verdict from any of `pools`, whoever shaped what they
    judged. A judgment counts once: by its retained statement, and by the verdict it charged its
    views, whichever is in the journal. A verdict recorded without a judgment counts by itself."""
    pools = [pools] if isinstance(pools, str) else list(pools)
    judged, other = set(), 0
    for _, body in _of(events, "exposure"):
        if body.get("pool") in pools and body.get("grain") == "verdict":
            if body.get("request") is None:
                other += 1
            else:
                judged.add(body["request"])
    replaced = set()
    for _, body in _of(events, "insight"):
        if body.get("pool") in pools and isinstance(body.get("request"), str) and "unmet" in body:
            judged.add(body["request"])
            replaced.add(body.get("replaces"))  # a judgment and the one made in its place are one verdict
    return len(judged - replaced) + other


def _seen(events, view: str, pools: list) -> dict:
    """What one view has drawn from any of `pools`: whether it read their cases, and how many
    verdicts. Every verdict charged to the view counts, and so does the retained statement of a
    judgment on a candidate the view shaped that has not charged it yet: a judgment interrupted
    before it concluded has still given its verdict, and whoever shaped the candidate can read
    it."""
    grains, charges, charged = [], [], set()
    for _, body in _of(events, "exposure"):
        if body.get("view") == view and body.get("pool") in pools:
            grains.append(body.get("grain"))
            if body.get("grain") == "verdict":
                charges.append(body.get("request"))
                if body.get("request") is not None:
                    charged.add(body["request"])
    retained, replaced = set(), set()
    for _, body in _of(events, "insight"):
        if (body.get("pool") in pools and isinstance(body.get("request"), str) and "unmet" in body
                and isinstance(body.get("exposure"), dict) and view in body["exposure"]):
            retained.add(body["request"])
            replaced.add(body.get("replaces"))  # a judgment and the one made in its place are one verdict
    # A conclusion may have charged some views before it was interrupted. When its judgment
    # is replaced, those old charges stay recorded but are not another verdict for the view.
    # Unkeyed legacy charges retain their original count.
    counted_charges = sum(request is None or request not in replaced for request in charges)
    return {"cases": "cases" in grains, "verdicts": counted_charges + len(retained - charged - replaced)}


def reach(events, views, pools, max_verdicts: int | None = None, max_pool_verdicts: int | None = None) -> str:
    """Where cases lie for a candidate shaped by `views` (its author and everyone who selected
    it): within once any of them has read the cases of any of `pools`, or has drawn as many
    verdicts from them as the terms allow; beyond otherwise. A new author does not clear what a
    selector has read.

    `max_verdicts` counts by view, which is enough when views share nothing. When one judgment's
    result can reach the next candidate's shapers by any other way (an adoption they build on, a
    shared note), the terms state `max_pool_verdicts` instead or as well: the cases are within
    reach once the pools have given that many verdicts to anyone (`verdicts`). That count does
    not depend on how a view is named."""
    pools = [pools] if isinstance(pools, str) else list(pools)
    if max_pool_verdicts is not None and verdicts(events, pools) >= max_pool_verdicts:
        return "within"
    for view in [views] if isinstance(views, str) else views:
        seen = _seen(events, view, pools)
        if seen["cases"] or (max_verdicts is not None and seen["verdicts"] >= max_verdicts):
            return "within"
    return "beyond"


# ------------------------------------------------------------------ the native task journal (read-only)

NATIVE_PHASES = ("progress_snapshot", "interrupted_snapshot")


def native_record(events, contract_id: str) -> list[dict]:
    """States the native task harness captured for one Contract, in order, for `ledger`.

    Each state is one occurrence, identified by the journal row it came from, of an artifact,
    identified by its subject. Only captures that name the Contract are used; a delivery capture
    without one is not guessed into a record (`native_coverage` counts them). The native capture
    carries no cost, so cost is left empty: unknown, not zero.
    """
    states = []
    for row in _events(events):
        event = row["event"]
        body = event["body"]
        if event["kind"] == "capture" and body.get("phase") in NATIVE_PHASES and body.get("contract_id") == contract_id:
            identities = event["identities"]
            states.append({
                "id": row["seq"], "subject": body["subject_hash"], "cost": {},
                "by": {"harness": identities.get("harness"), "model": identities.get("model"),
                       "effort": identities.get("effort"), "phase": body["phase"],
                       "turn": body.get("turn_id"), "epoch": body.get("execution_epoch")},
                "source": {"seq": row["seq"], "hash": row.get("hash")}})
    return states


def native_outcomes(events, contract_id: str, policy: str = "as_observed") -> dict:
    """In-run sealed tallies of one Contract's verifications, grouped by the conditions they were
    taken under: (evaluator, environment, number of qualified cases) -> {occurrence id: outcome}.

    Under "as_observed" a tally belongs to the occurrence it was taken on: the latest capture of
    that subject at or before the verification. The same artifact reached again later keeps its
    own later tally. Under "corrected" every occurrence of an artifact takes that artifact's
    latest tally: a re-estimate, not what was observed at the time. Each outcome names the
    verification it came from.

    States are comparable only inside one group. Equal counts do not prove equal cases, and the
    tally is the run's own evaluation, not an outside measure. The tie between a tally and an
    occurrence is inferred, not recorded by the harness, and every outcome carries that label.
    This is for reading a record; it is never the measurement identity of a judgment or a fork,
    which take only measurements written by `measure` for an exact recorded state.
    """
    if policy not in ("as_observed", "corrected"):
        raise ValueError("policy is as_observed or corrected")
    occurrences = [(state["id"], state["subject"]) for state in native_record(events, contract_id)]
    groups: dict = {}
    for row in _events(events):
        event = row["event"]
        body = event["body"]
        sealed, receipts = body.get("sealed") or {}, body.get("receipts") or {}
        if event["kind"] == "verification" and body.get("contract_id") == contract_id and sealed.get("qualified"):
            key = (receipts.get("evaluator_digest"), receipts.get("environment_digest"), sealed["qualified"])
            # The native journal gives no key from a verification to the capture it measured.
            # The tie below is inferred from subject and order, and says so.
            outcome = {"passed": sealed["passed"], "total": sealed["qualified"], "source": row["seq"],
                       "association": "inferred: latest capture of the same subject at or before the tally"
                       if policy == "as_observed" else "re-estimate: the artifact's latest tally"}
            same = [seq for seq, subject in occurrences if subject == body["subject_hash"]]
            if policy == "corrected":
                targets = same
            else:
                earlier = [seq for seq in same if seq <= row["seq"]]
                targets = earlier[-1:]
            for target in targets:
                groups.setdefault(key, {})[target] = outcome
    return groups


def native_coverage(events, contract_id: str) -> dict:
    """How much of a native journal the read-only projection covers for one Contract: captures
    that name it, captures that name no Contract and so were not attached to any, and
    verifications with a tally that found an occurrence to belong to."""
    captures = [row["event"]["body"] for row in _events(events) if row["event"]["kind"] == "capture"]
    occurrences = native_record(events, contract_id)
    tallies = [row for row in _events(events) if row["event"]["kind"] == "verification"
               and row["event"]["body"].get("contract_id") == contract_id
               and (row["event"]["body"].get("sealed") or {}).get("qualified")]
    attached = sum(any(state["subject"] == row["event"]["body"]["subject_hash"] and state["id"] <= row["seq"]
                       for state in occurrences) for row in tallies)
    return {"captures": len(captures), "bound": len(occurrences),
            "unbound": sum(1 for body in captures if not body.get("contract_id")),
            "tallies": len(tallies), "tallies_attached": attached, "tallies_unattached": len(tallies) - attached,
            "association": "inferred from subject and order; the native journal records no exact tie"}


# ------------------------------------------------------------------ improvements in the kernel


def _apply(store: Path, key: str, current: dict | None, contract_id: str, role: str, provenance: str,
           command: dict) -> dict:
    """Apply one command conditional on the Contract as it was observed. The idempotency key
    carries a digest of the whole command, so the same command repeated is one command and two
    different commands on the same version never collide: the kernel decides between them."""
    request = {"contract_id": contract_id, "expected_version": current["version"] if current else 0,
               "role": role, "provenance": provenance, "command": command}
    return kernel.apply(store, f"{key}:{_digest('command', request)[:16]}", request)


def _check(terms: dict) -> None:
    if not {"scope", "defect", "evidence", "criterion"} <= set(terms) or "measure" not in terms["criterion"]:
        raise ValueError("terms need scope, defect, evidence and a criterion with its measure")
    if "version" in terms:
        raise ValueError("terms are written before candidates; they name no version")
    measure_id(terms["criterion"]["measure"])
    criterion = terms["criterion"]
    declared = [name for name in ("differs", "tolerated", "shared_executions", "sampling", "series") if name in criterion]
    if "receipt_protocol" not in criterion:
        if declared:
            raise ValueError("differs, tolerated, shared_executions, sampling and series are read only under a "
                             "receipt protocol the terms name")
    else:
        if criterion["receipt_protocol"] not in RECEIPT_PROTOCOLS:
            raise ValueError("unknown receipt protocol: " + repr(criterion["receipt_protocol"]))
        for name in ("differs", "tolerated"):
            names = criterion.get(name)
            if not isinstance(names, list) or not all(isinstance(item, str) and item for item in names) \
                    or len(set(names)) != len(names):
                raise ValueError(f"under a receipt protocol the terms state {name} as a list of names")
        if "version" not in criterion["differs"]:
            raise ValueError("a candidate is a version: the inputs a fork may differ in include the version")
        if type(criterion.get("shared_executions", False)) is not bool:
            raise ValueError("shared_executions is true or false")
        if "sampling" in criterion:
            _design(criterion["sampling"])
            undefined = [name for name in ("shared_executions", "above_noise", "families_nonnegative") if criterion.get(name)]
            if undefined:
                raise ValueError("a sampled estimate does not define: " + ", ".join(undefined))
        if "series" in criterion:
            _series_design(criterion["series"])
            if "sampling" in criterion:
                raise ValueError("an estimate is over a sample or over a series, not both")
            if criterion.get("above_noise"):
                raise ValueError("an estimate over a series does not define: above_noise")
    if "max_pool_verdicts" in criterion and (type(criterion["max_pool_verdicts"]) is not int
                                             or criterion["max_pool_verdicts"] < 1):
        raise ValueError("max_pool_verdicts is a whole number, at least one")
    # Optional: "convention", under which the principal lets a met criterion be presumed accepted,
    # and, on a scope's installation, "restore". Present means a non-empty name.
    for name in ("convention", "restore"):
        if terms.get(name) is not None and not (isinstance(terms[name], str) and terms[name].strip()):
            raise ValueError(f"{name} names a convention the principal chose: a non-empty string or absent")


def _bound(terms: dict) -> tuple:
    """The terms' exact digest input, retained as a receipt, and the kernel bindings it gives."""
    binding = kernel.json_binding("terms", _canonical(terms))
    return binding, {"terms_hash": binding["digest"],
                     "capture_policy_hash": _digest("capture_policy", "version"),
                     "evidence_policy_hash": _digest("evidence_policy", terms["criterion"])}


def _terms(events, contract: dict) -> dict:
    """The terms this Contract is bound to now, reproduced from their retained digest input. A
    revision replaces them; terms that do not reproduce the Contract's hash are not used."""
    for _, body in reversed(list(_of(events, "issue"))):
        binding = body.get("binding") or {}
        if body.get("contract") == contract["id"] and binding.get("digest") == contract["bindings"]["terms_hash"]:
            terms = json.loads(binding["input_json"])
            if kernel.binding_matches(binding, "terms", terms):
                return terms
    raise ValueError("no retained terms reproduce this Contract's terms hash")


def _proposal(events, contract_id: str, version: str, before: int | None = None) -> dict:
    """The latest registration of a version as this Contract's candidate, with its sequence."""
    found = [dict(body, seq=seq) for seq, body in _of(events, "version")
             if body.get("contract") == contract_id and body.get("version") == version
             and (before is None or seq < before)]
    if not found:
        raise ValueError("version was never put forward on this Contract")
    return found[-1]


def _statements(events, contract_id: str) -> list[dict]:
    return [dict(body, seq=seq) for seq, body in _of(events, "insight") if body.get("contract") == contract_id]


def _retain(store: Path, contract: dict, terms: dict, binding: dict, identities: dict | None) -> None:
    if not any(body.get("contract") == contract["id"] and (body.get("binding") or {}).get("digest") == binding["digest"]
               for _, body in _of(store, "issue")):
        kernel.append(store, "issue", identities or kernel.identities(), {
            "contract": contract["id"], "revision": contract["revision"], "terms": terms, "binding": binding})


def locate(store: Path, owner: str, terms: dict, identities: dict | None = None) -> dict:
    """Make a defect the strategy chose to act on a responsibility: issue its improvement Contract.

    Terms name the scope, the defect, the evidence that located it and the criterion a candidate
    must meet under a registered measure. They name no version, so they can only precede
    candidates. Calling it again with the same terms returns the same Contract.
    """
    _check(terms)
    binding, bindings = _bound(terms)
    contract_id = f"improvement.{binding['digest'][:32]}"
    with _exclusive(store):
        current = kernel.contract(store, contract_id)
        if current is None:
            current = _apply(store, f"issue:{contract_id}", None, contract_id, "issuer", "delegate",
                             {"type": "issue", "owner": owner, "bindings": bindings})
        _retain(store, current, terms, binding, identities)
        return current


def revise(store: Path, contract_id: str, terms: dict, identities: dict | None = None) -> dict:
    """The principal changes what an improvement must meet. The kernel drops the candidate and its
    support; every later judgment reads these terms and no earlier ones."""
    _check(terms)
    binding, bindings = _bound(terms)
    with _exclusive(store):
        contract = kernel.contract(store, contract_id)
        if contract["bindings"]["terms_hash"] != binding["digest"]:
            contract = _apply(store, f"revise:{contract_id}:{binding['digest']}", contract, contract_id,
                              "issuer", "human", {"type": "revise", "bindings": bindings})
        _retain(store, contract, terms, binding, identities)
        return contract


def propose(store: Path, contract_id: str, version: str, *, base: str | None, change: str, author: str,
            selectors: tuple = (), identities: dict | None = None) -> dict:
    """Put one change to the incumbent forward as the improvement's candidate. The registration
    names the exact acceptance the change was built on, not only that version's content, and
    every view that shaped the candidate: its author and whoever selected it among alternatives."""
    with _exclusive(store):
        events, serving_now = kernel.events(store), incumbent(store)
        contract = kernel.contract(store, contract_id)
        if serving_now["version"] is None and serving_now["seq"] is not None:
            raise ValueError("no version serves this scope; select one before proposing a change to it")
        if base != serving_now["version"]:
            raise ValueError("a candidate is one change to the incumbent; rebuild it on the current one")
        candidate = contract["candidate"]
        views = sorted({author, *selectors})
        registration = {"version": version, "base": base, "on": serving_now["acceptance"], "change": change,
                        "author": author, "views": views, "contract": contract_id}
        if candidate and candidate["subject_hash"] == version and contract["standing"] == "outstanding":
            earlier = _proposal(events, contract_id, version)
            if earlier["on"] == serving_now["acceptance"]:
                earlier.pop("seq")
                if earlier == registration:
                    return contract  # a repeated call after a lost acknowledgement
                if dict(earlier, views=views) != registration or not set(earlier["views"]) <= set(views):
                    raise ValueError("this version is already the candidate under another registration")
                if contract["support"] is not None:
                    raise ValueError("this candidate was already judged; its views cannot change")
                # The same candidate with more selectors disclosed: views grow, nothing is dropped.
                kernel.append(store, "version", identities or kernel.identities(), registration)
                return contract
        kernel.append(store, "version", identities or kernel.identities(), registration)
        return _apply(store, f"propose:{contract_id}:{version}:{contract['version']}", contract, contract_id,
                      "executor", "automation", {"type": "propose", "subject_hash": version})


def install(store: Path, owner: str, scope: str, version: str, author: str, *, authorization: str,
            restore_convention: str | None = None) -> dict:
    """The principal's explicit choice of the first version for a scope. It claims no measured
    effect, so its Support lies within reach and only the principal's act settles it.
    `authorization` is the host's receipt of that act. `restore_convention`, when the principal
    names one here, is what later lets a standing acceptance be selected again without asking."""
    if _receipt(authorization, "authorization") is None:
        raise ValueError("authorization is the host's receipt of the principal's act: a non-empty string")
    registration = {"pool": _digest("pool", "none"), "cases": case_set([]), "total": 0,
                    "evaluator": _digest("evaluator", owner), "environment": _digest("environment", scope)}
    terms = {"scope": scope, "defect": "no version serves this scope", "evidence": [],
             "criterion": {"measure": registration}, "restore": restore_convention}
    with _exclusive(store):
        contract_id = locate(store, owner, terms)["id"]
        contract = propose(store, contract_id, version, base=None, change="initial", author=author)
        if contract["standing"] == "outstanding" and contract["support"] is None:
            candidate = contract["candidate"]
            coordinate = {
                "contract_id": contract_id, "revision": contract["revision"], **contract["bindings"],
                "generation": candidate["generation"], "subject_hash": version,
                "environment_digest": registration["environment"], "evaluator_digest": registration["evaluator"],
                "evidence_hash": _digest("evidence", [owner, scope, version]),
                "basis": _digest("basis", {"receipts": [], "executions": [], "on": None}), "reach": "within"}
            _apply(store, f"support:{contract_id}:install", contract, contract_id, "verifier", "automation", {
                "type": "support", "coordinate": coordinate, "certificate": _digest("certificate", coordinate)})
        return adopt(store, contract_id, explicit=authorization)


def _chosen(measured: dict, row: dict, pinned: dict, registration: dict) -> tuple:
    """The measurement of one state that a judgment uses: the receipt the caller pinned, or the
    latest retained. Returns (outcome, receipt sequence).

    The one chosen is checked here, where it is used: a label can have been written by an
    earlier writer that accepted other values, and such a label is not a result. It is refused by
    name, with the reason, and stays in the journal as it was; another retained measurement of
    the state can be pinned instead. Labels of other states are not read. Under a measure by
    unit, "latest" is among the labels of the unit the state is measured as."""
    retained = measured.get(row["subject"])
    if not retained:
        raise ValueError("a fork's side has no outcome under the criterion's measure")
    expected, latest = None, max(retained)
    if "units" in registration:  # which cases apply is the state's measurement unit, not the unit it is counted under
        expected = row.get("measurement_unit")
        if not (isinstance(expected, str) and expected):
            raise ValueError(f"under a measure by unit, state {row['seq']} names the unit it is measured as")
        # One frozen subject can be measured as several units. The latest of this state's is the
        # latest that names its unit. A newer label that names no unit of the measure is not
        # passed over: it is refused, like any chosen label that is not a result.
        named = {seq: outcome.get("unit") if isinstance(outcome, dict) else None for seq, outcome in retained.items()}
        own = [seq for seq, unit in named.items() if unit == expected]
        unnamed = [seq for seq, unit in named.items() if not (isinstance(unit, str) and unit in registration["units"])]
        if not own and not unnamed:
            raise ValueError(f"state {row['seq']} has no outcome of the unit it is measured as")
        latest = max(own + unnamed)
    receipt = pinned.get(row["seq"], latest)
    if receipt not in retained:
        raise ValueError("the pinned receipt is not a measurement of that state under the criterion's measure")
    try:
        check_outcome(registration, retained[receipt], unit=expected)
    except ValueError as reason:
        raise ValueError(f"measurement {receipt} of state {row['seq']} is not a result under the criterion's "
                         f"measure: {reason}") from None
    return retained[receipt], receipt


def _sides(rows: dict, pair: tuple) -> tuple:
    """Both sides of a fork: recorded states that continue one recorded state of the same task,
    each with the harness's receipt of its execution."""
    left, right = rows.get(pair[0]), rows.get(pair[1])
    if left is None or right is None:
        raise ValueError("a fork names recorded states")
    origin = rows.get(left.get("parent"))
    if origin is None or left["parent"] != right.get("parent") or origin["seq"] >= min(left["seq"], right["seq"]):
        raise ValueError("a fork's two sides continue the same recorded state")
    if len({(row["task"], row["family"]) for row in (origin, left, right)}) != 1:
        raise ValueError("a fork stays within one task")
    for side in (left, right):
        if not side.get("receipt"):
            raise ValueError("a fork's side carries the harness's receipt of its execution")
    if left.get("measurement_unit") != right.get("measurement_unit"):
        raise ValueError("a fork's two sides are measured as the same unit")
    return left, right


def _measured_pair(measured: dict, pinned: dict, registration: dict, left: dict, right: dict) -> dict:
    (before, first), (after, second) = (_chosen(measured, left, pinned, registration),
                                        _chosen(measured, right, pinned, registration))
    scale = registration.get("scale")
    return {"task": left["task"], "family": left["family"], "base": value(before, scale), "candidate": value(after, scale),
            "measurements": [[left["seq"], first], [right["seq"], second]],
            "executions": [left["receipt"], right["receipt"]]}


def _fork(rows: dict, measured: dict, pinned: dict, registration: dict, base: str | None, candidate: str,
          pair: tuple, unit: str | None, declared=("version",)) -> dict:
    left, right = _sides(rows, pair)
    a, b = left["inputs"], right["inputs"]
    if "version" not in a or "version" not in b:
        raise ValueError("a fork's sides each name the version they ran under")
    different = {name for name in set(a) | set(b) if (name in a) != (name in b) or not same(a[name], b[name])}
    if not (same(a["version"], base) and same(b["version"], candidate)) or different != set(declared):
        raise ValueError("a fork's sides differ in the version and in nothing else" if set(declared) == {"version"}
                         else "a fork's sides differ in exactly what the terms declare: " + ", ".join(sorted(declared)))
    # Cost is compared in the one unit the terms name; a side without it is unknown.
    return dict(_measured_pair(measured, pinned, registration, left, right),
                base_cost=left["cost"].get(unit), candidate_cost=right["cost"].get(unit))


def _null(rows: dict, measured: dict, pinned: dict, registration: dict, pair: tuple) -> dict:
    left, right = _sides(rows, pair)
    if "version" not in left["inputs"] or "version" not in right["inputs"]:
        raise ValueError("a fork's sides each name the version they ran under")
    if not same(left["inputs"], right["inputs"]):
        raise ValueError("a null fork continues one state twice under the same inputs")
    return _measured_pair(measured, pinned, registration, left, right)


def _read(reader, protocol: str, reference, kind: str) -> dict:
    """One receipt as the host retains it. The reader is the host's capability: it resolves an
    object id of the expected kind against its own records and returns that object's body."""
    if not (isinstance(reference, str) and reference):
        raise ValueError(f"under {protocol} every judged state and measurement references its {kind}")
    try:
        body = reader(reference, kind)
    except Exception as error:
        raise ValueError(f"no {kind} is retained as {reference}: {error}") from None
    if not isinstance(body, dict) or body.get("schema") != protocol:
        raise ValueError(f"{reference} is not a {protocol} {kind}")
    return body


def _bind(reader, protocol: str, rows: dict, labels: dict, registration: dict, measured_pair: dict,
          differs, tolerated, sampled: bool = False) -> dict:
    """The receipts behind one measured pair, read and joined to what the journal records.

    The parent state's receipt is a checkpoint of that record and subject. Each side's receipt is
    a continuation whose result, resolved inputs and costs are those recorded for the state. Each
    chosen label's receipt is a measurement whose outcome is the labelled one. The checkpoint names
    the analysis unit it belongs to, and the states are recorded under that unit. Then the fork's
    own rules decide (procontract_fork.measured, admit): one checkpoint, new executions, the same
    restored state, inputs differing exactly as the terms declare, each outcome measured on what
    that side produced, and nothing unshared that the terms do not tolerate."""
    import procontract_fork as fork  # it imports this module
    (left_seq, left_label), (right_seq, right_label) = measured_pair["measurements"]
    left, right = rows[left_seq], rows[right_seq]
    origin = rows[left["parent"]]
    parent = _read(reader, protocol, origin.get("receipt"), "checkpoint_receipt")
    if not ("record" in parent and "subject" in parent and same(parent["record"], origin["record"])
            and same(parent["subject"], origin["subject"])):
        raise ValueError("the checkpoint receipt is of another record or subject than the recorded parent state")
    # The unit a statistic counts is the host's: which task this state belongs to, and its family,
    # as the host bound them when the work was assigned. A label in the journal cannot make one.
    unit = parent.get("analysis_unit")
    if not (isinstance(unit, dict) and all(isinstance(unit.get(name), str) and unit[name] for name in ("id", "family"))):
        raise ValueError("a checkpoint receipt names its analysis unit: an id and a family")
    for row in (origin, left, right):
        if not (same(row["task"], unit["id"]) and same(row["family"], unit["family"])):
            raise ValueError(f"state {row['seq']} is recorded under another task or family than the analysis unit "
                             "its checkpoint belongs to")
    sides, taken = [], []
    for side, label in ((left, left_label), (right, right_label)):
        body = _read(reader, protocol, side.get("receipt"), "continuation_receipt")
        for name, recorded in (("result", side["subject"]), ("resolved_inputs", side["inputs"]), ("costs", side["cost"])):
            if name not in body or not same(body[name], recorded):
                raise ValueError(f"the continuation receipt's {name} is not what is recorded for state {side['seq']}")
        sides.append(body)
        receipt = _read(reader, protocol, labels[label].get("receipt"), "measurement_receipt")
        if "outcome" not in receipt or not same(receipt["outcome"], labels[label]["outcome"]):
            raise ValueError(f"the measurement receipt's outcome is not the outcome of label {label}")
        if "units" in registration and side.get("measurement_unit") != parent.get("measurement_unit"):
            raise ValueError(f"state {side['seq']} is recorded as measured on another unit than its checkpoint names")
        taken.append(receipt)
    joined = fork.admit(fork.measured(parent, sides[0], sides[1], taken, registration, differs=differs), tolerated)
    # What this returns goes into the judgment's request, so its shape is part of every retained
    # judgment's identity: a judgment made without a sampling or series design must give the same
    # request as before either existed. The state's own identity is therefore added only under one
    # (`sampled` is true under either).
    return {**({"state": parent.get("state")} if sampled else {}),
            "unit": {"id": unit["id"], "family": unit["family"]},
            "executions": [sides[0]["execution"], sides[1]["execution"]],
            "measured_by": [taken[0]["execution"], taken[1]["execution"]],
            "checkpoint": [origin["receipt"], joined["parent"]],
            "continuations": [[left["receipt"], joined["continuations"][0]], [right["receipt"], joined["continuations"][1]]],
            "measurements": [[labels[left_label]["receipt"], joined["measurements"][0]],
                             [labels[right_label]["receipt"], joined["measurements"][1]]],
            "differs": joined["differs"], "not_shared": joined["not_shared"],
            "measurement_reused": joined["measurement_reused"]}


def _shared(bound: list[dict], allowed: bool) -> dict:
    """What the pairs of one judgment share, over all of them: executions and measurements named
    by more than one pair. The same two executions named twice are one pair and are refused; an
    execution in several pairs is refused unless the terms provide for shared executions."""
    pairs, executions, measurements = set(), {}, {}
    for receipt in bound:
        both = frozenset(receipt["executions"])
        if both in pairs:
            raise ValueError("the same two executions are named twice: a pair counts once")
        pairs.add(both)
        for execution in both:
            executions[execution] = executions.get(execution, 0) + 1
        for measurement in set(receipt["measured_by"]):
            measurements[measurement] = measurements.get(measurement, 0) + 1
    shared = {"executions": sorted(name for name, uses in executions.items() if uses > 1),
              "measurements": sorted(name for name, uses in measurements.items() if uses > 1)}
    if shared["executions"] and not allowed:
        raise ValueError("an execution is used in more than one pair, and the terms do not provide for shared executions")
    return shared


def _sampled(reader, protocol: str, design: dict, reference, bound: list[dict], contract_id: str,
             generation: int, version: str) -> tuple:
    """The weights of a judgment's forks under the terms' sampling design, and what was sampled.

    `reference` is the host's draw: made once for this Contract's candidate, after the frame was
    fixed, and naming that frame by id and digest together with the seed it drew. The frame lists
    the terms' whole population (the same states and units, `population_id`) with the stratum the
    candidate's first stage put each state in, and names that candidate and the first stage's
    record. The sample follows from the design, the frame and the seed (`sample`). The judgment's
    forks must be forks of exactly the drawn states, each naming the state it continues and
    recorded under the frame's unit for it. A drawn state that is left out is not a missing value
    to fill: the judgment is refused. Forks of one state share that state's weight.

    What this cannot check is time: that the seed did not exist, or was not known, while a stratum
    or a state's identity could still be set. That, and drawing only once per candidate, are the
    host's to guarantee and to show in its own records."""
    draw = _read(reader, protocol, reference, "sampling_draw")
    for name, expected in (("contract", contract_id), ("generation", generation), ("candidate", version)):
        if name not in draw or not same(draw[name], expected):
            raise ValueError(f"the draw was made for another {name} than the one judged")
    named = draw.get("frame")
    if not (isinstance(named, list) and len(named) == 2 and isinstance(draw.get("seed"), str) and draw["seed"]):
        raise ValueError("a draw names its frame by id and digest, and its seed")
    body = _read(reader, protocol, named[0], "sampling_frame")
    if _digest("sampling_frame", body) != named[1]:
        raise ValueError("the frame is not the one the draw was made on")
    if "candidate" not in body or not same(body["candidate"], version):
        raise ValueError("the frame's strata were measured for another candidate")
    if not (isinstance(body.get("first_stage"), str) and body["first_stage"]):
        raise ValueError("a frame names the record of the first stage that set its strata")
    drawn = sample(design, body.get("frame"), draw["seed"])
    units = {row["state"]: row["unit"] for row in body["frame"]}
    judged: dict = {}
    for receipt in bound:
        state = receipt["state"]
        if not (isinstance(state, str) and state):
            raise ValueError("under a sampling design a checkpoint receipt names the state it is in the population")
        if state not in drawn:
            raise ValueError(f"state {state} is judged but was not drawn")
        if receipt["unit"]["id"] != units[state]:
            raise ValueError(f"state {state} belongs to another unit than the frame lists it under")
        judged[state] = judged.get(state, 0) + 1
    left_out = sorted(set(drawn) - set(judged))
    if left_out:
        raise ValueError(f"{len(left_out)} of {len(drawn)} drawn states are not judged: " + ", ".join(left_out[:3]))
    strata: dict = {}
    for row in body["frame"]:
        entry = strata.setdefault(row["stratum"], {"frame": 0, "drawn": 0})
        entry["frame"] += 1
        entry["drawn"] += row["state"] in drawn
    weights = [drawn[receipt["state"]] / judged[receipt["state"]] for receipt in bound]
    return weights, population(design, body["frame"]), {
        "draw": [reference, _digest("sampling_draw", draw)], "frame": named, "design": design,
        "first_stage": body["first_stage"], "states": len(body["frame"]), "drawn": len(drawn), "strata": strata}


def judge(store: Path, contract_id: str, forks: list[tuple], *, null: list[tuple] = (),
          receipts: dict | None = None, reader=None, sampling: str | None = None, roster: list | None = None,
          identities: dict | None = None) -> dict:
    """Judge the candidate on recorded forks, under the terms the Contract is bound to now.

    `forks` pairs journal sequences of states: one state continued under the incumbent and under
    the candidate; `null` pairs states continued twice under the same input. `receipts` may pin,
    for a state's sequence, which retained measurement of it to use; otherwise the latest is used.
    Everything else is read from the record: the criterion, the evaluator and environment of the
    registered measure, values, tasks, costs in the terms' unit, and what the candidate's views
    have read. The caller cannot report a verdict or name an evaluator.

    When the terms name a receipt protocol, `reader` is the host's capability to resolve a
    receipt's object id of an expected kind to the body the host retains. Each pair's checkpoint,
    continuations and chosen measurements are read through it and checked (`_bind`) against the
    journal and against what the terms declare may differ and tolerate being unshared. Without the
    reader, or with a receipt missing, such a judgment is refused; it never falls back to reading
    a state's receipt as an opaque string.

    When the terms also state a sampling design, `sampling` is the host's object id of the draw
    made for this candidate. The forks are then weighted as the design, the draw and its frame
    dictate (`_sampled`), and the effect is an estimate for the terms' whole population. A draw
    named under terms without a design is refused, never ignored.

    When the terms state a series design, `roster` is the roster they name: the units in their
    order with the states measured in each. It is data, checked against the identity in the terms,
    so it does not matter who supplies it. The forks must cover exactly its states, each fork
    naming through its checkpoint receipt the state it continues, and the effect is the one
    `_series_effect` defines. A roster given under terms without a series design is refused.

    A judgment is its complete input: the Contract's revision and terms, the candidate and its
    generation, the forks, the null controls and the exact measurements used. The same input
    again finishes or returns the retained judgment. A candidate that already carries a judgment
    is not judged again on other inputs until that judgment is withdrawn; new measurements of the
    same states are other inputs. The statement is retained, each view is charged one verdict,
    and the kernel receives a Support with the statement's reach and basis, or a Defeat.
    """
    with _exclusive(store):
        events = kernel.events(store)
        contract = kernel.contract(store, contract_id)
        candidate = contract["candidate"]
        pairs, nulls = [list(pair) for pair in forks], [list(pair) for pair in null]
        statements = _statements(events, contract_id)
        if contract["standing"] != "outstanding" or candidate is None:
            last = dict(statements[-1]) if statements else None
            # Only the judgment that defeated the last candidate, repeated, has an answer here.
            pinned = {int(state): receipt for state, receipt in (receipts or {}).items()}
            used = {state: receipt for state, receipt in (last or {}).get("measurements", [])}
            if (last and candidate is None and contract["standing"] == "outstanding" and last["unmet"]
                    and (last["forks"], last["null"]) == (pairs, nulls)
                    and last["generation"] + 1 == contract["generation"]
                    and all(used.get(state) == receipt for state, receipt in pinned.items())
                    # A draw named explicitly must be the one that judgment used; naming none reads it back.
                    and (sampling is None or sampling == (last.get("sampling") or {}).get("draw", [None])[0])
                    and (roster is None
                         or roster_id(roster) == ((last.get("series") or {}).get("design") or {}).get("roster"))):
                last.pop("seq")
                return _conclude(store, contract, last)
            raise ValueError("no candidate to judge")
        terms, proposal = _terms(events, contract), _proposal(events, contract_id, candidate["subject_hash"])
        criterion = terms["criterion"]
        registration, measured_as = criterion["measure"], measure_id(criterion["measure"])
        rows = {row["seq"]: row for row in (dict(body, seq=seq) for seq, body in _of(events, "capture"))}
        measured = outcomes(events, registration)
        pinned = {int(state): receipt for state, receipt in (receipts or {}).items()}
        protocol = criterion.get("receipt_protocol")
        # Terms this module would not issue are not judged by it, only read back: an earlier writer
        # may have accepted a design without its method, or a key it did not know under conditions
        # this module refuses.
        _check(terms)
        series = None
        if "series" in criterion:
            design = criterion["series"]
            if roster is None or roster_id(roster) != design["roster"]:
                raise ValueError("these terms judge over a series: the roster they name is required")
            series = {"design": design, "units": len(roster), "states": sum(len(row["states"]) for row in roster)}
        elif roster is not None:  # on every path: a roster is never ignored
            raise ValueError("a roster is read only when the terms state a series design")
        declared = criterion["differs"] if protocol else ["version"]
        paired = [_fork(rows, measured, pinned, registration, proposal["base"], proposal["version"], pair,
                        criterion.get("cost_unit"), declared) for pair in forks]
        controls = [_null(rows, measured, pinned, registration, pair) for pair in null]
        measurements = sorted(m for fork in paired + controls for m in fork["measurements"])
        bound = None
        if protocol:
            if reader is None:
                raise ValueError(f"these terms judge by {protocol} receipts: the host's reader is required")
            labels = dict(_of(events, "label"))
            bound = [_bind(reader, protocol, rows, labels, registration, row, differs, criterion["tolerated"],
                           sampled=("sampling" in criterion or series is not None) and index < len(paired))
                     for index, (row, differs) in enumerate([(row, declared) for row in paired]
                                                            + [(row, ()) for row in controls])]
            shared = _shared(bound, criterion.get("shared_executions") is True)
            # The statistic's units and executions are the receipts', read above; not the journal's.
            for row, receipt in zip(paired + controls, bound):
                row.update(task=receipt["unit"]["id"], family=receipt["unit"]["family"],
                           base_execution=receipt["executions"][0], candidate_execution=receipt["executions"][1])
            drawn = whole = None
            if "sampling" in criterion:
                if controls:
                    raise ValueError("no noise floor is defined for a sampled estimate: it reads no null forks")
                weights, whole, drawn = _sampled(reader, protocol, criterion["sampling"], sampling, bound[:len(paired)],
                                                 contract_id, candidate["generation"], proposal["version"])
                for row, weight in zip(paired, weights):
                    row["weight"] = weight
            if series is not None:
                if controls:
                    raise ValueError("no noise floor is defined for an estimate over a series: it reads no null forks")
                for row, receipt in zip(paired, bound):
                    row["state"] = receipt["state"]
        if sampling is not None and "sampling" not in criterion:  # on every path: a draw is never ignored
            raise ValueError("a draw is read only when the terms state a sampling design")
        # The candidate's registration is part of the input: a judgment begun before another
        # selector was disclosed is a different judgment and is not finished under the new views.
        asked = {
            "contract": contract_id, "revision": contract["revision"],
            "terms_hash": contract["bindings"]["terms_hash"], "generation": candidate["generation"],
            "candidate": candidate["subject_hash"], "proposal": proposal["seq"], "views": proposal["views"],
            "forks": pairs, "null": nulls, "measurements": measurements}
        if protocol:  # what was read is part of the judgment's input; older judgments keep their identity
            asked.update(receipt_protocol=protocol, fork_receipts=bound, shared=shared)
            if drawn is not None:
                asked.update(sampling=drawn)
            if series is not None:
                asked.update(series=series)
        # Which pools this judgment reads exposure and verdicts of. When the store's record adds
        # pools to the measure's own, they are part of the input: a judgment retained before the
        # store knew of them is another judgment and is not finished as this one, whose reach
        # can differ. A judgment with nothing added keeps the request it always had.
        pools = _overlapping(events, registration)
        added = sorted(set(pools) - set(_pools(registration)))
        if added:
            asked.update(pools=sorted(pools))
        request, replaced = _digest("judgment", asked), None
        while True:
            earlier = next((found for found in reversed(statements) if found.get("request") == request), None)
            if earlier is None:
                break
            if not _stale(events, earlier, registration, criterion):
                earlier.pop("seq")
                return _conclude(store, contract, earlier)
            # A retained answer is checked before it is used. This one recorded the cases as
            # beyond reach although the record before it already put them within, so it is not
            # concluded and not returned as this judgment. The judgment is made again as one that
            # replaces it, under its own request; the earlier statement stays as it is.
            replaced = request
            asked.update(replaces=replaced)
            request = _digest("judgment", asked)
        if contract["support"] is not None:
            raise ValueError("this candidate carries a judgment that recorded these cases as beyond reach although the "
                             "record before it puts them within: withdraw it and judge again" if replaced else
                             "this candidate already carries a judgment on other inputs")
        result = effect(paired, controls or None, population=whole if protocol else None,
                        series=dict(criterion["series"], roster=roster) if series is not None else None)
        views = proposal["views"]
        statement = {
            "request": request, "contract": contract_id, "revision": contract["revision"],
            "generation": candidate["generation"], "terms_hash": contract["bindings"]["terms_hash"],
            "candidate": proposal["version"], "base": proposal["base"], "on": proposal["on"],
            "proposal": proposal["seq"],
            "measure": measured_as, "pool": registration["pool"], "evaluator": registration["evaluator"],
            "environment": registration["environment"], "forks": pairs, "null": nulls,
            "measurements": measurements,
            "receipts": sorted({receipt for _, receipt in measurements}),
            "executions": sorted({r for fork in paired + controls for r in fork["executions"]}),
            "effect": result, "unmet": met(criterion, result),
            "exposure": {view: _seen(events, view, pools) for view in views},
            "max_verdicts": criterion.get("max_verdicts"),
            "reach": reach(events, views, pools, criterion.get("max_verdicts"), criterion.get("max_pool_verdicts")),
        }
        if added:
            statement.update(pools=sorted(pools))
        if replaced:
            statement.update(replaces=replaced)
        if "max_pool_verdicts" in criterion:  # only then: a statement under other terms keeps its shape
            statement.update(max_pool_verdicts=criterion["max_pool_verdicts"], pool_verdicts=verdicts(events, pools))
        if protocol:
            statement.update(receipt_protocol=protocol, fork_receipts=bound, shared=shared)
            if drawn is not None:
                statement.update(sampling=drawn)
            if series is not None:
                statement.update(series=series)
        # The retained statement is the judgment's intent: a repeat finishes it, never redoes it.
        kernel.append(store, "insight", identities or kernel.identities(), statement)
        return _conclude(store, contract, statement)


def _conclude(store: Path, contract: dict, statement: dict) -> dict:
    """Charge each view its one verdict for this judgment and give the kernel the statement's
    Support or Defeat, unless the Contract already shows it."""
    contract_id, candidate = statement["contract"], contract["candidate"]
    for view in statement["exposure"]:
        expose(store, view, statement["pool"], "verdict", statement["request"])
    evidence = _digest("credit", statement)
    current = (candidate and contract["standing"] == "outstanding" and contract["support"] is None
               and candidate["generation"] == statement["generation"])
    if current and statement["unmet"]:
        contract = _apply(store, f"defeat:{contract_id}:{evidence}", contract, contract_id, "verifier", "automation", {
            "type": "defeat", "defeater": evidence,
            "target": {"type": "candidate", "generation": candidate["generation"],
                       "subject_hash": candidate["subject_hash"]}})
    elif current:
        coordinate = {
            "contract_id": contract_id, "revision": contract["revision"], **contract["bindings"],
            "generation": candidate["generation"], "subject_hash": candidate["subject_hash"],
            "environment_digest": statement["environment"], "evaluator_digest": statement["evaluator"],
            "evidence_hash": evidence,
            # What this judgment stands on: its measurements, the executions that produced the
            # measured states, and the acceptance the candidate was measured against.
            "basis": _digest("basis", {"receipts": statement["receipts"], "executions": statement["executions"],
                                       "on": statement["on"]}),
            "reach": statement["reach"]}
        contract = _apply(store, f"support:{contract_id}:{evidence}", contract, contract_id, "verifier", "automation", {
            "type": "support", "coordinate": coordinate, "certificate": _digest("certificate", coordinate)})
    return {"statement": statement, "contract": contract}


def _as_stated(events, contract: dict, terms: dict) -> None:
    """Refuse a Support whose judgment did not use the method the terms state. A writer older
    than a design in the terms does not know it and judges another way; what it supported is not
    what these terms ask for, under any authority. The Support stays as it is: it can be
    withdrawn and the candidate judged again."""
    criterion = terms["criterion"]
    if not any(name in criterion for name in ("series", "sampling")):
        return
    # Older writers can retain a malformed or unnamed design as well as ignore a valid one.
    # Check the issued design before comparing methods: two missing names are not a method.
    _check(terms)
    design = criterion["series"] if "series" in criterion else criterion["sampling"]
    evidence = contract["support"]["coordinate"]["evidence_hash"]
    for statement in _statements(events, contract["id"]):
        statement.pop("seq")
        if _digest("credit", statement) == evidence:
            if (statement.get("effect") or {}).get("method") != design.get("method"):
                raise ValueError("the judgment behind this Support did not use the method the terms state")
            return
    raise ValueError("no retained judgment is the evidence of this Support")


def _rederived(events, statement: dict, registration: dict, criterion: dict) -> str:
    """The reach this module derives now for a retained judgment, from the record before it:
    exposure and verdicts as they stood when its statement was retained, for the views it was
    made under, over the pools known now to share the measure's cases. Nothing after the
    statement is read: not its own conclusion, not what anyone read or was told later."""
    before = [row for row in _events(events) if row["seq"] < statement["seq"]]
    return reach(before, list(statement["exposure"]), _overlapping(events, registration),
                 criterion.get("max_verdicts"), criterion.get("max_pool_verdicts"))


def _stale(events, statement: dict, registration: dict, criterion: dict) -> bool:
    """A retained judgment that recorded its cases as beyond reach although the record before
    it, read by this module's rules, puts them within: left by a writer that counted less."""
    return (not statement["unmet"] and statement["reach"] == "beyond"
            and _rederived(events, statement, registration, criterion) == "within")


def _reach_stands(events, contract: dict, terms: dict) -> None:
    """Refuse to presume an adoption on a judgment that did not read every pool now known to
    share the measure's cases. Its reach was computed over the pools the store knew of then; if
    terms retained since register another, or its writer did not derive them, that reach says
    nothing about who read the other pool. The Support stays: withdrawn, the candidate is judged
    again over the pools known now. The principal's own acceptance does not rest on reach and is
    not refused here."""
    registration = terms["criterion"]["measure"]
    evidence = contract["support"]["coordinate"]["evidence_hash"]
    for statement in _statements(events, contract["id"]):
        seq = statement.pop("seq")
        if _digest("credit", statement) == evidence:
            if sorted(statement.get("pools", set(_pools(registration)))) != sorted(set(_overlapping(events, registration))):
                raise ValueError("pools now known to share these cases were not read by this judgment: "
                                 "withdraw it and judge again, or the principal accepts explicitly")
            # The same for what its views had drawn: a writer that counted less may have left a
            # Support as beyond reach that the record before it does not bear out.
            if _stale(events, dict(statement, seq=seq), registration, terms["criterion"]):
                raise ValueError("the record before this judgment already put these cases within reach: "
                                 "withdraw it and judge again, or the principal accepts explicitly")
            return
    raise ValueError("no retained judgment is the evidence of this Support")


@_locked
def adopt(store: Path, contract_id: str, *, explicit: str | None = None, identities: dict | None = None) -> dict:
    """Settle a supported improvement and select its candidate, in one transaction.

    Authority is given, never implied. `explicit` is the host's receipt of the principal's own
    acceptance. Without it the acceptance is presumed under the convention the principal wrote
    into the terms, which the kernel allows only on evidence from beyond reach; terms without a
    convention authorize no presumed adoption. This is a host interface, not a tool for a model:
    the module cannot authenticate the receipt it is given.

    When the terms state a sampling or series design, the Support adopted is one whose retained
    judgment used that design's method (`_as_stated`); the principal's own act does not change
    which arithmetic the terms ask for. A presumed adoption also needs the judgment to have read
    every pool now known to share the measure's cases (`_reach_stands`).

    The selection is fenced by the latest selection seen and refused when the acceptance the
    candidate was built on no longer serves. Repeating an adoption under the same authority
    returns the settled Contract; another authority on a settled Contract is refused.
    """
    explicit = _receipt(explicit, "explicit")
    events = kernel.events(store)
    contract = kernel.contract(store, contract_id)
    terms = _terms(events, contract)
    convention = terms.get("convention")
    if explicit is None and not convention:
        raise ValueError("these terms authorize no presumed adoption; the principal must act")
    decision = {"type": "explicit"} if explicit else {
        "type": "presumed", "convention": _digest("convention", convention),
        "classifier": _digest("classifier", IMPLEMENTATION)}
    attestation = _digest("attestation", {"contract": contract_id, "explicit": explicit, "decision": decision})
    if contract["standing"] == "discharged" and any(
            row["contract_id"] == contract_id
            and row["generation"] == contract["settlement"]["coordinate"]["generation"]
            for row in kernel.acceptances(store)):
        if contract["settlement"]["attestation"] != attestation:
            raise ValueError("already adopted under another authorization")
        return contract
    if not contract["support"]:
        raise ValueError("nothing supported to adopt")
    _as_stated(events, contract, terms)
    if explicit is None:  # a presumed adoption is the one that rests on reach
        _reach_stands(events, contract, terms)
    # One observation of what is selected: the comparison below and the fence the store checks
    # are the same reading, so a selection made in between is refused by the store.
    selected = kernel.incumbent(store)
    serving_now = selected["acceptance"]["selection"] if selected and selected["acceptance"]["live"] else None
    on = _proposal(events, contract_id, contract["candidate"]["subject_hash"])["on"]
    if on != serving_now:
        raise ValueError("the incumbent changed since this candidate was judged")
    command = {"contract_id": contract_id, "expected_version": contract["version"], "role": "settler",
               "provenance": "human" if explicit else "delegate",
               "command": {"type": "discharge", "coordinate": contract["support"]["coordinate"],
                           "attestation": attestation, "decision": decision}}
    # The store commits only while the acceptance this candidate was built on is live, checked
    # under the same write lock as the settlement; the check above is for a clear message.
    return kernel.adopt(store, f"adopt:{contract_id}:{contract['version']}", command,
                        selected["selection"] if selected else None,
                        identities or kernel.identities(harness=IMPLEMENTATION),
                        supports=[on] if on is not None else None)


def incumbent(store: Path) -> dict:
    """The version selected to serve, when the acceptance behind that selection is live.

    The store answers this from the selections its own adoption and re-selection transactions
    made; an entry another writer appended to the journal selects nothing. After a withdrawal
    beneath it the selection is void: no version is returned and nothing is chosen in its place.
    `seq` stays the latest selection, which fences the next one; `last_standing` names the most
    recent acceptance that is still live, for `restore`.
    """
    current = kernel.incumbent(store)
    if current is None:
        return {"version": None, "adoption": None, "seq": None, "acceptance": None, "last_standing": None}
    accepted = current["acceptance"]
    if accepted["live"]:
        return {"version": accepted["subject_hash"], "adoption": accepted["contract_id"],
                "seq": current["selection"], "acceptance": accepted["selection"]}
    standing = [row["selection"] for row in kernel.acceptances(store) if row["live"]]
    return {"version": None, "adoption": None, "seq": current["selection"], "acceptance": None,
            "last_standing": standing[-1] if standing else None}


def _root_terms(store: Path, events, acceptance: int) -> dict:
    """Terms of the installation at the root of an acceptance's lineage: the scope's own terms."""
    recorded = {row["selection"]: row for row in kernel.acceptances(store)}
    while recorded[acceptance]["supports"]:
        acceptance = recorded[acceptance]["supports"][0]
    return _terms(events, kernel.contract(store, recorded[acceptance]["contract_id"]))


@_locked
def restore(store: Path, *, explicit: str | None = None, identities: dict | None = None) -> dict:
    """Select the most recent acceptance that still stands, after the selected one was withdrawn.

    This is a new selection and is recorded as one: under the host's receipt of the principal's
    act, or presumed under the restore convention the principal named when the scope was
    installed. Without either it is refused. It accepts nothing new.
    """
    explicit = _receipt(explicit, "explicit")
    current = incumbent(store)
    if current["version"] is not None or current["last_standing"] is None:
        return current
    events = kernel.events(store)
    convention = _root_terms(store, events, current["last_standing"]).get("restore")
    if explicit is None and not convention:
        raise ValueError("no restore convention was named for this scope; the principal must act")
    decision = {"type": "explicit", "receipt": explicit} if explicit else {
        "type": "presumed", "convention": _digest("convention", convention)}
    # One transaction in the store: the fence, the acceptance's liveness and the new selection.
    kernel.select(store, f"restore:{current['last_standing']}:{current['seq']}", current["last_standing"],
                  current["seq"], identities or kernel.identities(harness=IMPLEMENTATION), decision)
    return incumbent(store)


@_locked
def withdraw(store: Path, contract_id: str, cause: str, *, observed: dict | None = None) -> dict:
    """Take back a judgment that no longer holds: an adoption, or a Support not yet adopted. The
    Contract is outstanding with its candidate kept for a new judgment, so the defect is in the
    backlog and the candidate can be judged again on other evidence.

    `observed` is the Contract as the caller checked it. The command names that version and that
    exact judgment, so if the Contract has moved since, the kernel refuses instead of taking back
    something else."""
    contract = observed or kernel.contract(store, contract_id)
    if contract["standing"] == "discharged":
        target = {"type": "settlement", "coordinate": contract["settlement"]["coordinate"]}
    elif contract["support"] is not None:
        target = {"type": "support", "coordinate": contract["support"]["coordinate"]}
    else:
        return contract
    return _apply(store, f"withdraw:{contract_id}:{contract['version']}", contract, contract_id, "verifier",
                  "automation", {"type": "withdraw", "cause": _digest("cause", cause), "target": target})


@_locked
def reconcile(store: Path) -> list[str]:
    """Reopen every adoption that is still settled but no longer live because an acceptance
    beneath it was withdrawn. Serving never depended on this; it restores those defects to the
    backlog. Only acceptances the store recorded are considered, and only where the Contract's
    present settlement is exactly the recorded one: a settlement the store has no acceptance
    for is unknown, not invalid, and is left alone."""
    reopened = []
    for row in kernel.acceptances(store):
        contract = kernel.contract(store, row["contract_id"])
        settled = contract["standing"] == "discharged" and (
            contract["settlement"]["coordinate"]["generation"], contract["settlement"]["coordinate"]["subject_hash"]
        ) == (row["generation"], row["subject_hash"])
        if settled and not row["live"]:
            try:
                withdraw(store, row["contract_id"],
                         f"an acceptance beneath acceptance {row['selection']} was withdrawn", observed=contract)
            except RuntimeError as moved:
                if "StaleVersion" not in str(moved) and "TargetMismatch" not in str(moved):
                    raise
                continue  # settled anew since it was checked: that settlement is not this acceptance
            reopened.append(row["contract_id"])
    return reopened


def improvements(store: Path) -> list[dict]:
    """Every improvement with the terms it is bound to now and its present state in the kernel."""
    events, rows = kernel.events(store), {}
    for _, body in _of(events, "issue"):
        if "binding" in body and body.get("contract") not in rows:  # issue events of this module only
            contract = kernel.contract(store, body["contract"])
            rows[body["contract"]] = {
                "contract": body["contract"], "terms": _terms(events, contract), "standing": contract["standing"],
                "candidate": (contract["candidate"] or {}).get("subject_hash"),
                "supported": contract["support"] is not None}
    return list(rows.values())


def backlog(store: Path) -> list[dict]:
    """Located defects that are still owed."""
    return [row for row in improvements(store) if row["standing"] == "outstanding"]


def history(store: Path, contract_id: str) -> list[dict]:
    """Every candidate this improvement ever had, with its judgment and its adoption if any. The
    kernel holds one current candidate; the journal keeps them all, so this is the improver's
    own record for a ledger of what it tried, what was defeated and what held."""
    events = kernel.events(store)
    statements = _statements(events, contract_id)
    accepted = [row["selection"] for row in kernel.acceptances(store) if row["contract_id"] == contract_id]
    proposals = [dict(body, seq=seq) for seq, body in _of(events, "version") if body.get("contract") == contract_id]
    rows = []
    for index, proposal in enumerate(proposals):
        until = proposals[index + 1]["seq"] if index + 1 < len(proposals) else float("inf")
        within = lambda seq: proposal["seq"] < seq < until  # noqa: E731
        rows.append({**proposal,
                     "judgments": [s for s in statements if within(s["seq"]) and s["candidate"] == proposal["version"]],
                     "adopted": next((seq for seq in accepted if within(seq)), None)})
    return rows


def serving(store: Path, trial: str | None = None) -> str | None:
    """The version a default execution binds: the incumbent, or None when no selection stands.

    With `trial`, the candidate of that improvement while it is outstanding. This only resolves
    which version a declared trial means. Permission to execute it is the host's, and its runs are
    evidence for that Contract, not an adoption.
    """
    if trial is None:
        return incumbent(store)["version"]
    row = next(row for row in backlog(store) if row["contract"] == trial)
    if row["candidate"] is None:
        raise ValueError("this improvement has no candidate to try")
    return row["candidate"]
