"""Pure, versioned decision-study specifications and fixed-frame readouts.

No files, model requests, code supplied by an author, or kernel transitions are
executed here. The host must bind source bytes, the allowlisted frame, actual
requests, closure and records. ``validate(..., frame_registry=...)`` checks a
host-supplied frame allowlist; validation without that argument checks shape
only. Source SHA256s refer to retained object bytes, not reserialized objects.

The working percentile interval resamples original runs, carrying every point
in each sampled run. It is a decision-level descriptive interval, not a task
quality, generalization, independence, or adoption certificate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
import random
import re

import procontract_zero_step_check as zero_step


SCHEMA = "procontract-decision-study/v1"
ACTIONS = ("successor", "verify", "diagnose", "check", "rest", "tool_request")
READINGS = ("action_delivery", "serialized_json_marker", "delivered_json_length", "zero_step_check_avoidance")
_FIELDS = {
    "schema", "candidate", "frame", "baseline_policy_sha256", "reading",
    "prediction", "applicable", "unknown_rule", "repeats", "ordering_seed",
    "analysis_seed", "cluster", "bootstrap_replicates", "aa",
}


class UnsupportedSpec(ValueError):
    """A named reading or rule has no implementation in this module."""


def canonical(value):
    """Strict canonical JSON bytes; no Python equality or key coercion."""
    def check(item):
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("JSON keys must be strings")
            for child in item.values():
                check(child)
        elif type(item) is list:
            for child in item:
                check(child)
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
        elif type(item) not in (str, int, bool, type(None)):
            raise ValueError("value is not JSON")
    check(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except UnicodeError as error:
        raise ValueError("JSON strings must contain Unicode scalars") from error


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _object(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"{label} must have exactly {sorted(keys)}")


def _text(value, label):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


def _id(value, label):
    _text(value, label)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        raise ValueError(f"{label} must be an opaque object ID, not a host path")


def _sha(value, label):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a lowercase SHA256")


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and nonnegative")


def _positive_int(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")


def validate(spec, *, frame_registry=None):
    """Return a detached strict JSON spec; refuse unknown executable semantics.

    ``frame_registry`` is a host-owned mapping ``frame_id -> source_sha256``.
    It is deliberately outside the author specification. Hashing the actual
    source bytes and checking request/receipt provenance remain host work.
    """
    spec = json.loads(canonical(spec))
    _object(spec, _FIELDS, "study")
    if spec["schema"] != SCHEMA:
        raise UnsupportedSpec("unsupported decision-study schema")
    for key, id_key in (("candidate", "source_id"), ("frame", "id")):
        _object(spec[key], {id_key, "sha256"}, key)
        _id(spec[key][id_key], f"{key}.{id_key}")
        _sha(spec[key]["sha256"], f"{key}.sha256")
    _sha(spec["baseline_policy_sha256"], "baseline_policy_sha256")
    if frame_registry is not None:
        if (type(frame_registry) is not dict
                or frame_registry.get(spec["frame"]["id"]) != spec["frame"]["sha256"]):
            raise ValueError("frame is not in the host allowlist at this digest")

    reading = spec["reading"]
    _object(reading, {"id", "version", "parameters"}, "reading")
    if (reading["id"] not in READINGS or type(reading["version"]) is not int
            or reading["version"] != 1):
        raise UnsupportedSpec("unsupported named reading or version")
    parameter_keys = {"closed_non_delivery"}
    if reading["id"] == "action_delivery":
        parameter_keys.add("actions")
    elif reading["id"] == "serialized_json_marker":
        parameter_keys.update(("words", "tokenizer"))
    elif reading["id"] == "zero_step_check_avoidance":
        parameter_keys.add("population")
    params = reading["parameters"]
    _object(params, parameter_keys, "reading.parameters")
    if reading["id"] == "zero_step_check_avoidance":
        if params != {"closed_non_delivery": "unknown", "population": "known_applicable"}:
            raise UnsupportedSpec("zero-step reading requires unknown non-delivery and known-applicable population")
    elif params["closed_non_delivery"] != "zero":
        raise UnsupportedSpec("delivery readings require explicit closed non-delivery zero")
    if reading["id"] == "action_delivery":
        values = params["actions"]
        if (type(values) is not list or not values or any(v not in ACTIONS for v in values)
                or len(set(values)) != len(values)):
            raise ValueError("actions must be distinct registered delivery categories")
    elif reading["id"] == "serialized_json_marker":
        if params["tokenizer"] != "ascii_letters_min5_lower":
            raise UnsupportedSpec("unsupported serialized-JSON tokenizer")
        words = params["words"]
        if (type(words) is not list or not words
                or any(type(word) is not str or not re.fullmatch(r"[a-z]{5,}", word) for word in words)
                or len(set(words)) != len(words)):
            raise ValueError("marker words must be distinct lowercase ASCII words of length >=5")

    prediction = spec["prediction"]
    _object(prediction, {"direction", "min_effect", "falsifier"}, "prediction")
    if prediction["direction"] not in ("increase", "decrease"):
        raise UnsupportedSpec("unsupported prediction direction")
    _number(prediction["min_effect"], "prediction.min_effect")
    _object(prediction["falsifier"], {"kind", "min_effect"}, "prediction.falsifier")
    if prediction["falsifier"]["kind"] not in ("opposite_interval", "nonpositive_interval"):
        raise UnsupportedSpec("unsupported falsifier; natural-language claims are not inferred")
    _number(prediction["falsifier"]["min_effect"], "falsifier.min_effect")

    _object(spec["applicable"], {"all"}, "applicable")
    conditions = spec["applicable"]["all"]
    if type(conditions) is not list:
        raise ValueError("applicable.all must be a list of pre-input conditions")
    if reading["id"] == "zero_step_check_avoidance" and conditions:
        raise UnsupportedSpec("zero-step applicability is the registered facts rule, without extra filters")
    for condition in conditions:
        _object(condition, {"field", "equals"}, "pre-input condition")
        field = condition["field"]
        if (type(field) is not str or (field not in ("family", "original_action")
                and not re.fullmatch(r"pre_input\.[A-Za-z][A-Za-z0-9_]*", field))):
            raise UnsupportedSpec("applicability must use registered pre-input metadata")
    if spec["unknown_rule"] != "retain":
        raise UnsupportedSpec("unknown results must remain unknown in the fixed frame")
    _object(spec["repeats"], {"A", "B"}, "repeats")
    for arm, count in spec["repeats"].items():
        _positive_int(count, f"repeats.{arm}")
    for key in ("ordering_seed", "analysis_seed"):
        if type(spec[key]) is not int and not (type(spec[key]) is str and spec[key]):
            raise ValueError(f"{key} must be an integer or nonempty string")
    if spec["cluster"] != "run_id":
        raise UnsupportedSpec("only whole-run clustering is implemented")
    _positive_int(spec["bootstrap_replicates"], "bootstrap_replicates")
    if spec["aa"] not in ("none", "shared_A"):
        raise UnsupportedSpec("only contemporaneous shared A1/A2 is an implemented A/A reference")
    if spec["aa"] == "shared_A" and spec["repeats"]["A"] < 2:
        raise ValueError("shared A/A requires two planned A repetitions")
    return spec


def identity(spec):
    """Identity includes all reading, prediction, missingness and design fields."""
    return digest(validate(spec))


def _applies(spec, point):
    if spec["reading"]["id"] == "zero_step_check_avoidance":
        return zero_step.applicable(point.get("facts"))
    unknown = False
    for condition in spec["applicable"]["all"]:
        field = condition["field"]
        source = point
        if field.startswith("pre_input."):
            source, field = point.get("pre_input", {}), field.split(".", 1)[1]
        if type(source) is not dict or field not in source:
            unknown = True
        elif canonical(source[field]) != canonical(condition["equals"]):
            return False
    return None if unknown else True


def _known(row):
    # Same retained-observation convention as the owner v4/v5 readout. The host,
    # not this pure reader, authenticates the observation and physical closure.
    return bool(row and row.get("shutdown_confirmed") is True
                and type(row.get("record")) in (str, dict) and row["record"]
                and type(row.get("valid_decision")) is bool)


def _category(row):
    if row is None:
        return "unstarted"
    if row.get("shutdown_confirmed") is not True:
        return "unresolved"
    if not _known(row):
        return "missing_measurement"
    if row.get("valid_decision") is True:
        decision = row.get("decision")
        if type(decision) is not dict or type(decision.get("choice")) is not dict:
            raise ValueError("a valid decision must contain the complete choice object")
        action = decision["choice"].get("action")
        if action not in ACTIONS[:-1] or ("action" in row and row["action"] != action):
            raise ValueError("valid decision action is missing or disagrees with the row")
        return action
    if row.get("category") == "tool_request":
        return "tool_request"
    if row.get("category") == "refusal":
        return "refusal"
    if type(row.get("http_status")) is int and row["http_status"] >= 400:
        return "http_error"
    if row.get("category") in ("structured_json", "unstructured_output"):
        return "invalid_output"
    return row.get("category") or "missing_measurement"


def _value(reading, row, point=None):
    if not _known(row):
        return None
    category = _category(row)
    if reading["id"] == "zero_step_check_avoidance":
        return zero_step.score((point or {}).get("facts"), {
            "closed": row["shutdown_confirmed"], "valid_decision": row["valid_decision"],
            "decision": row.get("decision"), "category": category})
    if reading["id"] == "action_delivery":
        delivered = category if row["valid_decision"] or category == "tool_request" else None
        return float(delivered in reading["parameters"]["actions"])
    text = (json.dumps(row["decision"], ensure_ascii=False, sort_keys=True, allow_nan=False)
            if row.get("valid_decision") is True else "")
    if reading["id"] == "delivered_json_length":
        return len(text)
    tokens = set(re.findall(r"[a-z]{5,}", text.lower()))
    return float(bool(tokens.intersection(reading["parameters"]["words"])))


def _point(point, a, b, bounded):
    def mean(values):
        return sum(values) / len(values) if all(v is not None for v in values) else None
    ma, mb = mean(a), mean(b)
    delta = mb - ma if ma is not None and mb is not None else None
    if bounded:
        low = sum(0 if v is None else v for v in b) / len(b) - sum(1 if v is None else v for v in a) / len(a)
        high = sum(1 if v is None else v for v in b) / len(b) - sum(0 if v is None else v for v in a) / len(a)
    else:
        low = high = delta
    return {"point_id": point["point_id"], "run_id": point["run_id"], "A": a, "B": b,
            "mean_A": ma, "mean_B": mb, "delta": delta, "low": low, "high": high}


def _quantile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _contrast(points, spec, applicability_complete):
    count = len(points)
    known = [p for p in points if p["delta"] is not None]
    complete = bool(count and len(known) == count and applicability_complete)
    groups = defaultdict(list)
    for point in points:
        groups[point["run_id"]].append(point["delta"])
    bounds = ([sum(p["low"] for p in points) / count,
               sum(p["high"] for p in points) / count]
              if count and applicability_complete and all(p["low"] is not None for p in points) else None)
    result = {
        "planned_points": count, "known_point_contrasts": len(known),
        "run_count": len(groups), "contributing_runs": len({p["run_id"] for p in known}),
        "applicability_complete": applicability_complete,
        "mean_difference": sum(p["delta"] for p in known) / count if complete else None,
        "worst_case_bounds": bounds, "run_bootstrap_interval_95": None,
        "bootstrap_replicates": 0,
        "interval_method": "working-whole-run-percentile/1",
    }
    if complete and len(groups) >= 2:
        # Sort group identities so input-row order does not change seed semantics.
        clusters = [(sum(groups[key]), len(groups[key])) for key in sorted(groups)]
        rng = random.Random(spec["analysis_seed"])
        draws = []
        for _ in range(spec["bootstrap_replicates"]):
            sample = [rng.choice(clusters) for _ in clusters]
            draws.append(sum(total for total, _ in sample) / sum(n for _, n in sample))
        result["run_bootstrap_interval_95"] = [_quantile(draws, .025), _quantile(draws, .975)]
        result["bootstrap_replicates"] = spec["bootstrap_replicates"]
    elif complete:
        result["interval_unavailable"] = "fewer than two original runs"
    else:
        result["interval_unavailable"] = "empty frame or unknown applicability/outcome"
    return result


def _slots(spec, frame, rows):
    points = {p["point_id"] for p in frame}
    slots, operations = {}, set()
    for row in rows:
        if type(row) is not dict:
            raise ValueError("outcome row must be an object")
        point = row.get("point_id", row.get("task"))
        if "point_id" in row and "task" in row and row["point_id"] != row["task"]:
            raise ValueError("point_id and task disagree")
        if point not in points:
            raise ValueError("outcome is outside the frozen frame")
        arm = row.get("arm")
        if type(arm) is not str:
            raise ValueError("outcome arm must name a planned repetition")
        match = re.fullmatch(r"([AB])([1-9][0-9]*)?", arm)
        if not match:
            raise ValueError("unplanned arm; historical H is not a fresh A sample")
        name, repetition = match.groups()
        if repetition is None and spec["repeats"][name] != 1:
            raise ValueError("repeated arms must identify their repetition")
        repetition = int(repetition or 1)
        if repetition > spec["repeats"][name]:
            raise ValueError("unplanned repetition")
        key = (point, name, repetition)
        operation = row.get("operation")
        _text(operation, "outcome.operation")
        if key in slots or operation in operations:
            raise ValueError("duplicate slot or shared operation; not a new independent call")
        operations.add(operation)
        if "known_cost_usd" in row and row["known_cost_usd"] is not None:
            _number(row["known_cost_usd"], "known_cost_usd")
        if "valid_decision" in row and type(row["valid_decision"]) is not bool:
            raise ValueError("valid_decision must be a literal Boolean")
        slots[key] = row
    return slots


def analyze(spec, frame_rows, outcome_rows):
    """Analyze exactly the supplied planned frame; absent slots remain unknown.

    Frame rows require point_id, run_id, payload_sha256 and strategy_sha256.
    Outcomes follow the owner format: task (= point_id), arm (A1/B2 or A/B
    when that arm has one repetition), operation, shutdown_confirmed, record,
    valid_decision, complete decision, category and optional known_cost_usd.
    No result grants admission, new calls, adoption or task-quality support.
    """
    try:
        spec = validate(spec)
    except UnsupportedSpec as error:
        return {"status": "unsupported", "reason": str(error), "admissible": False,
                "beyond_reach": False, "quality_adoption": False}
    frame = json.loads(canonical(list(frame_rows)))
    rows = json.loads(canonical(list(outcome_rows)))
    seen = set()
    for point in frame:
        if type(point) is not dict:
            raise ValueError("frame row must be an object")
        for field in ("point_id", "run_id"):
            _text(point.get(field), field)
        if point["point_id"] in seen:
            raise ValueError("duplicate frame point")
        seen.add(point["point_id"])
        _sha(point.get("payload_sha256"), "frame.payload_sha256")
        if point.get("strategy_sha256") != spec["baseline_policy_sha256"]:
            raise ValueError("frame point is not at the declared baseline policy")
    frame.sort(key=lambda p: p["point_id"])
    slots = _slots(spec, frame, rows)
    eligibility = {p["point_id"]: _applies(spec, p) for p in frame}
    eligible = [p for p in frame if eligibility[p["point_id"]] is True]
    unknown_applicability = [p["point_id"] for p in frame if eligibility[p["point_id"]] is None]
    bounded = spec["reading"]["id"] != "delivered_json_length"
    contrasts, aa_points, bb_points, rest_points = [], [], [], []
    registered_population = spec["reading"]["id"] == "zero_step_check_avoidance"
    population_complete = registered_population or not unknown_applicability
    rest_reading = {"id": "action_delivery", "version": 1,
                    "parameters": {"actions": ["rest"], "closed_non_delivery": "zero"}}
    arm_categories = {arm: Counter() for arm in ("A", "B")}
    known_calls = {arm: 0 for arm in ("A", "B")}
    for point in frame:
        pid = point["point_id"]
        per_arm = {}
        for arm, repeat in spec["repeats"].items():
            per_arm[arm] = []
            for number in range(1, repeat + 1):
                row = slots.get((pid, arm, number))
                arm_categories[arm][_category(row)] += 1
                value = _value(spec["reading"], row, point)
                known_calls[arm] += int(value is not None)
                per_arm[arm].append(value)
        if eligibility[pid] is True:
            contrasts.append(_point(point, per_arm["A"], per_arm["B"], bounded))
            if registered_population:
                rest_values = {arm: [_value(rest_reading, slots.get((pid, arm, n)))
                                    for n in range(1, spec["repeats"][arm] + 1)] for arm in ("A", "B")}
                rest_points.append(_point(point, rest_values["A"], rest_values["B"], True))
            if spec["aa"] == "shared_A":
                aa_points.append(_point(point, [per_arm["A"][0]], [per_arm["A"][1]], bounded))
            if spec["repeats"]["B"] >= 2:
                bb_points.append(_point(point, [per_arm["B"][0]], [per_arm["B"][1]], bounded))
    primary = _contrast(contrasts, spec, population_complete)
    interval = primary["run_bootstrap_interval_95"]
    status = "insufficient_evidence"
    if not eligible and population_complete:
        status = "not_applicable"
    elif interval is not None:
        prediction = spec["prediction"]
        oriented = interval if prediction["direction"] == "increase" else [-interval[1], -interval[0]]
        if oriented[0] > prediction["min_effect"]:
            status = "support"
        elif (oriented[1] <= -prediction["falsifier"]["min_effect"]
              if prediction["falsifier"]["kind"] == "nonpositive_interval"
              else oriented[1] < -prediction["falsifier"]["min_effect"]):
            status = "refutation"
    planned = len(frame) * sum(spec["repeats"].values())
    eligible_ids = {p["point_id"] for p in eligible}
    aa = {"status": "unknown", "reason": "no contemporaneous A/A requested", "extra_calls": 0}
    if spec["aa"] == "shared_A":
        aa = {
            "status": "measured" if aa_points and all(p["delta"] is not None for p in aa_points)
                      and population_complete else "unknown",
            "source": "contemporaneous A2 minus A1 at the same retained points",
            "shared_with_primary": True, "extra_calls": 0,
            "shared_operations": sorted(row["operation"] for key, row in slots.items()
                                        if key[0] in eligible_ids
                                        and key[1] == "A" and key[2] <= 2),
            "contrast": _contrast(aa_points, spec, population_complete),
            "historical_replies_used": 0,
            "binding_limit": "host must bind the same baseline, payload, model/settings and batch to these operations",
        }
    bb = {"status": "unknown", "reason": "fewer than two candidate repetitions requested", "extra_calls": 0}
    if spec["repeats"]["B"] >= 2:
        bb = {
            "status": "measured" if bb_points and all(p["delta"] is not None for p in bb_points)
                      and population_complete else "unknown",
            "source": "contemporaneous B2 minus B1 at the same retained points",
            "shared_with_primary": True, "extra_calls": 0,
            "shared_operations": sorted(row["operation"] for key, row in slots.items()
                                        if key[0] in eligible_ids and key[1] == "B" and key[2] <= 2),
            "contrast": _contrast(bb_points, spec, population_complete),
            "historical_replies_used": 0,
            "binding_limit": "host must bind the same candidate, payload, model/settings and batch to these operations",
        }
    result = {
        "schema": "procontract-decision-study-readout/v1", "spec_sha256": identity(spec),
        "frame": spec["frame"], "frame_rows_sha256": digest(frame),
        "status": status, "primary": primary, "points": contrasts,
        "planned_frame_points": len(frame), "eligible_points": len(eligible),
        "unknown_applicability": unknown_applicability,
        "inapplicable_points": len(frame) - len(eligible) - len(unknown_applicability),
        "planned_runs": len({p["run_id"] for p in frame}),
        "planned_calls": planned, "recorded_calls": len(rows),
        "known_calls": known_calls, "unknown_calls": planned - sum(known_calls.values()),
        "arm_categories": {arm: dict(counts) for arm, counts in arm_categories.items()},
        "known_cost_usd": sum(row.get("known_cost_usd") or 0 for row in rows),
        "calls_without_price": sum(row.get("known_cost_usd") is None for row in rows),
        "aa": aa, "bb": bb, "historical_replies_in_primary": 0,
        "admissible": False, "beyond_reach": False, "quality_adoption": False,
        "reading": "Fixed-frame decision delivery contrast; working run-bootstrap interval. "
                   "No semantic-rule, task-quality, unseen-task, equivalence or adoption conclusion.",
    }
    if registered_population:
        eligible_known = {arm: sum(v is not None for p in contrasts for v in p[arm]) for arm in ("A", "B")}
        eligible_calls = len(eligible) * sum(spec["repeats"].values())
        result.update(
            unscored_calls=result["unknown_calls"],
            unknown_calls=eligible_calls - sum(eligible_known.values()),
            eligible_calls=eligible_calls, eligible_known_calls=eligible_known,
            not_applicable_calls=result["inapplicable_points"] * sum(spec["repeats"].values()),
            unknown_applicability_calls=len(unknown_applicability) * sum(spec["repeats"].values()),
            population={"rule": "known_applicable", "reading": "zero_step_check_avoidance/1",
                        "author_metric_sha256": "7bd845c9f76f1f50b99ae09a247c0b2f1794029221d1f7e2b1a46d3c0b83148b",
                        "true": len(eligible), "false": result["inapplicable_points"],
                        "unknown": len(unknown_applicability),
                        "frame_applicability_fully_known": not unknown_applicability},
            rest_guardrail={"reading": rest_reading, "population": "same_known_applicable_points",
                            "contrast": _contrast(rest_points, spec, True), "points": rest_points,
                            "shared_with_primary": True, "extra_calls": 0,
                            "scope": "Rest delivery only; no stopping, quality or adoption gate"})
    return result
