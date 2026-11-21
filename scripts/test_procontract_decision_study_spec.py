"""Pure controls for fixed-frame missingness, clustering and shared A/A."""
import copy
import json
import unittest

import procontract_decision_study_spec as study


def spec(reading="action_delivery", a=2, b=2):
    parameters = {"closed_non_delivery": "zero"}
    if reading == "action_delivery":
        parameters["actions"] = ["check", "diagnose"]
    elif reading == "serialized_json_marker":
        parameters.update(words=["oracle", "bounded"], tokenizer="ascii_letters_min5_lower")
    return {
        "schema": study.SCHEMA,
        "candidate": {"source_id": "source-candidate", "sha256": "c" * 64},
        "frame": {"id": "frame-dev", "sha256": "f" * 64},
        "baseline_policy_sha256": "b" * 64,
        "reading": {"id": reading, "version": 1, "parameters": parameters},
        "prediction": {"direction": "increase", "min_effect": 0,
                       "falsifier": {"kind": "opposite_interval", "min_effect": 0}},
        "applicable": {"all": []}, "unknown_rule": "retain",
        "repeats": {"A": a, "B": b}, "ordering_seed": "frozen-order-1",
        "analysis_seed": 7, "cluster": "run_id", "bootstrap_replicates": 2000,
        "aa": "shared_A" if a >= 2 else "none",
    }


def point(name, run=None, **extra):
    return {"point_id": name, "run_id": run or "run-" + name,
            "payload_sha256": "d" * 64, "strategy_sha256": "b" * 64, **extra}


def outcome(name, arm, action="rest", reason="reason", **extra):
    return {"task": name, "arm": arm, "operation": f"op-{name}-{arm}",
            "shutdown_confirmed": True, "record": "retained-observation",
            "valid_decision": True, "action": action,
            "decision": {"choice": {"action": action, "reason": reason}},
            "known_cost_usd": .01, **extra}


class SpecTest(unittest.TestCase):
    def test_identity_is_order_stable_detached_and_binds_the_complete_design(self):
        original = spec()
        reordered = dict(reversed(list(original.items())))
        self.assertEqual(study.identity(original), study.identity(reordered))
        validated = study.validate(original, frame_registry={"frame-dev": "f" * 64})
        validated["reading"]["parameters"]["actions"].append("rest")
        self.assertNotEqual(study.identity(original), study.identity(validated))
        changed = copy.deepcopy(original)
        changed["analysis_seed"] += 1
        self.assertNotEqual(study.identity(original), study.identity(changed))
        with self.assertRaisesRegex(ValueError, "allowlist"):
            study.validate(original, frame_registry={"frame-dev": "e" * 64})

    def test_no_arbitrary_code_paths_providers_or_self_signed_admission(self):
        for key in ("scorer", "provider", "admissible", "host_path"):
            value = spec()
            value[key] = "/tmp/author.py"
            with self.subTest(key=key), self.assertRaises(ValueError):
                study.validate(value)
        value = spec()
        value["candidate"]["source_id"] = "/tmp/candidate"
        with self.assertRaisesRegex(ValueError, "host path"):
            study.validate(value)
        value = spec()
        value["reading"]["id"] = "semantic_correctness"
        self.assertEqual(study.analyze(value, [], [])["status"], "unsupported")

    def test_strict_json_types_repeats_and_delivery_definition(self):
        for count in (0, -1, True, 1.0):
            value = spec()
            value["repeats"]["A"] = count
            with self.subTest(count=count), self.assertRaises(ValueError):
                study.validate(value)
        for value in ({1: "key"}, {"bad": float("nan")}, {"bad": {1, 2}}):
            with self.assertRaises(ValueError):
                study.canonical(value)
        value = spec()
        value["reading"]["parameters"]["closed_non_delivery"] = "drop"
        with self.assertRaises(study.UnsupportedSpec):
            study.validate(value)
        value = spec()
        value["unknown_rule"] = "zero"
        with self.assertRaises(study.UnsupportedSpec):
            study.validate(value)

    def test_only_frozen_pre_input_conditions_are_expressible(self):
        value = spec()
        value["applicable"]["all"] = [{"field": "decision.action", "equals": "check"}]
        with self.assertRaises(study.UnsupportedSpec):
            study.validate(value)
        value["applicable"]["all"] = [{"field": "pre_input.flag", "equals": True}]
        result = study.analyze(value, [point("p", pre_input={"flag": 1})], [])
        self.assertEqual(result["status"], "not_applicable")


class AnalysisTest(unittest.TestCase):
    def test_empty_denominator_is_not_applicable_and_one_run_has_no_interval(self):
        empty = study.analyze(spec(), [], [])
        self.assertEqual(empty["status"], "not_applicable")
        self.assertIsNone(empty["primary"]["mean_difference"])
        self.assertIsNone(empty["primary"]["worst_case_bounds"])
        one = study.analyze(spec(a=1, b=1), [point("p")],
                            [outcome("p", "A"), outcome("p", "B", "check")])
        self.assertEqual(one["primary"]["mean_difference"], 1)
        self.assertIsNone(one["primary"]["run_bootstrap_interval_95"])
        self.assertEqual(one["status"], "insufficient_evidence")

    def test_missing_unresolved_and_unretained_are_unknown_not_zero(self):
        frame = [point("p")]
        rows = [outcome("p", "A1"), outcome("p", "A2"), outcome("p", "B1", "check")]
        unclassified = outcome("p", "B2", "check")
        del unclassified["valid_decision"]
        for additional in ([], [outcome("p", "B2", "check", shutdown_confirmed=False)],
                           [outcome("p", "B2", "check", record={})], [unclassified]):
            result = study.analyze(spec(), frame, rows + additional)
            self.assertIsNone(result["primary"]["mean_difference"])
            self.assertEqual(result["primary"]["worst_case_bounds"], [.5, 1])
            self.assertEqual(result["known_calls"], {"A": 2, "B": 1})
            self.assertEqual(result["unknown_calls"], 1)
            self.assertIsNone(result["points"][0]["mean_B"])
            self.assertEqual(result["status"], "insufficient_evidence")

    def test_closed_non_delivery_zero_requires_a_retained_closed_observation(self):
        row = outcome("p", "B", valid_decision=False, category="tool_request")
        result = study.analyze(spec(a=1, b=1), [point("p")], [outcome("p", "A", "check"), row])
        self.assertEqual(result["primary"]["mean_difference"], -1)
        self.assertEqual(result["arm_categories"]["B"], {"tool_request": 1})
        value = spec(a=1, b=1)
        value["reading"]["parameters"]["actions"] = ["tool_request"]
        self.assertEqual(study.analyze(value, [point("p")], [outcome("p", "A"), row])
                         ["primary"]["mean_difference"], 1)
        row["category"] = "check"
        self.assertEqual(study.analyze(spec(a=1, b=1), [point("p")], [outcome("p", "A"), row])
                         ["primary"]["mean_difference"], 0)

    def test_unequal_repetitions_are_point_arm_means_not_call_weighted(self):
        frame = [point("p"), point("q")]
        rows = [outcome("p", "A"), outcome("p", "B1", "check"), outcome("p", "B2"),
                outcome("q", "A"), outcome("q", "B1", "check"), outcome("q", "B2", "check")]
        result = study.analyze(spec(a=1, b=2), frame, rows)
        self.assertEqual(result["primary"]["mean_difference"], .75)
        self.assertEqual([p["delta"] for p in result["points"]], [.5, 1])
        self.assertEqual(result["planned_calls"], 6)

    def test_bootstrap_carries_whole_unequal_runs_and_recomputes_point_ratio(self):
        # r1 has (+1,-1), r2 has (+1). Resampled ratios can only be 0, 1/3, 1.
        # An independent point bootstrap could produce -1 and is not this method.
        frame = [point("p", "r1"), point("q", "r1"), point("s", "r2")]
        rows = [outcome("p", "A"), outcome("p", "B", "check"),
                outcome("q", "A", "check"), outcome("q", "B"),
                outcome("s", "A"), outcome("s", "B", "check")]
        value = spec(a=1, b=1)
        result = study.analyze(value, frame, rows)
        self.assertAlmostEqual(result["primary"]["mean_difference"], 1 / 3)
        self.assertEqual(result["primary"]["run_bootstrap_interval_95"], [0, 1])
        self.assertEqual(result["primary"]["run_count"], 2)
        reverse = study.analyze(value, list(reversed(frame)), list(reversed(rows)))
        self.assertEqual(result["primary"], reverse["primary"])
        self.assertEqual(result["frame_rows_sha256"], reverse["frame_rows_sha256"])

    def test_shared_contemporaneous_AA_does_not_duplicate_calls_or_cost(self):
        frame = [point("p"), point("q")]
        rows = [outcome(p["point_id"], arm, "check" if arm != "A1" else "rest")
                for p in frame for arm in ("A1", "A2", "B1", "B2")]
        result = study.analyze(spec(), frame, rows)
        self.assertEqual(result["recorded_calls"], 8)
        self.assertAlmostEqual(result["known_cost_usd"], .08)
        self.assertEqual(result["primary"]["mean_difference"], .5)
        self.assertEqual(result["aa"]["contrast"]["mean_difference"], 1)
        self.assertTrue(result["aa"]["shared_with_primary"])
        self.assertEqual(len(result["aa"]["shared_operations"]), 4)
        self.assertEqual(result["aa"]["extra_calls"], 0)
        self.assertEqual(result["historical_replies_in_primary"], 0)
        self.assertFalse(result["quality_adoption"])
        self.assertFalse(result["admissible"])

    def test_BB_shares_only_candidate_repetitions_and_preserves_unknowns(self):
        value, frame = spec(a=1, b=2), [point("p"), point("q")]
        rows = [outcome(p["point_id"], arm, "check" if arm == "B2" else "rest")
                for p in frame for arm in ("A", "B1", "B2")]
        result = study.analyze(value, frame, rows)
        self.assertEqual(result["aa"]["status"], "unknown")
        self.assertEqual(result["bb"]["status"], "measured")
        self.assertEqual(result["bb"]["contrast"]["mean_difference"], 1)
        self.assertEqual(result["primary"]["mean_difference"], .5)
        self.assertEqual(result["bb"]["shared_operations"],
                         ["op-p-B1", "op-p-B2", "op-q-B1", "op-q-B2"])
        self.assertTrue(result["bb"]["shared_with_primary"])
        self.assertEqual(result["bb"]["extra_calls"], 0)
        self.assertEqual(result["recorded_calls"], 6)
        self.assertAlmostEqual(result["known_cost_usd"], .06)
        missing = study.analyze(value, frame, rows[:-1])
        self.assertEqual(missing["bb"]["status"], "unknown")
        self.assertIsNone(missing["bb"]["contrast"]["mean_difference"])
        self.assertIsNone(missing["primary"]["mean_difference"])

    def test_unknown_eligibility_is_not_filtered_into_a_complete_result(self):
        value = spec(a=1, b=1)
        value["applicable"]["all"] = [{"field": "pre_input.has_counterexample", "equals": True}]
        frame = [point("p", pre_input={"has_counterexample": True}), point("q")]
        rows = [outcome(p["point_id"], arm, "check" if arm == "B" else "rest")
                for p in frame for arm in ("A", "B")]
        result = study.analyze(value, frame, rows)
        self.assertEqual(result["eligible_points"], 1)
        self.assertEqual(result["unknown_applicability"], ["q"])
        self.assertIsNone(result["primary"]["mean_difference"])
        self.assertIsNone(result["primary"]["worst_case_bounds"])

    def test_marker_is_literal_serialized_json_including_keys_quotes_and_negation(self):
        value = spec("serialized_json_marker", a=1, b=1)
        a = outcome("p", "A", reason="no oracle; ‘bounded’ is a quote")
        b = outcome("p", "B", reason="oracles unbounded")
        result = study.analyze(value, [point("p")], [a, b])
        self.assertEqual(result["primary"]["mean_difference"], -1)
        a["decision"]["bounded"] = False
        a["decision"]["choice"]["reason"] = "plain"
        self.assertEqual(study.analyze(value, [point("p")], [a, b])
                         ["primary"]["mean_difference"], -1)

    def test_delivered_length_has_no_finite_bound_for_unknown_output(self):
        value = spec("delivered_json_length", a=1, b=1)
        a = outcome("p", "A", reason="é😀")
        result = study.analyze(value, [point("p")], [a])
        self.assertIsNone(result["primary"]["mean_difference"])
        self.assertIsNone(result["primary"]["worst_case_bounds"])
        expected = len(json.dumps(a["decision"], ensure_ascii=False, sort_keys=True))
        self.assertEqual(result["points"][0]["mean_A"], expected)
        b = outcome("p", "B", valid_decision=False, category="refusal")
        self.assertEqual(study.analyze(value, [point("p")], [a, b])
                         ["primary"]["mean_difference"], -expected)

    def test_unplanned_historical_duplicate_and_wrong_baseline_rows_refuse(self):
        value, frame = spec(a=1, b=1), [point("p")]
        for rows in ([outcome("p", "H")], [outcome("p", "A"), outcome("p", "A1")],
                     [outcome("p", "A"), outcome("p", "B", operation="op-p-A")],
                     [outcome("foreign", "A")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                study.analyze(value, frame, rows)
        frame[0]["strategy_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "baseline"):
            study.analyze(value, frame, [])

    def test_support_refutation_and_insufficient_are_distinct(self):
        value = spec(a=1, b=1)
        frame = [point("p"), point("q")]
        for action_a, action_b, expected in (("rest", "check", "support"),
                                             ("check", "rest", "refutation"),
                                             ("rest", "rest", "insufficient_evidence")):
            rows = [row for p in frame for row in
                    (outcome(p["point_id"], "A", action_a), outcome(p["point_id"], "B", action_b))]
            self.assertEqual(study.analyze(value, frame, rows)["status"], expected)


if __name__ == "__main__":
    unittest.main()
