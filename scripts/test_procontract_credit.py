"""Credit assignment and improvement Contracts against the real kernel store; no model, grader
or container runs. Evaluator and continuation results are scripted."""

import contextlib
import os
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import procontract_credit as credit
import procontract_store as kernel

AVAILABLE = Path(kernel.store_bin()).exists() or bool(os.environ.get("PRO_CONTRACT_STORE_BIN"))


METHOD = credit.SAMPLED_METHODS[0]


def outcome(*passing, total=4):
    return {"cases": {f"c{i}": i in passing for i in range(total)}}


SERIES = credit.SERIES_METHODS[0]
# scipy.stats.t.ppf(0.975, df), scipy 1.18.1
STUDENT_975 = {1: 12.706204736174694, 2: 4.302652729749462, 3: 3.1824463052837078, 4: 2.7764451051977934,
               5: 2.5705818356363146, 6: 2.4469118511449786, 7: 2.364624251592784, 8: 2.306004135204166,
               9: 2.262157162798205, 10: 2.228138851986274, 15: 2.131449545559776, 16: 2.1199052992212546,
               30: 2.0422724563012378, 31: 2.039513446396408, 63: 1.998340542520741, 100: 1.9839715185235518,
               999: 1.9623414611334493, 1000: 1.9623390808264083}


def by_hand(points, lag):
    """The mean and the two standard errors of a series, written out: not the module's code."""
    b = len(points)
    m = sum(points) / b
    plain = (sum((x - m) ** 2 for x in points) / (b - 1) / b) ** 0.5
    g = [sum((points[i] - m) * (points[i - l] - m) for i in range(l, b)) / b for l in range(lag + 1)]
    return m, plain, ((g[0] + 2 * sum((1 - l / (lag + 1)) * g[l] for l in range(1, lag + 1))) / (b - 1)) ** 0.5


def in_order(values, **more):
    """One unit for each value, in that order, each with one state continued once."""
    roster = [{"unit": f"u{i:02d}", "states": [f"s{i:02d}"]} for i in range(len(values))]
    forks = [{"task": f"u{i:02d}", "family": "f", "state": f"s{i:02d}", "base": 0.5, "candidate": 0.5 + value}
             for i, value in enumerate(values)]
    return forks, {"method": SERIES, "lag": 3, "roster": roster, **more}

class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.states = [
            {"subject": "s0", "by": {"harness": "A", "action": "start"}, "cost": {"requests": 10}},
            {"subject": "s1", "by": {"harness": "A", "action": "resume"}, "cost": {"requests": 20}},
            {"subject": "s2", "by": {"harness": "A", "action": "successor"}, "cost": {"requests": 30}},
            {"subject": "s3", "by": {"harness": "B", "action": "successor"}, "cost": {"requests": 40}},
        ]
        self.outcomes = {"s0": outcome(0), "s1": outcome(0, 1, 2), "s3": outcome(1, 3)}

    def test_segments_add_up_and_name_what_moved(self):
        book = credit.ledger(self.states, self.outcomes)
        self.assertEqual([s["change"] for s in book["segments"]], [0.5, -0.25])
        self.assertAlmostEqual(sum(s["change"] for s in book["segments"]), book["change"])
        self.assertEqual(book["segments"][0]["acquired"], ["c1", "c2"])
        self.assertEqual((book["segments"][1]["acquired"], book["segments"][1]["lost"]), (["c3"], ["c0", "c2"]))

    def test_unmeasured_state_joins_the_next_segment(self):
        segment = credit.ledger(self.states, self.outcomes)["segments"][1]
        self.assertEqual(segment["cost"], {"requests": 70})
        self.assertEqual([by["harness"] for by in segment["by"]], ["A", "B"])

    def test_best_state_and_what_the_last_one_forgoes(self):
        book = credit.ledger(self.states, self.outcomes)
        self.assertEqual((book["best"], book["last"], book["forgone"]), ("s1", "s3", 0.25))

    def test_attribution_keeps_mixed_segments_apart_and_sums_to_the_total(self):
        book = credit.ledger(self.states, self.outcomes)
        rows = credit.attribute(book["segments"], lambda by: by["harness"])
        self.assertEqual(set(rows), {"A", "mixed"})
        self.assertAlmostEqual(sum(row["change"] for row in rows.values()), book["change"])
        self.assertEqual(rows["A"]["cost"], {"requests": 20})

    def test_no_measured_state(self):
        book = credit.ledger(self.states, {})
        self.assertEqual((book["change"], book["tail"]), (None, {"requests": 100}))

    def test_cost_is_conserved_across_prefix_segments_and_tail(self):
        states = self.states + [{"subject": "s4", "by": {}, "cost": {"requests": 5, "usd": 1}}]
        book = credit.ledger(states, self.outcomes)
        total = {}
        for part in [book["prefix"], book["tail"], *[s["cost"] for s in book["segments"]]]:
            credit._add(total, part)
        self.assertEqual((book["prefix"], book["tail"]), ({"requests": 10}, {"requests": 5, "usd": 1}))
        self.assertEqual(total, {"requests": 105, "usd": 1})

    def test_a_subtotal_says_how_much_of_its_window_it_covers(self):
        states = [{"subject": "a", "cost": {"usd": 0}}, {"subject": "b", "cost": {}},
                  {"subject": "c", "cost": {"usd": 2, "requests": 3}}]
        outcomes = {"a": {"passed": 1, "total": 2}, "c": {"passed": 2, "total": 2}}
        book = credit.ledger(states, outcomes)
        segment = book["segments"][0]
        # usd 0 was recorded by one state and usd 2 by another; one state recorded nothing.
        self.assertEqual((segment["cost"], segment["coverage"]),
                         ({"usd": 2, "requests": 3}, {"states": 2, "reported": {"usd": 1, "requests": 1}}))
        self.assertEqual(book["prefix_coverage"], {"states": 1, "reported": {"usd": 1}})
        row = credit.attribute(book["segments"], lambda by: "all")["all"]
        self.assertEqual(row["coverage"], {"states": 2, "reported": {"usd": 1, "requests": 1}})

    def test_counts_only_outcomes_do_not_claim_which_cases_moved(self):
        outcomes = {"s0": {"passed": 1, "total": 4}, "s1": {"passed": 3, "total": 4}}
        segment = credit.ledger(self.states, outcomes)["segments"][0]
        self.assertEqual((segment["change"], segment["acquired"], segment["lost"]), (0.5, None, None))
        row = credit.attribute([segment], lambda by: by["harness"])["A"]
        self.assertEqual((row["segments_without_cases"], row["acquired"]), (1, 0))

    def test_an_artifact_reached_twice_keeps_what_was_observed_each_time(self):
        states = [{"id": 1, "subject": "A", "by": {}}, {"id": 2, "subject": "B", "by": {}},
                  {"id": 3, "subject": "A", "by": {}}]
        observed = {1: {"passed": 0, "total": 4}, 2: {"passed": 2, "total": 4}, 3: {"passed": 4, "total": 4}}
        book = credit.ledger(states, observed)
        self.assertEqual(([s["change"] for s in book["segments"]], book["change"], book["last"]),
                         ([0.5, 0.5], 1.0, 3))


@unittest.skipUnless(AVAILABLE, "pro-contract-store binary is not built")
class MeasureTest(unittest.TestCase):
    """What a measure is and what counts as its outcome; digests use the store binary."""

    def setUp(self):
        base = {"pool": "p", "evaluator": "e", "environment": "env"}
        self.one = dict(base, cases=credit.case_set(["a", "b"]), total=2)
        self.graded = dict(self.one, scale="score")
        self.by_unit = dict(base, scale="score", units={
            "q1": {"cases": credit.case_set(["q1-resolution"]), "total": 1},
            "q2": {"cases": credit.case_set(["q2-low", "q2-high"]), "total": 2}})

    def test_a_measure_has_one_case_set_for_every_state_or_one_for_each_unit(self):
        for registration in (self.one, self.graded, self.by_unit):
            credit.measure_id(registration)
        self.assertNotEqual(credit.measure_id(self.one), credit.measure_id(self.graded))
        base = {"pool": "p", "evaluator": "e", "environment": "env"}
        for bad in (base, dict(self.one, units=self.by_unit["units"]), dict(base, cases="c"),
                    dict(base, units={}), dict(base, units={"q": {"cases": "c"}}), dict(base, units={"q": {"cases": "c", "total": 0}}),
                    dict(base, units={"q": {"cases": "c", "total": True}}), dict(base, units=["q"]),
                    dict(base, units={"q": {"cases": "c", "total": 1}}, total=1)):
            with self.assertRaisesRegex(ValueError, "a measure", msg=str(bad)):
                credit.measure_id(bad)
        with self.assertRaisesRegex(ValueError, "unknown scale"):
            credit.measure_id(dict(self.one, scale="percent"))

    def test_a_score_is_a_number_from_0_to_1_and_a_pass_is_true_or_false(self):
        self.assertEqual(credit.check_outcome(self.graded, {"cases": {"a": 0.25, "b": 1}}), {"cases": {"a": 0.25, "b": 1}})
        for result in (True, False, "0.5", None, -0.1, 1.5, float("nan"), float("inf")):
            with self.assertRaisesRegex(ValueError, "a score from 0 to 1", msg=repr(result)):
                credit.check_outcome(self.graded, {"cases": {"a": 0.5, "b": result}})
        for result in (0.5, 1, 0):
            with self.assertRaisesRegex(ValueError, "exactly true or false", msg=repr(result)):
                credit.check_outcome(self.one, {"cases": {"a": True, "b": result}})
        self.assertEqual(credit.value({"cases": {"a": 0.25, "b": 0.75}}, "score"), 0.5)
        self.assertEqual(credit.value({"cases": {"a": True, "b": False}}), 0.5)
        self.assertIsNone(credit.value({"cases": {"a": True, "b": 0.5}}, "score"))   # mixed results are not results
        self.assertIsNone(credit.value({"cases": {"a": 0.5, "b": 2}}, "score"))

    def test_what_a_result_is_comes_from_the_registered_scale_not_from_how_it_looks(self):
        numbers, passes = {"cases": {"c0": 0, "c1": 1}}, {"cases": {"c0": False, "c1": True}}
        self.assertIsNone(credit.value(numbers))                 # as before scores existed: unknown
        self.assertIsNone(credit.cases(numbers))
        self.assertEqual(credit.value(numbers, "score"), 0.5)
        self.assertEqual(credit.value(passes), 0.5)
        self.assertIsNone(credit.value(passes, "score"))         # true and false are not scores
        states = [{"subject": "s0", "cost": {}}, {"subject": "s1", "cost": {}}]
        old = credit.ledger(states, {"s0": {"cases": {"c0": 0, "c1": 0}}, "s1": {"cases": {"c0": 0, "c1": 1}}})
        self.assertEqual((old["change"], old["segments"]), (None, []))
        graded = credit.ledger(states, {"s0": {"cases": {"c0": 0, "c1": 0}}, "s1": {"cases": {"c0": 0, "c1": 1}}}, scale="score")
        self.assertEqual(graded["change"], 0.5)

    def test_under_a_measure_by_unit_an_outcome_is_of_its_own_unit(self):
        good = {"unit": "q2", "cases": {"q2-low": 0.5, "q2-high": 1.0}}
        self.assertIs(credit.check_outcome(self.by_unit, good), good)
        self.assertIs(credit.check_outcome(self.by_unit, good, unit="q2"), good)
        with self.assertRaisesRegex(ValueError, "of unit q2, not of the state's unit q1"):
            credit.check_outcome(self.by_unit, good, unit="q1")
        for bad in ({"cases": good["cases"]}, dict(good, unit="q9"), dict(good, unit=2)):
            with self.assertRaisesRegex(ValueError, "names no unit of the measure", msg=str(bad)):
                credit.check_outcome(self.by_unit, bad)
        with self.assertRaisesRegex(ValueError, "not over the measure's registered cases"):
            credit.check_outcome(self.by_unit, {"unit": "q1", "cases": good["cases"]})     # another unit's cases
        with self.assertRaisesRegex(ValueError, "not over the measure's registered cases"):
            credit.check_outcome(self.by_unit, {"unit": "q2", "cases": {"q2-low": 0.5}})

    def test_which_cases_moved_is_known_only_for_pass_and_fail(self):
        states = [{"subject": "s0", "cost": {"usd": 1}}, {"subject": "s1", "cost": {"usd": 1}}]
        graded = credit.ledger(states, {"s0": {"cases": {"a": 0.25, "b": 0.5}}, "s1": {"cases": {"a": 0.75, "b": 0.5}}}, scale="score")
        self.assertEqual(graded["change"], 0.25)
        self.assertEqual((graded["segments"][0]["acquired"], graded["segments"][0]["lost"]), (None, None))
        passed = credit.ledger(states, {"s0": {"cases": {"a": False, "b": True}}, "s1": {"cases": {"a": True, "b": True}}})
        self.assertEqual((passed["segments"][0]["acquired"], passed["segments"][0]["lost"]), (["a"], []))


class EffectTest(unittest.TestCase):
    def stratified(self, rare=7, certain=140, probability=0.1):
        frame = ([{"state": f"c{i}", "unit": f"c{i}", "stratum": "certain"} for i in range(certain)]
                 + [{"state": f"r{i}", "unit": f"r{i}", "stratum": "rare"} for i in range(rare)])
        design = {"estimand": "state_mean", "method": METHOD, "population": credit.population_id(frame),
                  "strata": {"certain": 1.0, "rare": probability}}
        return frame, design

    def test_a_sampled_estimate_is_for_the_whole_population(self):
        # 100 states: 10 where the change matters (+0.5), all drawn; 90 where it does not, drawn one in nine.
        frame = ([{"state": f"a{i}", "unit": f"a{i}", "stratum": "changed"} for i in range(10)]
                 + [{"state": f"b{i}", "unit": f"b{i}", "stratum": "unchanged"} for i in range(90)])
        design = {"estimand": "state_mean", "method": METHOD, "population": credit.population_id(frame),
                  "strata": {"changed": 1.0, "unchanged": 1 / 9}}
        whole = credit.population(design, frame)
        self.assertEqual(whole["total"], 100.0)
        self.assertAlmostEqual(whole["variance_bound"], (10 * 1 + 90 * 9) / 100 ** 2)
        self.assertAlmostEqual(whole["sampling_variance_bound"], 90 * 8 / 100 ** 2)
        forks = [{"task": f"a{i}", "family": "f", "base": 0.25, "candidate": 0.75, "weight": 1.0} for i in range(10)]
        forks += [{"task": f"b{i}", "family": "f", "base": 0.5, "candidate": 0.5, "weight": 9.0} for i in range(10)]
        measured = credit.effect(forks, population=whole)
        self.assertTrue(measured["sampled"])
        self.assertAlmostEqual(measured["mean"], 0.05)          # 10 * 0.5 / 100, the population's mean
        half = (20 * whole["variance_bound"]) ** 0.5
        self.assertAlmostEqual(measured["low"], 0.05 - half)
        self.assertAlmostEqual(measured["high"], 0.05 + half)
        self.assertEqual(measured["method"], "horvitz-thompson/chebyshev-95/1")
        self.assertIn("which states are drawn does not depend on what their forks return", measured["interval"]["assumes"])
        self.assertEqual((measured["tasks"], measured["forks"], measured["floor"], measured["families"]), (20, 20, None, {}))
        unweighted = credit.effect([{k: v for k, v in fork.items() if k != "weight"} for fork in forks])
        self.assertAlmostEqual(unweighted["mean"], 0.25)        # the sample's own mean, which is not the population's

    def test_a_stratum_that_was_not_drawn_is_not_certainty(self):
        """140 units always drawn, each +0.05; 7 units drawn one time in ten, each -1: the population's
        mean is zero. Nearly half the draws contain none of the seven. Resampling the 140 that were
        drawn would then report +0.05 with no spread; the design's interval still covers zero."""
        frame, design = self.stratified()
        whole = credit.population(design, frame)
        truth = {row["state"]: 0.05 if row["stratum"] == "certain" else -1.0 for row in frame}
        self.assertAlmostEqual(sum(truth.values()), 0.0)
        covered = empty = met = 0
        for attempt in range(300):
            drawn = credit.sample(design, frame, f"seed-{attempt}")
            forks = [{"task": state, "base": 1.0 if truth[state] < 0 else 0.5,
                      "candidate": (1.0 if truth[state] < 0 else 0.5) + truth[state], "weight": weight}
                     for state, weight in drawn.items()]
            measured = credit.effect(forks, population=whole)
            empty += not any(state.startswith("r") for state in drawn)
            covered += measured["low"] <= 0 <= measured["high"]
            met += not credit.met({"min_tasks": 100, "min_effect": 0.01, "lower_bound": 0}, measured)
        self.assertTrue(110 < empty < 180, empty)               # 0.9 ** 7 = 0.478 of 300
        self.assertEqual((covered, met), (300, 0))

    def test_the_interval_covers_whatever_the_outcomes_are(self):
        """The worst case for the bound: every difference is +1 or -1, most states are rarely drawn,
        and each fork's own result is a coin weighted by its state."""
        import random as rnd
        rng = rnd.Random(7)
        frame = [{"state": f"s{i}", "unit": f"u{i % 40}", "stratum": "often" if i % 5 == 0 else "seldom"} for i in range(400)]
        for estimand in ("state_mean", "task_mean"):
            design = {"estimand": estimand, "method": METHOD, "population": credit.population_id(frame), "strata": {"often": 0.8, "seldom": 0.2}}
            whole = credit.population(design, frame)
            chance = {row["state"]: rng.random() for row in frame}                 # P(the fork returns +1)
            shares = credit._shares(design, frame)
            truth = sum(shares[s] * (2 * p - 1) for s, p in chance.items()) / whole["total"]
            covered = 0
            for attempt in range(400):
                drawn = credit.sample(design, frame, f"{estimand}-{attempt}")
                forks = []
                for state, weight in drawn.items():
                    up = rng.random() < chance[state]
                    forks.append({"task": state, "base": 0.0 if up else 1.0, "candidate": 1.0 if up else 0.0, "weight": weight})
                measured = credit.effect(forks, population=whole)
                covered += measured["low"] <= truth <= measured["high"]
            self.assertGreaterEqual(covered, 380, estimand)                        # at least 95 in 100; in fact nearly all

    def test_weights_and_the_population_come_together(self):
        fork = {"task": "t", "base": 0.0, "candidate": 0.5}
        whole = {"method": METHOD, "total": 4.0, "variance_bound": 0.5}
        for weight in (0, -1, True, "2", float("inf")):
            with self.assertRaisesRegex(ValueError, "positive weight", msg=repr(weight)):
                credit.effect([dict(fork, weight=weight)], population=whole)
        with self.assertRaisesRegex(ValueError, "positive weight"):
            credit.effect([dict(fork, weight=1.0), dict(fork, task="u")], population=whole)
        with self.assertRaisesRegex(ValueError, "both its forks' weights and its population"):
            credit.effect([dict(fork, weight=1.0)])
        with self.assertRaisesRegex(ValueError, "both its forks' weights and its population"):
            credit.effect([fork], population=whole)
        for bad in ({"total": 0, "variance_bound": 1}, {"total": 4.0}, {"total": 4.0, "variance_bound": True}):
            with self.assertRaisesRegex(ValueError, "population's total and its variance bound", msg=str(bad)):
                credit.effect([dict(fork, weight=1.0)], population=dict(bad, method=METHOD))
        for bad in ({"total": 4.0, "variance_bound": 0.5}, {"method": "bootstrap", "total": 4.0, "variance_bound": 0.5}):
            with self.assertRaisesRegex(ValueError, "by a method the terms name", msg=str(bad)):
                credit.effect([dict(fork, weight=1.0)], population=bad)
        with self.assertRaisesRegex(ValueError, "no noise floor is defined for a sampled estimate"):
            credit.effect([dict(fork, weight=1.0)], [dict(fork, task="n")], population=whole)
        shared = [dict(fork, weight=1.0, base_cost=1, candidate_cost=1, base_execution="x", candidate_execution="a"),
                  dict(fork, task="u", weight=1.0, base_cost=1, candidate_cost=1, base_execution="x", candidate_execution="b")]
        with self.assertRaisesRegex(ValueError, "several forks share"):
            credit.effect(shared, population=whole)
        costed = [dict(fork, weight=3.0, base_cost=2, candidate_cost=4), dict(fork, task="u", weight=1.0, base_cost=2, candidate_cost=2)]
        measured = credit.effect(costed, population=whole)
        self.assertEqual(measured["cost_ratio"], 14 / 8)
        self.assertEqual((measured["cost_base"], measured["cost_candidate"]), (2.0, 3.5))   # estimated per state of the population
        self.assertEqual((measured["paid_base"], measured["paid_candidate"]), (4, 6))       # what these executions cost
        one = credit.effect([dict(fork, weight=10.0, base_cost=2, candidate_cost=4)], population={"method": METHOD, "total": 10.0, "variance_bound": 1.0})
        self.assertEqual((one["cost_base"], one["cost_candidate"], one["paid_base"], one["paid_candidate"]), (2.0, 4.0, 2, 4))

    def test_a_sample_follows_from_the_design_the_frame_and_the_draws_seed(self):
        frame = [{"state": f"s{i}", "unit": f"u{i // 4}", "stratum": "changed" if i % 10 == 0 else "unchanged"}
                 for i in range(4000)]
        design = {"estimand": "state_mean", "method": METHOD, "population": credit.population_id(frame),
                  "strata": {"changed": 1.0, "unchanged": 0.25}}
        drawn = credit.sample(design, frame, "draw-1")
        self.assertEqual(drawn, credit.sample(design, list(reversed(frame)), "draw-1"))   # recomputable, not an order
        changed = {row["state"] for row in frame if row["stratum"] == "changed"}
        self.assertTrue(changed <= set(drawn))
        self.assertTrue(all(drawn[state] == 1.0 for state in changed))
        others = [state for state in drawn if state not in changed]
        self.assertTrue(all(drawn[state] == 4.0 for state in others))
        self.assertTrue(800 < len(others) < 1000, len(others))                            # a quarter of 3,600
        self.assertNotEqual(set(drawn), set(credit.sample(design, frame, "draw-2")))
        by_task = credit.sample(dict(design, estimand="task_mean"), frame, "draw-1")
        self.assertEqual(set(by_task), set(drawn))
        self.assertTrue(all(by_task[state] == drawn[state] / 4 for state in drawn))      # four states to a unit
        # The estimate over the drawn states recovers the population's mean; the plain mean does not.
        forks = [{"task": row["unit"], "base": 0.0, "candidate": 0.5 if row["stratum"] == "changed" else 0.0,
                  "weight": drawn[row["state"]]} for row in frame if row["state"] in drawn]
        self.assertAlmostEqual(credit.effect(forks, population=credit.population(design, frame))["mean"], 0.05)  # 400 * 0.5 / 4000
        plain = credit.effect([{k: v for k, v in fork.items() if k != "weight"} for fork in forks])
        self.assertGreater(plain["mean"], 0.12)

    def test_a_population_is_its_states_and_units_whatever_their_strata(self):
        frame = [{"state": f"s{i}", "unit": f"u{i // 2}", "stratum": "a"} for i in range(6)]
        other_strata = [dict(row, stratum="b") for row in frame]
        self.assertEqual(credit.population_id(frame), credit.population_id(list(reversed(other_strata))))
        self.assertNotEqual(credit.population_id(frame), credit.population_id(frame[:-1]))
        self.assertNotEqual(credit.population_id(frame), credit.population_id([dict(frame[0], unit="u9")] + frame[1:]))
        design = {"estimand": "state_mean", "method": METHOD, "population": credit.population_id(frame), "strata": {"a": 1.0, "b": 0.5}}
        with self.assertRaisesRegex(ValueError, "not the population the design names"):
            credit.sample(design, frame[:-1], "seed")           # a state dropped from the frame
        with self.assertRaisesRegex(ValueError, "not the population the design names"):
            credit.sample(design, frame + [{"state": "extra", "unit": "u0", "stratum": "a"}], "seed")
        self.assertEqual(len(credit.sample(design, frame, "seed")), 6)

    def test_a_public_seed_would_let_strata_steer_the_sample(self):
        """Why the seed is not in the terms: with it known, putting in the low-probability stratum
        exactly the states whose number is too high to be drawn leaves none of them in the
        sample, although the terms say one in ten. So the seed is the host's, drawn afterwards."""
        states = [f"s{i}" for i in range(1000)]
        seed = "known-in-advance"
        frame = [{"state": state, "unit": state, "stratum": "changed" if credit._draw(seed, state) < 0.1 else "unchanged"}
                 for state in states]
        design = {"estimand": "state_mean", "method": METHOD, "population": credit.population_id(frame),
                  "strata": {"changed": 1.0, "unchanged": 0.1}}
        steered = credit.sample(design, frame, seed)
        unchanged = [row["state"] for row in frame if row["stratum"] == "unchanged"]
        self.assertGreater(len(unchanged), 800)
        self.assertEqual([state for state in unchanged if state in steered], [])
        fresh = credit.sample(design, frame, "drawn-after-the-frame-was-fixed")
        self.assertTrue(50 < len([state for state in unchanged if state in fresh]) < 130)

    def test_a_design_and_a_frame_are_complete(self):
        frame = [{"state": "s1", "unit": "u", "stratum": "a"}]
        good = {"estimand": "state_mean", "method": METHOD, "population": credit.population_id(frame), "strata": {"a": 0.5}}
        for change in ({"estimand": "best"}, {"population": ""}, {"strata": {}}, {"strata": {"a": 0}}, {"strata": {"a": 1.5}},
                       {"strata": {"a": True}}, {"strata": ["a"]}, {"seed": "in the terms"}):
            with self.assertRaisesRegex(ValueError, "a sampling design states", msg=str(change)):
                credit.sample(dict(good, **change), frame, "seed")
        for method in (None, "bootstrap-of-drawn-units", "horvitz-thompson/chebyshev-95/2"):
            with self.assertRaisesRegex(ValueError, "names no estimation method this module implements", msg=repr(method)):
                credit.sample(dict(good, method=method), frame, "seed")
        with self.assertRaisesRegex(ValueError, "names no estimation method"):
            credit.sample({k: v for k, v in good.items() if k != "method"}, frame, "seed")
        for seed in ("", None, 7):
            with self.assertRaisesRegex(ValueError, "a draw has a seed"):
                credit.sample(good, frame, seed)
        two = frame + [{"state": "s2", "unit": "u", "stratum": "b"}]
        with self.assertRaisesRegex(ValueError, "no probability for stratum b"):
            credit.sample(dict(good, population=credit.population_id(two)), two, "seed")
        with self.assertRaisesRegex(ValueError, "each state once"):
            credit.sample(good, frame + frame, "seed")
        for bad in ([{"state": "s1", "unit": "u"}], [{"state": 1, "unit": "u", "stratum": "a"}], {"s1": "a"}, None):
            with self.assertRaisesRegex(ValueError, "lists each state with its unit and stratum", msg=repr(bad)):
                credit.sample(good, bad, "seed")

    def test_the_cost_of_an_execution_several_forks_name_is_counted_once(self):
        forks = [{"task": "t", "base": 0.25, "candidate": 0.75, "base_cost": 10, "candidate_cost": 20,
                  "base_execution": "control", "candidate_execution": "a"},
                 {"task": "t", "base": 0.25, "candidate": 0.5, "base_cost": 10, "candidate_cost": 30,
                  "base_execution": "control", "candidate_execution": "b"}]
        measured = credit.effect(forks)
        self.assertEqual((measured["cost_base"], measured["cost_candidate"], measured["cost_ratio"]), (10, 50, 5.0))
        unnamed = [{key: value for key, value in fork.items() if not key.endswith("_execution")} for fork in forks]
        self.assertEqual(credit.effect(unnamed)["cost_base"], 20)  # without identities each fork's cost stands alone

    def test_tasks_are_the_units_and_forks_of_a_task_are_averaged(self):
        forks = [{"task": "t1", "base": 0.2, "candidate": 0.6}, {"task": "t1", "base": 0.2, "candidate": 0.4},
                 {"task": "t2", "base": 0.5, "candidate": 0.5}]
        measured = credit.effect(forks)
        self.assertEqual((measured["tasks"], measured["wins"], measured["losses"]), (2, 1, 0))
        self.assertAlmostEqual(measured["mean"], 0.15)

    def test_noise_floor_and_criterion(self):
        forks = [{"task": f"t{i}", "family": "f", "base": 0.5, "candidate": 0.6, "base_cost": 10,
                  "candidate_cost": 12} for i in range(8)]
        null = [{"task": f"n{i}", "family": "f", "base": 0.5, "candidate": 0.5 + (0.02 if i % 2 else -0.02)}
                for i in range(8)]
        measured = credit.effect(forks, null)
        self.assertLess(measured["floor"], 0.05)
        criterion = {"min_tasks": 8, "min_effect": 0.05, "above_noise": True, "lower_bound": 0,
                     "families_nonnegative": True, "max_cost_ratio": 1.25}
        self.assertEqual(credit.met(criterion, measured), [])
        self.assertEqual(credit.met(dict(criterion, max_cost_ratio=1.1), measured), ["cost above the stated ratio"])
        self.assertEqual(credit.met({"min_tasks": 9}, measured), ["too few tasks"])
        self.assertIn("effect not above the noise of identical continuations",
                      credit.met({"above_noise": True}, credit.effect(forks)))

    def test_the_floor_is_for_the_effects_own_statistic(self):
        # One treated task against a hundred null tasks that each differ by 0.3 in absolute value:
        # a mean over one task is as noisy as one null task, not as a mean over a hundred.
        forks = [{"task": "t", "family": "f", "base": 0.5, "candidate": 0.7}]
        null = [{"task": f"n{i}", "family": "f", "base": 0.5, "candidate": 0.5 + (0.3 if i % 2 else -0.3)}
                for i in range(100)]
        measured = credit.effect(forks, null)
        self.assertAlmostEqual(measured["floor"], 0.3)
        self.assertIsNone(measured["low"])
        self.assertEqual(credit.met({"above_noise": True, "lower_bound": 0}, measured), [
            "effect not above the noise of identical continuations",
            "interval reaches the stated bound or is undefined"])
        with self.assertRaisesRegex(ValueError, "do not cover task family"):
            credit.effect(forks, [dict(row, family="g") for row in null])

    def test_unknown_cost_is_not_zero(self):
        forks = [{"task": "t1", "base": 0.1, "candidate": 0.5, "base_cost": 1.0, "candidate_cost": None},
                 {"task": "t2", "base": 0.1, "candidate": 0.5, "base_cost": 1.0, "candidate_cost": 1.0}]
        measured = credit.effect(forks)
        self.assertIsNone(measured["cost_ratio"])
        self.assertEqual(credit.met({"max_cost_ratio": 1.1}, measured), ["cost unknown in the stated unit"])

    def test_known_zero_cost_is_known(self):
        free = [{"task": f"t{i}", "base": 0.1, "candidate": 0.5, "base_cost": 0, "candidate_cost": 0} for i in range(2)]
        measured = credit.effect(free)
        self.assertEqual((measured["cost_base"], measured["cost_candidate"], measured["cost_ratio"]), (0, 0, None))
        self.assertEqual(credit.met({"max_cost_ratio": 1.1}, measured), [])
        paid = [dict(fork, candidate_cost=1) for fork in free]
        self.assertEqual(credit.met({"max_cost_ratio": 1.1}, credit.effect(paid)), ["cost above the stated ratio"])

    def test_an_estimate_over_a_series_is_the_stated_formula(self):
        """Sixteen units with one to three states each, some states continued twice."""
        values = [0.07, 0.05, -0.02, 0.09, 0.01, 0.03, 0.08, -0.01, 0.04, 0.06, 0.02, 0.05, 0.0, 0.07, 0.03, 0.04]
        roster, forks = [], []
        for i, value in enumerate(values):
            states = [f"d{i:02d}/q{j}" for j in range(1 + i % 3)]
            roster.append({"unit": f"d{i:02d}", "states": states})
            for j, state in enumerate(states):
                level = value + (0.02 * (1 - j) if len(states) > 1 else 0.0)
                for repeat in ((0.01, -0.01) if j == 0 and i % 4 == 0 else (0.0,)):
                    forks.append({"task": f"d{i:02d}", "family": "even" if i % 2 == 0 else "odd", "state": state,
                                  "base": 0.4, "candidate": 0.4 + level + repeat, "base_cost": 3, "candidate_cost": 4})
        by_state: dict = {}
        for fork in forks:
            by_state.setdefault(fork["state"], []).append(fork["candidate"] - fork["base"])
        points = []
        for row in roster:
            states = [sum(by_state[state]) / len(by_state[state]) for state in row["states"]]
            points.append(sum(states) / len(states))
        mean, plain, bartlett = by_hand(points, 3)
        measured = credit.effect(forks, series={"method": SERIES, "lag": 3, "roster": roster})
        self.assertEqual((measured["tasks"], measured["forks"], measured["method"]), (16, len(forks), SERIES))
        self.assertAlmostEqual(measured["mean"], mean, places=14)
        self.assertAlmostEqual(measured["interval"]["plain_se"], plain, places=14)
        self.assertAlmostEqual(measured["interval"]["bartlett_se"], bartlett, places=14)
        error = max(plain, bartlett)
        self.assertEqual(measured["interval"]["se"], max(measured["interval"]["plain_se"], measured["interval"]["bartlett_se"]))
        self.assertAlmostEqual(measured["low"], mean - STUDENT_975[15] * error, places=13)
        self.assertAlmostEqual(measured["high"], mean + STUDENT_975[15] * error, places=13)
        self.assertEqual((measured["interval"]["df"], measured["interval"]["lag"], measured["interval"]["nominal"]), (15, 3, 0.95))
        # A unit counts once: the mean is not the mean over forks, which the larger units would pull.
        self.assertNotAlmostEqual(mean, sum(f["candidate"] - f["base"] for f in forks) / len(forks), places=4)
        self.assertAlmostEqual(measured["families"]["even"], sum(points[0::2]) / 8, places=14)
        self.assertEqual((measured["wins"], measured["losses"]), (sum(x > 0 for x in points), sum(x < 0 for x in points)))
        self.assertEqual((measured["cost_base"], measured["cost_candidate"]), (3 * len(forks), 4 * len(forks)))
        self.assertIsNone(measured["floor"])
        # With no lag the second standard error is the plain one.
        flat = credit.effect(forks, series={"method": SERIES, "lag": 0, "roster": roster})["interval"]
        self.assertAlmostEqual(flat["bartlett_se"], flat["plain_se"], places=15)

    def test_the_students_point_is_the_tabulated_one(self):
        for df, expected in STUDENT_975.items():
            self.assertAlmostEqual(credit._student_95(df) / expected, 1.0, places=13, msg=str(df))
            self.assertAlmostEqual(credit._two_sided(expected, df), 0.95, places=13, msg=str(df))

    def test_order_matters_to_a_series_and_not_to_resampling(self):
        """The same sixteen differences, eight of 0.07 and eight of 0.01, in two orders. Resampling
        the units cannot tell the orders apart. In blocks, neighbours move together and the series
        interval is wider than the plain one; alternating, they move against each other, the
        second standard error is the smaller, and the plain one is used."""
        blocks, alternating = [0.07] * 8 + [0.01] * 8, [0.07, 0.01] * 8
        resampled = [credit.effect([{"task": f"u{i:02d}", "base": 0.5, "candidate": 0.5 + value}
                                    for i, value in enumerate(values)]) for values in (blocks, alternating)]
        self.assertAlmostEqual(resampled[0]["low"], resampled[1]["low"], places=12)
        self.assertAlmostEqual(resampled[0]["high"], resampled[1]["high"], places=12)
        first, second = (credit.effect(forks, series=series)
                         for forks, series in (in_order(values) for values in (blocks, alternating)))
        self.assertAlmostEqual(first["mean"], second["mean"], places=14)
        self.assertAlmostEqual(first["interval"]["plain_se"], second["interval"]["plain_se"], places=14)
        self.assertGreater(first["interval"]["bartlett_se"], 1.5 * first["interval"]["plain_se"])
        self.assertEqual(first["interval"]["se"], first["interval"]["bartlett_se"])
        self.assertLess(second["interval"]["bartlett_se"], second["interval"]["plain_se"])
        self.assertEqual(second["interval"]["se"], second["interval"]["plain_se"])
        self.assertLess(first["low"], second["low"])
        for measured, values in ((first, blocks), (second, alternating)):
            mean, plain, bartlett = by_hand([0.5 + value - 0.5 for value in values], 3)
            self.assertAlmostEqual(measured["low"], mean - STUDENT_975[15] * max(plain, bartlett), places=13)
        # The criterion reads the series interval: 0.04 +- 0.0165 resampled, but in blocks the bound is lower.
        self.assertEqual(credit.met({"lower_bound": 0.02}, resampled[0]), [])
        self.assertEqual(credit.met({"lower_bound": 0.02}, first), ["interval reaches the stated bound or is undefined"])

    def test_a_fixed_lag_understates_dependence_that_lies_within_it(self):
        """Why the interval is nominal even with many units. Units e_t + e_(t-1), with e = +-0.1 at
        random, are independent beyond one position and their mean is close to normal. The mean's
        true variance times b is 4 Var(e) = 0.04; the plain error tends to 2 Var(e) and the
        Bartlett one at lag 3 to 3.5 Var(e), so the interval is too short by a factor
        sqrt(7/8) and covers about 93.3%, not 95%, however many units there are."""
        rng = random.Random(20261117)
        noise = [rng.choice((-0.1, 0.1)) for _ in range(20001)]
        forks, series = in_order([0.05 + noise[i] + noise[i + 1] for i in range(20000)])
        interval = credit.effect(forks, series=series)["interval"]
        self.assertAlmostEqual(20000 * interval["plain_se"] ** 2 / 0.01, 2.0, delta=0.1)
        self.assertAlmostEqual(20000 * interval["bartlett_se"] ** 2 / 0.01, 3.5, delta=0.15)
        self.assertEqual(interval["se"], interval["bartlett_se"])
        self.assertLess(20000 * interval["se"] ** 2 / 0.01, 3.8)                  # below the true 4, at any b
        self.assertEqual(interval["nominal"], 0.95)
        self.assertNotIn("coverage", interval)
        self.assertNotIn("assumes", interval)
        self.assertIn("0.95 is a nominal level, not a coverage guarantee, for few units or for many", interval["limits"])

    def test_a_series_estimate_is_over_the_whole_roster_and_nothing_else(self):
        forks, series = in_order([0.05, 0.02, 0.04, 0.03])
        with self.assertRaisesRegex(ValueError, "1 of 4 states of the roster have no fork: s02"):
            credit.effect(forks[:2] + forks[3:], series=series)
        with self.assertRaisesRegex(ValueError, "which the roster does not list"):
            credit.effect(forks + [dict(forks[0], state="s99")], series=series)
        with self.assertRaisesRegex(ValueError, "which the roster does not list"):
            credit.effect([{k: v for k, v in fork.items() if k != "state"} for fork in forks], series=series)
        with self.assertRaisesRegex(ValueError, "counted under another unit"):
            credit.effect(forks[:3] + [dict(forks[3], task="u00")], series=series)
        with self.assertRaisesRegex(ValueError, "no noise floor is defined"):
            credit.effect(forks, forks, series=series)
        with self.assertRaisesRegex(ValueError, "4 of 4 states of the roster have no fork"):
            credit.effect([], series=series)                  # no forks at all is incomplete, not zero tasks
        with self.assertRaisesRegex(ValueError, "given more than one family"):
            credit.effect(forks + [dict(forks[0], family="g")], series=series)
        for lag in (None, -1, True, 3.0, "3"):
            with self.assertRaisesRegex(ValueError, "a whole number from zero", msg=repr(lag)):
                credit.effect(forks, series=dict(series, lag=lag))
        with self.assertRaisesRegex(ValueError, "over a sample or over a series, not both"):
            credit.effect([dict(fork, weight=1.0) for fork in forks], series=series,
                          population={"method": METHOD, "total": 4.0, "variance_bound": 1.0})
        with self.assertRaisesRegex(ValueError, "computed by a method the terms name"):
            credit.effect(forks, series=dict(series, method="resampled"))
        for roster in ([], [{"unit": "u", "states": []}], [{"unit": "u", "states": ["s"]}, {"unit": "u", "states": ["t"]}],
                       [{"unit": "u", "states": ["s"]}, {"unit": "v", "states": ["s"]}], [{"unit": "u"}],
                       [{"unit": "u", "states": ["s"], "stratum": "x"}], {"u": ["s"]}):
            with self.assertRaisesRegex(ValueError, "a roster lists", msg=repr(roster)):
                credit.roster_id(roster)
        # The order is part of a roster's identity; the order of a unit's states is not.
        roster = [{"unit": "a", "states": ["s1", "s2"]}, {"unit": "b", "states": ["s3"]}]
        self.assertNotEqual(credit.roster_id(roster), credit.roster_id(roster[::-1]))
        self.assertEqual(credit.roster_id(roster), credit.roster_id([{"unit": "a", "states": ["s2", "s1"]}, roster[1]]))
        # A variance a hair under zero is rounding; further under is refused, not read as zero.
        with mock.patch.object(credit, "_bartlett", return_value=-1e-16):
            self.assertEqual(credit.effect(forks, series=series)["interval"]["bartlett_se"], 0.0)
        with mock.patch.object(credit, "_bartlett", return_value=-1e-9):
            with self.assertRaisesRegex(ValueError, "negative beyond rounding"):
                credit.effect(forks, series=series)
        # One unit gives a mean and no interval, so a bound in the terms is not met.
        alone = credit.effect(forks[:1], series=dict(series, roster=series["roster"][:1]))
        self.assertEqual((alone["tasks"], alone["low"], alone["interval"]), (1, None, None))
        self.assertEqual(credit.met({"lower_bound": 0}, alone), ["interval reaches the stated bound or is undefined"])


class NativeJournalTest(unittest.TestCase):
    def test_native_captures_and_tallies_read_into_a_ledger(self):
        def row(seq, kind, body, **identities):
            return {"seq": seq, "hash": f"h{seq}", "event": {"kind": kind, "body": body, "identities": {
                "harness": identities.get("harness", "H"), "model": "m", "effort": "max"}}}
        receipts = {"evaluator_digest": "E", "environment_digest": "V"}
        events = [
            row(1, "capture", {"phase": "task_delivery", "subject_hash": "d0"}),
            row(2, "capture", {"phase": "progress_snapshot", "contract_id": "c", "subject_hash": "s1", "turn_id": "t1"}),
            row(3, "verification", {"contract_id": "c", "subject_hash": "s1", "receipts": receipts,
                                    "sealed": {"passed": 2, "qualified": 4}}),
            row(4, "capture", {"phase": "progress_snapshot", "contract_id": "other", "subject_hash": "x"}),
            row(5, "capture", {"phase": "interrupted_snapshot", "contract_id": "c", "subject_hash": "s2"}, harness="H2"),
            row(6, "verification", {"contract_id": "c", "subject_hash": "s2", "receipts": receipts,
                                    "sealed": {"passed": 3, "qualified": 4}}),
            row(7, "verification", {"contract_id": "c", "subject_hash": "s2", "receipts": receipts,
                                    "sealed": {"passed": 5, "qualified": 5}}),
        ]
        states = credit.native_record(events, "c")
        self.assertEqual([(s["id"], s["subject"], s["by"]["harness"]) for s in states],
                         [(2, "s1", "H"), (5, "s2", "H2")])
        groups = credit.native_outcomes(events, "c")
        self.assertEqual(set(groups), {("E", "V", 4), ("E", "V", 5)})
        book = credit.ledger(states, groups[("E", "V", 4)])
        self.assertEqual((book["change"], book["segments"][0]["cost"], book["segments"][0]["acquired"]),
                         (0.25, {}, None))
        coverage = credit.native_coverage(events, "c")
        self.assertEqual({k: coverage[k] for k in ("captures", "bound", "unbound", "tallies", "tallies_attached",
                                                   "tallies_unattached")}, {
            "captures": 4, "bound": 2, "unbound": 1, "tallies": 3, "tallies_attached": 3, "tallies_unattached": 0})
        self.assertIn("inferred", coverage["association"])
        self.assertTrue(all("inferred" in outcome["association"]
                            for group in groups.values() for outcome in group.values()))

    def test_a_tally_belongs_to_the_occurrence_it_was_taken_on(self):
        def row(seq, kind, body):
            return {"seq": seq, "hash": f"h{seq}", "event": {"kind": kind, "body": body, "identities": {}}}
        capture = lambda seq, subject: row(seq, "capture", {  # noqa: E731
            "phase": "progress_snapshot", "contract_id": "c", "subject_hash": subject})
        tally = lambda seq, subject, passed: row(seq, "verification", {  # noqa: E731
            "contract_id": "c", "subject_hash": subject, "receipts": {}, "sealed": {"passed": passed, "qualified": 4}})
        events = [capture(1, "A"), tally(2, "A", 0), capture(3, "B"), tally(4, "B", 2),
                  capture(5, "A"), tally(6, "A", 4)]
        states, key = credit.native_record(events, "c"), (None, None, 4)
        observed = credit.ledger(states, credit.native_outcomes(events, "c")[key])
        corrected = credit.ledger(states, credit.native_outcomes(events, "c", policy="corrected")[key])
        self.assertEqual(([s["change"] for s in observed["segments"]], observed["change"]), ([0.5, 0.5], 1.0))
        self.assertEqual(([s["change"] for s in corrected["segments"]], corrected["change"]), ([-0.5, 0.5], 0.0))


@unittest.skipUnless(AVAILABLE, "pro-contract-store binary is not built")
class ImprovementTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Path(directory.name)
        self.sealed = {"pool": credit._digest("pool", "sealed cases, their content and source"),
                       "evaluator": credit._digest("evaluator", "scripted"),
                       "environment": credit._digest("environment", "scripted"),
                       "total": 4, "cases": credit.case_set(f"c{i}" for i in range(4))}
        self.versions = {name: credit._digest("version", name) for name in ("v0", "v1", "v2", "v3")}
        self.count = 0
        credit.install(self.store, "principal", "scope", self.versions["v0"], "principal",
                       authorization="the principal chose v0",
                       restore_convention="serve the last standing acceptance")

    def terms(self, defect, **criterion):
        return {"scope": "scope", "defect": defect, "evidence": ["segment s1->s3 lost c0, c2"],
                "criterion": {"measure": self.sealed, **criterion},
                "convention": "adopt when the criterion is met beyond reach"}

    def locate(self, defect, **criterion):
        return credit.locate(self.store, "principal", self.terms(defect, **criterion))["id"]

    def side(self, task, origin, version, passing, cost=None, registration=None):
        self.count += 1
        subject = credit._digest("state", [task, version, self.count])
        seq = credit.state(self.store, kernel.identities(harness=version), task, subject, task=task, family="f",
                           parent=origin, inputs={"version": self.versions[version]},
                           receipt=credit._digest("execution", [task, self.count]), cost=cost or {"requests": 10})
        credit.measure(self.store, registration or self.sealed, subject, outcome(*passing))
        return seq

    def origin(self, task):
        return credit.state(self.store, kernel.identities(), task, credit._digest("state", task), task=task,
                            family="f")

    def forks(self, base, candidate, gains, tasks=4, cost=None):
        """One state per task continued under two versions; returns pairs of journal sequences."""
        pairs = []
        for index in range(tasks):
            self.count += 1
            task = f"task-{self.count}"
            origin = self.origin(task)
            pairs.append((self.side(task, origin, base, (0,)),
                          self.side(task, origin, candidate, tuple(range(1 + gains[index % len(gains)])), cost)))
        return pairs

    def nulls(self, version, spread, tasks=4):
        pairs = []
        for index in range(tasks):
            self.count += 1
            task = f"null-{self.count}"
            origin = self.origin(task)
            pairs.append((self.side(task, origin, version, (0,)),
                          self.side(task, origin, version, tuple(range(1 + spread[index % len(spread)])))))
        return pairs

    def put(self, contract, candidate, base="v0", change="a", **more):
        return credit.propose(self.store, contract, self.versions[candidate], base=self.versions[base],
                              change=change, author="improver", **more)

    def improve(self, defect, base, candidate, gains=(2,), **criterion):
        contract = self.locate(defect, **criterion)
        self.put(contract, candidate, base, f"{base}->{candidate}")
        return contract, credit.judge(self.store, contract, self.forks(base, candidate, gains))

    def test_terms_precede_candidates(self):
        with self.assertRaisesRegex(ValueError, "name no version"):
            credit.locate(self.store, "principal", dict(self.terms("d"), version=self.versions["v1"]))
        contract = self.locate("successors restart from an empty history")
        self.assertEqual([row["contract"] for row in credit.backlog(self.store)], [contract])
        self.assertEqual(self.locate("successors restart from an empty history"), contract)

    def test_beyond_reach_effect_is_adopted_and_serves(self):
        contract, judged = self.improve("d1", "v0", "v1", min_effect=0.1, lower_bound=0)
        self.assertEqual((judged["statement"]["reach"], judged["statement"]["unmet"]), ("beyond", []))
        self.assertEqual(credit.serving(self.store, trial=contract), self.versions["v1"])
        self.assertEqual(credit.serving(self.store), self.versions["v0"])
        credit.adopt(self.store, contract)
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v1"])
        self.assertEqual(credit.backlog(self.store), [])
        kernel._run(["verify", "--store", str(self.store)])

    def test_the_support_is_reproducible_from_the_journal(self):
        contract, judged = self.improve("d1", "v0", "v1")
        coordinate = judged["contract"]["support"]["coordinate"]
        retained = credit._statements(kernel.events(self.store), contract)[-1]
        retained.pop("seq")
        self.assertEqual(credit._digest("credit", retained), coordinate["evidence_hash"])
        self.assertEqual((coordinate["evaluator_digest"], coordinate["environment_digest"]),
                         (self.sealed["evaluator"], self.sealed["environment"]))
        self.assertEqual(credit._terms(kernel.events(self.store), judged["contract"])["defect"], "d1")

    def test_within_reach_effect_needs_the_principal(self):
        credit.expose(self.store, "improver", self.sealed["pool"])
        contract, judged = self.improve("d1", "v0", "v1")
        self.assertEqual(judged["statement"]["reach"], "within")
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):
            credit.adopt(self.store, contract)
        credit.adopt(self.store, contract, explicit="the principal accepted v1")
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v1"])

    def test_verdicts_spend_reach(self):
        contract = self.locate("d1", min_effect=0.5, max_verdicts=1)
        self.put(contract, "v1")
        first = credit.judge(self.store, contract, self.forks("v0", "v1", (0,)))
        self.assertEqual((first["statement"]["reach"], first["contract"]["candidate"]), ("beyond", None))
        self.put(contract, "v2", change="b")
        second = credit.judge(self.store, contract, self.forks("v0", "v2", (3,)))
        self.assertEqual((second["statement"]["reach"], second["statement"]["unmet"]), ("within", []))
        self.assertEqual(second["statement"]["exposure"], {"improver": {"cases": False, "verdicts": 1}})

    def test_unmet_criterion_defeats_and_the_defect_stays_owed(self):
        contract, judged = self.improve("d1", "v0", "v1", gains=(0,), min_effect=0.1)
        self.assertEqual(judged["statement"]["unmet"], ["effect below the stated minimum"])
        self.assertIsNone(judged["contract"]["candidate"])
        self.assertEqual([row["contract"] for row in credit.backlog(self.store)], [contract])
        with self.assertRaisesRegex(ValueError, "no candidate to try"):
            credit.serving(self.store, trial=contract)

    def test_revised_terms_govern_the_next_judgment(self):
        contract = self.locate("d1", min_effect=0.1)
        credit.revise(self.store, contract, self.terms("d1", min_effect=0.9))
        self.put(contract, "v1")
        judged = credit.judge(self.store, contract, self.forks("v0", "v1", (2,)))
        self.assertEqual(judged["statement"]["effect"]["mean"], 0.5)
        self.assertEqual(judged["statement"]["unmet"], ["effect below the stated minimum"])
        self.assertEqual(judged["contract"]["revision"], 2)
        self.assertEqual([row["terms"]["criterion"]["min_effect"] for row in credit.backlog(self.store)], [0.9])

    def test_a_judgment_is_its_complete_input(self):
        contract = self.locate("d1", above_noise=True)
        self.put(contract, "v1")
        pairs, quiet = self.forks("v0", "v1", (2,)), self.nulls("v0", (0,))
        first = credit.judge(self.store, contract, pairs, null=quiet)
        self.assertEqual((first["statement"]["effect"]["floor"], first["statement"]["unmet"]), (0.0, []))
        self.assertEqual(credit.judge(self.store, contract, pairs, null=quiet)["statement"]["request"],
                         first["statement"]["request"])
        with self.assertRaisesRegex(ValueError, "already carries a judgment on other inputs"):
            credit.judge(self.store, contract, pairs, null=self.nulls("v0", (3,)))
        self.assertTrue(set(n for pair in quiet for n in pair) and
                        len(first["statement"]["receipts"]) == 2 * (len(pairs) + len(quiet)))

    def test_a_fork_continues_one_recorded_state_of_one_task(self):
        contract = self.locate("d1")
        self.put(contract, "v1")
        (left, right), = self.forks("v0", "v1", (2,), tasks=1)
        elsewhere = self.side("other", self.origin("other"), "v1", (0, 1))
        with self.assertRaisesRegex(ValueError, "same recorded state"):
            credit.judge(self.store, contract, [(left, elsewhere)])
        with self.assertRaisesRegex(ValueError, "version and in nothing else"):
            credit.judge(self.store, contract, [(right, left)])
        ghosts = (self.side("ghost", 999999, "v0", (0,)), self.side("ghost", 999999, "v1", (0, 1, 2)))
        with self.assertRaisesRegex(ValueError, "same recorded state"):
            credit.judge(self.store, contract, [ghosts])
        origin = self.origin("bare")
        bare = credit.state(self.store, kernel.identities(), "bare", credit._digest("state", "bare-side"),
                            task="bare", family="f", parent=origin, inputs={"version": self.versions["v0"]})
        with self.assertRaisesRegex(ValueError, "receipt of its execution"):
            credit.judge(self.store, contract, [(bare, self.side("bare", origin, "v1", (0, 1)))])

    def test_the_same_inputs_are_the_same_record_not_python_equals(self):
        contract = self.locate("d1")
        self.put(contract, "v1")

        def given(task, origin, inputs, passing):
            self.count += 1
            subject = credit._digest("state", [task, self.count])
            seq = credit.state(self.store, kernel.identities(), task, subject, task=task, family="f", parent=origin,
                               inputs=inputs, receipt=credit._digest("execution", [task, self.count]))
            credit.measure(self.store, self.sealed, subject, outcome(*passing))
            return seq

        v0, v1 = self.versions["v0"], self.versions["v1"]
        origin = self.origin("typed")
        base = given("typed", origin, {"version": v0, "seed": 1}, (0,))
        for other in ({"version": v1, "seed": True}, {"version": v1, "seed": 1.0},
                      {"version": v1, "seed": 1, "policy": None}, {"version": v1}):
            with self.assertRaisesRegex(ValueError, "version and in nothing else"):
                credit.judge(self.store, contract, [(base, given("typed", origin, other, (0, 1, 2)))])
        with self.assertRaisesRegex(ValueError, "each name the version"):
            credit.judge(self.store, contract, [(base, given("typed", origin, {"seed": 1}, (0, 1, 2)))])
        twin = given("typed", origin, {"version": v0, "seed": True}, (0,))
        good = given("typed", origin, {"version": v1, "seed": 1}, (0, 1, 2))
        with self.assertRaisesRegex(ValueError, "twice under the same inputs"):
            credit.judge(self.store, contract, [(base, good)], null=[(base, twin)])
        self.assertTrue(credit.same({"a": [1, {"b": None}]}, {"a": (1, {"b": None})}))
        self.assertFalse(credit.same({"a": 1}, {"a": True}))
        self.assertFalse(credit.same({}, {"a": None}))
        with self.assertRaisesRegex(ValueError, "keys are strings"):
            credit.same({1: "x"}, {"1": "x"})
        with self.assertRaisesRegex(ValueError, "exactly true or false"):
            credit.measure(self.store, self.sealed, credit._digest("state", "strings"),
                           {"cases": {f"c{i}": "fail" for i in range(4)}})

    def test_a_label_an_earlier_writer_left_is_not_a_result(self):
        contract = self.locate("d1")
        self.put(contract, "v1")

        def old_label(subject, results):  # what a writer without the outcome check could retain
            return kernel.append(self.store, "label", kernel.identities(evaluator_epoch=self.sealed["evaluator"]), {
                "subject": subject, "measure": credit.measure_id(self.sealed), "environment": self.sealed["environment"],
                "attempt": None, "outcome": {"cases": results}})["seq"]

        def side(task, origin, version):
            self.count += 1
            subject = credit._digest("state", [task, version, self.count])
            return subject, credit.state(self.store, kernel.identities(harness=version), task, subject, task=task,
                                         family="f", parent=origin, inputs={"version": self.versions[version]},
                                         receipt=credit._digest("execution", [task, self.count]), cost={"requests": 10})

        pairs, bases = [], []
        for index in range(4):
            origin = self.origin(f"old-{index}")
            subject, base = side(f"old-{index}", origin, "v0")
            bad = old_label(subject, {f"c{i}": "fail" for i in range(4)})
            bases.append((subject, base, bad))
            pairs.append((base, self.side(f"old-{index}", origin, "v1", (0, 1, 2, 3))))
        # Unknown is not zero: the strings are no measurement, so nothing is judged from them.
        self.assertIsNone(credit.value({"cases": {f"c{i}": "fail" for i in range(4)}}))
        before = len(kernel.events(self.store))
        with self.assertRaisesRegex(ValueError, f"measurement {bases[0][2]} of state {bases[0][1]} is not a result.*true or false"):
            credit.judge(self.store, contract, pairs)
        self.assertEqual(len(kernel.events(self.store)), before)  # no statement, no Support
        # A proper measurement of the same states is another retained label; the old ones stay.
        for subject, _, _ in bases:
            credit.measure(self.store, self.sealed, subject, outcome(0))
        with self.assertRaisesRegex(ValueError, "is not a result"):  # pinning the old label is still refused
            credit.judge(self.store, contract, pairs, receipts={bases[0][1]: bases[0][2]})
        # A bad label of a state no fork names does not stand in the way of the forks that are sound.
        old_label(credit._digest("state", "unrelated"), {"x": True, "y": "pass"})
        judged = credit.judge(self.store, contract, pairs)
        self.assertEqual(judged["statement"]["unmet"], [])
        self.assertEqual(judged["statement"]["effect"]["mean"], 0.75)
        kept = [row["event"]["body"]["outcome"] for row in kernel.events(self.store) if row["seq"] == bases[0][2]]
        self.assertEqual(kept, [{"cases": {f"c{i}": "fail" for i in range(4)}}])

    def test_a_label_over_other_cases_is_not_a_result_either(self):
        contract = self.locate("d1")
        self.put(contract, "v1")
        origin = self.origin("other-cases")
        base = self.side("other-cases", origin, "v0", (0,))
        self.count += 1
        subject = credit._digest("state", ["other-cases", self.count])
        candidate = credit.state(self.store, kernel.identities(), "other-cases", subject, task="other-cases", family="f",
                                 parent=origin, inputs={"version": self.versions["v1"]},
                                 receipt=credit._digest("execution", self.count), cost={"requests": 10})
        kernel.append(self.store, "label", kernel.identities(evaluator_epoch=self.sealed["evaluator"]), {
            "subject": subject, "measure": credit.measure_id(self.sealed), "environment": self.sealed["environment"],
            "attempt": None, "outcome": {"cases": {f"easy{i}": True for i in range(4)}}})
        with self.assertRaisesRegex(ValueError, "is not a result.*not over the measure's registered cases"):
            credit.judge(self.store, contract, [(base, candidate)])

    def test_a_null_fork_names_its_version_too(self):
        contract = self.locate("d1")
        self.put(contract, "v1")
        origin = self.origin("null-version")

        def given(inputs):
            self.count += 1
            subject = credit._digest("state", ["null-version", self.count])
            seq = credit.state(self.store, kernel.identities(), "null-version", subject, task="null-version", family="f",
                               parent=origin, inputs=inputs, receipt=credit._digest("execution", self.count))
            credit.measure(self.store, self.sealed, subject, outcome(0))
            return seq

        pair = (self.side("null-version", origin, "v0", (0,)), self.side("null-version", origin, "v1", (0, 1, 2)))
        with self.assertRaisesRegex(ValueError, "each name the version"):
            credit.judge(self.store, contract, [pair], null=[(given({"seed": 1}), given({"seed": 1}))])

    def test_a_judgment_under_a_graded_measure_by_unit(self):
        """Forecasts: three questions on each of two dates. Each question is scored on its own
        resolution, so the measure has a unit for each question; the dates are what a statistic
        counts. Which cases apply and which unit is counted are separate facts of a state."""
        dates = {"2025-07-01": ["q1", "q2", "q3"], "2025-07-02": ["q4", "q5", "q6"]}
        questions = [q for names in dates.values() for q in names]
        graded = {"pool": credit._digest("pool", "unseen dates"), "evaluator": credit._digest("evaluator", "scorer"),
                  "environment": credit._digest("environment", "scripted"), "scale": "score",
                  "units": {q: {"cases": credit.case_set([q + "-resolution"]), "total": 1} for q in questions}}
        contract = self.locate("d1", measure=graded, min_tasks=2, min_effect=0.25, lower_bound=0)
        self.put(contract, "v1")

        def side(date, question, origin, version, score, measured_as="same", outcome_of=None):
            self.count += 1
            subject = credit._digest("state", [question, version, self.count])
            seq = credit.state(self.store, kernel.identities(harness=version), date, subject, task=date, family="f",
                               parent=origin, inputs={"version": self.versions[version]},
                               receipt=credit._digest("execution", [question, self.count]), cost={"requests": 10},
                               measurement_unit=question if measured_as == "same" else measured_as)
            unit = outcome_of or question
            credit.measure(self.store, graded, subject, {"unit": unit, "cases": {unit + "-resolution": score}})
            return seq

        pairs = []
        for date, names in dates.items():
            for question in names:
                origin = self.origin(date)
                pairs.append((side(date, question, origin, "v0", 0.25), side(date, question, origin, "v1", 0.75)))
        origin = self.origin("2025-07-01")
        for wrong, message in (
                ((side("2025-07-01", "q1", origin, "v0", 0.25), side("2025-07-01", "q1", origin, "v1", 0.75, outcome_of="q2")),
                 "has no outcome of the unit it is measured as"),                  # its only outcome is of another question
                ((side("2025-07-01", "q1", origin, "v0", 0.25), side("2025-07-01", "q1", origin, "v1", 0.75, measured_as=None)),
                 "two sides are measured as the same unit"),
                ((side("2025-07-01", "q1", origin, "v0", 0.25), side("2025-07-01", "q1", origin, "v1", 0.75, measured_as="q2")),
                 "two sides are measured as the same unit"),
                ((side("2025-07-01", "q1", origin, "v0", 0.25, measured_as=None), side("2025-07-01", "q1", origin, "v1", 0.75, measured_as=None)),
                 "names the unit it is measured as")):
            with self.assertRaisesRegex(ValueError, message):
                credit.judge(self.store, contract, pairs + [wrong])
        with self.assertRaisesRegex(ValueError, "a score from 0 to 1"):
            credit.measure(self.store, graded, credit._digest("state", "bool"), {"unit": "q1", "cases": {"q1-resolution": True}})
        # One frozen answer given to two questions is one subject measured as two units: each state
        # reads the latest label of its own unit, whichever was written last.
        shared = credit._digest("state", "the same forecast text for q1 and q4")
        both = []
        for date, question in (("2025-07-01", "q1"), ("2025-07-02", "q4")):
            origin = self.origin(date)
            base = side(date, question, origin, "v0", 0.25)
            self.count += 1
            candidate = credit.state(self.store, kernel.identities(harness="v1"), date, shared, task=date, family="f",
                                     parent=origin, inputs={"version": self.versions["v1"]},
                                     receipt=credit._digest("execution", [question, self.count]), cost={"requests": 10},
                                     measurement_unit=question)
            both.append((base, candidate, question))
        first = credit.measure(self.store, graded, shared, {"unit": "q1", "cases": {"q1-resolution": 0.75}})
        second = credit.measure(self.store, graded, shared, {"unit": "q4", "cases": {"q4-resolution": 0.75}})
        twice = [(base, candidate) for base, candidate, _ in both]
        with self.assertRaisesRegex(ValueError, "of unit q4, not of the state's unit q1"):   # a pin of the other unit's label
            credit.judge(self.store, contract, pairs + twice, receipts={both[0][1]: second})
        unnamed = kernel.append(self.store, "label", kernel.identities(evaluator_epoch=graded["evaluator"]), {
            "subject": shared, "measure": credit.measure_id(graded), "environment": graded["environment"], "attempt": None,
            "outcome": {"cases": {"q1-resolution": 1.0}}})["seq"]                              # a newer label that names no unit
        with self.assertRaisesRegex(ValueError, f"measurement {unnamed} of state .* names no unit of the measure"):
            credit.judge(self.store, contract, pairs + twice)
        statement = credit.judge(self.store, contract, pairs + twice, receipts={both[0][1]: first, both[1][1]: second})["statement"]
        self.assertEqual(sorted(seq for _, seq in statement["measurements"] if seq in (first, second)), [first, second])
        # Eight questions measured, two dates counted.
        self.assertEqual((statement["effect"]["tasks"], statement["effect"]["mean"], statement["unmet"]), (2, 0.5, []))
        self.assertEqual(kernel.contract(self.store, contract)["support"]["coordinate"]["reach"], "beyond")

    def test_the_latest_label_of_a_state_is_the_latest_of_its_own_unit(self):
        questions = ["q1", "q2"]
        graded = {"pool": credit._digest("pool", "unseen dates"), "evaluator": credit._digest("evaluator", "scorer"),
                  "environment": credit._digest("environment", "scripted"), "scale": "score",
                  "units": {q: {"cases": credit.case_set([q + "-resolution"]), "total": 1} for q in questions}}
        contract = self.locate("d1", measure=graded)
        self.put(contract, "v1")
        shared = credit._digest("state", "one forecast text for both questions")
        pairs = []
        for question in questions:
            origin = self.origin("date-" + question)
            made = []
            for version, subject in (("v0", credit._digest("state", [question, "v0"])), ("v1", shared)):
                self.count += 1
                made.append(credit.state(self.store, kernel.identities(harness=version), "date-" + question, subject,
                                         task="date-" + question, family="f", parent=origin,
                                         inputs={"version": self.versions[version]},
                                         receipt=credit._digest("execution", [question, self.count]), cost={"requests": 10},
                                         measurement_unit=question))
            credit.measure(self.store, graded, credit._digest("state", [question, "v0"]),
                           {"unit": question, "cases": {question + "-resolution": 0.25}})
            pairs.append(tuple(made))
        credit.measure(self.store, graded, shared, {"unit": "q1", "cases": {"q1-resolution": 0.75}})
        credit.measure(self.store, graded, shared, {"unit": "q2", "cases": {"q2-resolution": 1.0}})   # written last
        statement = credit.judge(self.store, contract, pairs)["statement"]     # no pin: each state reads its own unit's label
        self.assertEqual((statement["effect"]["tasks"], statement["effect"]["mean"]), (2, 0.625))

    def test_only_the_registered_evaluator_measures(self):
        contract = self.locate("d1")
        self.put(contract, "v1")
        other = dict(self.sealed, evaluator=credit._digest("evaluator", "another"))
        origin = self.origin("t")
        pair = (self.side("t", origin, "v0", (0,), registration=other),
                self.side("t", origin, "v1", (0, 1, 2), registration=other))
        with self.assertRaisesRegex(ValueError, "no outcome under the criterion's measure"):
            credit.judge(self.store, contract, [pair])
        with self.assertRaisesRegex(ValueError, "not over the measure's registered cases"):
            credit.measure(self.store, self.sealed, credit._digest("state", "x"), outcome(0, total=3))

    def test_cost_is_compared_in_the_unit_the_terms_name(self):
        contract = self.locate("d1", cost_unit="usd", max_cost_ratio=1.1)
        self.put(contract, "v1")
        pairs = []
        for index in range(3):
            origin = self.origin(f"c{index}")
            pairs.append((self.side(f"c{index}", origin, "v0", (0,), {"tokens": 100000, "usd": 1.0}),
                          self.side(f"c{index}", origin, "v1", (0, 1, 2), {"tokens": 1, "usd": 2.0})))
        judged = credit.judge(self.store, contract, pairs)
        self.assertEqual(judged["statement"]["effect"]["cost_ratio"], 2.0)
        self.assertEqual(judged["statement"]["unmet"], ["cost above the stated ratio"])

    def test_reach_covers_the_author_and_whoever_selected(self):
        credit.expose(self.store, "operator", self.sealed["pool"])
        contract = self.locate("d1")
        self.put(contract, "v1", selectors=("operator",))
        judged = credit.judge(self.store, contract, self.forks("v0", "v1", (2,)))
        self.assertEqual(judged["statement"]["reach"], "within")
        self.assertEqual(set(judged["statement"]["exposure"]), {"improver", "operator"})
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):
            credit.adopt(self.store, contract)

    def test_authority_is_given_never_implied(self):
        bare = dict(self.terms("d1"))
        bare.pop("convention")
        contract = credit.locate(self.store, "principal", bare)["id"]
        self.put(contract, "v1")
        credit.judge(self.store, contract, self.forks("v0", "v1", (2,)))
        with self.assertRaisesRegex(ValueError, "authorize no presumed adoption"):
            credit.adopt(self.store, contract)
        adopted = credit.adopt(self.store, contract, explicit="receipt 1")
        self.assertEqual(adopted["settlement"]["decision"], {"type": "explicit"})
        self.assertEqual(credit.adopt(self.store, contract, explicit="receipt 1"), adopted)
        with self.assertRaisesRegex(ValueError, "another authorization"):
            credit.adopt(self.store, contract, explicit="receipt 2")
        with self.assertRaises(TypeError):
            credit.install(self.store, "principal", "other scope", self.versions["v2"], "principal")

    def test_restoring_needs_the_principal_or_a_convention_named_in_advance(self):
        store = Path(tempfile.mkdtemp(dir=self.store))
        credit.install(store, "principal", "scope", self.versions["v0"], "principal", authorization="chosen")
        self.store = store
        contract, _ = self.improve("d1", "v0", "v1")
        credit.adopt(self.store, contract)
        credit.withdraw(self.store, contract, "evidence failed")
        with self.assertRaisesRegex(ValueError, "no restore convention"):
            credit.restore(self.store)
        self.assertEqual(credit.restore(self.store, explicit="the principal reselected v0")["version"],
                         self.versions["v0"])

    def test_an_interrupted_judgment_is_finished_not_redone(self):
        contract = self.locate("d1", max_verdicts=1)
        self.put(contract, "v1")
        pairs = self.forks("v0", "v1", (2,))
        with mock.patch.object(credit, "_conclude", side_effect=RuntimeError("lost")):
            with self.assertRaises(RuntimeError):
                credit.judge(self.store, contract, pairs)
        self.assertIsNone(kernel.contract(self.store, contract)["support"])
        judged = credit.judge(self.store, contract, pairs)
        self.assertEqual((judged["statement"]["reach"], judged["contract"]["support"]["coordinate"]["reach"]),
                         ("beyond", "beyond"))
        verdicts = [body for _, body in credit._of(self.store, "exposure") if body["grain"] == "verdict"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(len(credit._statements(kernel.events(self.store), contract)), 1)

    def test_an_empty_receipt_is_not_authority(self):
        bare = dict(self.terms("d1"))
        bare.pop("convention")
        contract = credit.locate(self.store, "principal", bare)["id"]
        self.put(contract, "v1")
        credit.judge(self.store, contract, self.forks("v0", "v1", (2,)))
        for empty in ("", "  ", 0, False):
            with self.assertRaisesRegex(ValueError, "non-empty string"):
                credit.adopt(self.store, contract, explicit=empty)
            with self.assertRaisesRegex(ValueError, "non-empty string"):
                credit.restore(self.store, explicit=empty)
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            credit.install(self.store, "principal", "other", self.versions["v2"], "principal", authorization="")
        with self.assertRaisesRegex(ValueError, "non-empty string or absent"):
            credit.locate(self.store, "principal", dict(self.terms("d2"), convention=""))
        self.assertEqual(kernel.contract(self.store, contract)["standing"], "outstanding")

    def test_two_threads_do_not_both_draw_the_last_verdict(self):
        contracts = [self.locate(f"d{index}", max_verdicts=1) for index in range(2)]
        pairs = []
        for index, contract in enumerate(contracts):
            self.put(contract, f"v{index + 1}", change=str(index))
            pairs.append(self.forks("v0", f"v{index + 1}", (2,)))
        slow, results = credit.effect, {}

        def slowly(*args, **kwargs):
            time.sleep(0.4)
            return slow(*args, **kwargs)

        def run(index):
            results[index] = credit.judge(self.store, contracts[index], pairs[index])["statement"]["reach"]

        with mock.patch.object(credit, "effect", side_effect=slowly):
            threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(sorted(results.values()), ["beyond", "within"])

    def test_a_pool_gives_as_many_verdicts_as_the_terms_allow_whoever_asks(self):
        """Counting verdicts by view misses a second candidate from an author under another name.
        Counting them on the pool does not."""
        first = self.locate("d1", max_pool_verdicts=1)
        self.put(first, "v1")
        pairs = self.forks("v0", "v1", (2,))
        with mock.patch.object(credit, "_conclude", side_effect=RuntimeError("lost")):
            with self.assertRaises(RuntimeError):
                credit.judge(self.store, first, pairs)
        # The statement is retained though no view was charged yet: the pool has given its verdict.
        self.assertEqual(credit.verdicts(kernel.events(self.store), self.sealed["pool"]), 1)
        judged = credit.judge(self.store, first, pairs)["statement"]
        self.assertEqual((judged["reach"], judged["pool_verdicts"], judged["max_pool_verdicts"]), ("beyond", 0, 1))
        self.assertEqual(credit.verdicts(kernel.events(self.store), self.sealed["pool"]), 1)
        by_view = self.locate("d2", max_verdicts=1)
        credit.propose(self.store, by_view, self.versions["v2"], base=self.versions["v0"], change="b", author="renamed")
        statement = credit.judge(self.store, by_view, self.forks("v0", "v2", (2,)))["statement"]
        self.assertEqual(statement["reach"], "beyond")
        self.assertNotIn("pool_verdicts", statement)          # terms without the count keep their statement's shape
        by_pool = self.locate("d3", max_verdicts=1, max_pool_verdicts=1)
        credit.propose(self.store, by_pool, self.versions["v3"], base=self.versions["v0"], change="c", author="renamed again")
        statement = credit.judge(self.store, by_pool, self.forks("v0", "v3", (2,)))["statement"]
        self.assertEqual((statement["reach"], statement["pool_verdicts"]), ("within", 2))
        self.assertEqual(kernel.contract(self.store, by_pool)["support"]["coordinate"]["reach"], "within")
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):          # only the principal's own act adopts it
            credit.adopt(self.store, by_pool)
        # Another pool is another count, and a pool that includes this one counts this one's verdicts.
        other = dict(self.sealed, pool=credit._digest("pool", "other cases"))
        self.assertEqual(credit.verdicts(kernel.events(self.store), other["pool"]), 0)
        self.assertEqual(credit.reach(kernel.events(self.store), ["anyone"], credit._pools(dict(other, includes=[self.sealed["pool"]])),
                                      max_pool_verdicts=3), "within")
        for wrong in (0, -1, True, "1", 1.0, None):
            with self.assertRaisesRegex(ValueError, "max_pool_verdicts is a whole number"):
                self.locate("d4", max_pool_verdicts=wrong)

    def test_disclosing_another_selector_extends_the_views(self):
        credit.expose(self.store, "operator", self.sealed["pool"])
        contract = self.locate("d1")
        self.put(contract, "v1")
        self.put(contract, "v1", selectors=("operator",))
        with self.assertRaisesRegex(ValueError, "another registration"):
            self.put(contract, "v1", change="something else", selectors=("operator",))
        judged = credit.judge(self.store, contract, self.forks("v0", "v1", (2,)))
        self.assertEqual((judged["statement"]["reach"], set(judged["statement"]["exposure"])),
                         ("within", {"improver", "operator"}))
        with self.assertRaisesRegex(ValueError, "already judged"):
            self.put(contract, "v1", selectors=("operator", "reviewer"))

    def test_a_pending_judgment_is_not_finished_under_wider_views(self):
        credit.expose(self.store, "operator", self.sealed["pool"])
        contract = self.locate("d1")
        self.put(contract, "v1")
        pairs = self.forks("v0", "v1", (2,))
        with mock.patch.object(credit, "_conclude", side_effect=RuntimeError("lost")):
            with self.assertRaises(RuntimeError):
                credit.judge(self.store, contract, pairs)  # retained as beyond reach, not concluded
        self.put(contract, "v1", selectors=("operator",))
        judged = credit.judge(self.store, contract, pairs)
        self.assertEqual((judged["statement"]["reach"], judged["contract"]["support"]["coordinate"]["reach"]),
                         ("within", "within"))
        self.assertEqual([s["reach"] for s in credit._statements(kernel.events(self.store), contract)],
                         ["beyond", "within"])
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):
            credit.adopt(self.store, contract)

    def test_a_retained_judgment_has_given_its_verdict_before_it_concludes(self):
        """One author, two candidates, one sealed pool, one verdict allowed. The first judgment is
        retained and interrupted before it charges its views. The second must not find the pool
        unspent: the first verdict is in the journal for whoever shaped that candidate."""
        first, second, third = (self.locate(f"d{index}", max_verdicts=1) for index in range(3))
        self.put(first, "v1", change="a")
        self.put(second, "v2", change="b")
        credit.propose(self.store, third, self.versions["v3"], base=self.versions["v0"], change="c", author="someone else")
        pairs = [self.forks("v0", f"v{index + 1}", (2,)) for index in range(3)]
        with mock.patch.object(credit, "_conclude", side_effect=RuntimeError("lost")):
            with self.assertRaises(RuntimeError):
                credit.judge(self.store, first, pairs[0])
        events = kernel.events(self.store)
        self.assertEqual(sum(body["grain"] == "verdict" for _, body in credit._of(events, "exposure")), 0)
        self.assertEqual(credit._seen(events, "improver", [self.sealed["pool"]]), {"cases": False, "verdicts": 1})
        self.assertEqual(credit._seen(events, "someone else", [self.sealed["pool"]]), {"cases": False, "verdicts": 0})
        later = credit.judge(self.store, second, pairs[1])["statement"]
        self.assertEqual((later["reach"], later["exposure"]["improver"]["verdicts"]), ("within", 1))
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):
            credit.adopt(self.store, second)
        # The first is finished as the judgment it was, and the verdict it now charges is not counted twice.
        finished = credit.judge(self.store, first, pairs[0])["statement"]
        self.assertEqual((finished["reach"], finished["exposure"]["improver"]["verdicts"]), ("beyond", 0))
        self.assertEqual(len(credit._statements(kernel.events(self.store), first)), 1)
        self.assertEqual(credit._seen(kernel.events(self.store), "improver", [self.sealed["pool"]])["verdicts"], 2)
        # Another author shaped neither candidate and has drawn nothing.
        other = credit.judge(self.store, third, pairs[2])["statement"]
        self.assertEqual((other["reach"], other["exposure"]["someone else"]["verdicts"]), ("beyond", 0))
        self.assertEqual(credit.adopt(self.store, first)["standing"], "discharged")

    def left_by_a_writer_that_counted_only_charged_verdicts(self, concluded):
        """One author, two candidates, one pool, one verdict allowed. The first judgment is
        retained and interrupted. A writer that counted only charged verdicts then judges the
        second as beyond reach, and either is interrupted too or records its Support."""
        def charged_only(events, view, pools):
            grains = [body.get("grain") for _, body in credit._of(events, "exposure")
                      if body.get("view") == view and body.get("pool") in pools]
            return {"cases": "cases" in grains, "verdicts": grains.count("verdict")}

        first, second = self.locate("d1", max_verdicts=1), self.locate("d2", max_verdicts=1)
        self.put(first, "v1", change="a")
        self.put(second, "v2", change="b")
        pairs = [self.forks("v0", "v1", (2,)), self.forks("v0", "v2", (2,))]
        with mock.patch.object(credit, "_seen", charged_only):
            with mock.patch.object(credit, "_conclude", side_effect=RuntimeError("lost")):
                with self.assertRaises(RuntimeError):
                    credit.judge(self.store, first, pairs[0])
                if not concluded:
                    with self.assertRaises(RuntimeError):
                        credit.judge(self.store, second, pairs[1])
            if concluded:
                credit.judge(self.store, second, pairs[1])
        left = credit._statements(kernel.events(self.store), second)[-1]
        self.assertEqual((left["reach"], left["exposure"]["improver"]["verdicts"]), ("beyond", 0))
        self.assertEqual(kernel.contract(self.store, second)["support"] is not None, concluded)
        return first, second, pairs, left

    def the_first_is_still_finished_as_it_was(self, first, pairs):
        finished = credit.judge(self.store, first, pairs)["statement"]
        self.assertEqual((finished["reach"], len(credit._statements(kernel.events(self.store), first))), ("beyond", 1))
        self.assertEqual(credit.adopt(self.store, first)["standing"], "discharged")

    def test_a_retained_beyond_that_the_record_before_it_contradicts_is_not_concluded(self):
        first, second, pairs, left = self.left_by_a_writer_that_counted_only_charged_verdicts(concluded=False)
        judged = credit.judge(self.store, second, pairs[1])["statement"]
        self.assertNotEqual(judged["request"], left["request"])
        self.assertEqual((judged["reach"], judged["replaces"]), ("within", left["request"]))
        # Both retained statements are verdicts the author can read: the first candidate's and the replaced one.
        self.assertEqual(judged["exposure"]["improver"]["verdicts"], 2)
        statements = credit._statements(kernel.events(self.store), second)
        self.assertEqual([s["reach"] for s in statements], ["beyond", "within"])          # the earlier one stays
        self.assertEqual(kernel.contract(self.store, second)["support"]["coordinate"]["reach"], "within")
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):
            credit.adopt(self.store, second)
        events = len(kernel.events(self.store))
        again = credit.judge(self.store, second, pairs[1])["statement"]                   # the same call is the same judgment
        self.assertEqual((again["request"], len(kernel.events(self.store))), (judged["request"], events))
        self.the_first_is_still_finished_as_it_was(first, pairs[0])
        # Two judgments in the end: the first candidate's, and the second's, which was made twice.
        events = kernel.events(self.store)
        self.assertEqual(credit._seen(events, "improver", [self.sealed["pool"]])["verdicts"], 2)
        self.assertEqual(credit.verdicts(events, [self.sealed["pool"]]), 2)

    def test_a_replaced_judgment_does_not_charge_a_view_twice_after_partial_conclusion(self):
        first, second, pairs, left = self.left_by_a_writer_that_counted_only_charged_verdicts(concluded=False)
        # _conclude writes exposure before Support. Retain that real intermediate state.
        credit.expose(self.store, "improver", self.sealed["pool"], "verdict", left["request"])
        self.assertIsNone(kernel.contract(self.store, second)["support"])
        judged = credit.judge(self.store, second, pairs[1])["statement"]
        self.assertEqual((judged["reach"], judged["replaces"]), ("within", left["request"]))
        self.the_first_is_still_finished_as_it_was(first, pairs[0])
        events = kernel.events(self.store)
        self.assertEqual(credit._seen(events, "improver", [self.sealed["pool"]])["verdicts"], 2)
        self.assertEqual(credit.verdicts(events, [self.sealed["pool"]]), 2)
        before = len(events)
        self.assertEqual(credit.judge(self.store, second, pairs[1])["statement"]["request"], judged["request"])
        self.assertEqual(len(kernel.events(self.store)), before)
        # A third verdict under an allowance of three has not exhausted that allowance yet.
        _, third = self.improve("d3", "v1", "v3", max_verdicts=3)
        self.assertEqual(third["statement"]["exposure"]["improver"]["verdicts"], 2)
        self.assertEqual(third["statement"]["reach"], "beyond")

    def test_a_support_that_the_record_before_it_contradicts_is_not_presumed(self):
        first, second, pairs, left = self.left_by_a_writer_that_counted_only_charged_verdicts(concluded=True)
        events = len(kernel.events(self.store))
        with self.assertRaisesRegex(ValueError, "the record before this judgment already put these cases within reach"):
            credit.adopt(self.store, second)
        with self.assertRaisesRegex(ValueError, "although the record before it puts them within: withdraw it"):
            credit.judge(self.store, second, pairs[1])
        self.assertEqual(len(kernel.events(self.store)), events)                           # nothing concluded, nothing adopted
        self.assertEqual(kernel.contract(self.store, second)["support"]["coordinate"]["reach"], "beyond")   # kept as it was
        credit.withdraw(self.store, second, "its writer did not count a verdict retained before it")
        judged = credit.judge(self.store, second, pairs[1])["statement"]
        # After the withdrawal the candidate is judged anew, as another generation: nothing is replaced.
        self.assertEqual((judged["reach"], "replaces" in judged, judged["exposure"]["improver"]["verdicts"]), ("within", False, 2))
        self.assertEqual([s["reach"] for s in credit._statements(kernel.events(self.store), second)], ["beyond", "within"])
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):
            credit.adopt(self.store, second)
        self.the_first_is_still_finished_as_it_was(first, pairs[0])

    def test_a_judgment_made_again_under_wider_views_has_the_first_ones_verdict(self):
        """A consequence stated, not hidden: when a retained judgment is not finished because the
        candidate's views grew, the judgment made in its place is a second verdict for the
        author, and under a limit of one it lies within reach."""
        contract = self.locate("d1", max_verdicts=1)
        self.put(contract, "v1")
        pairs = self.forks("v0", "v1", (2,))
        with mock.patch.object(credit, "_conclude", side_effect=RuntimeError("lost")):
            with self.assertRaises(RuntimeError):
                credit.judge(self.store, contract, pairs)
        self.put(contract, "v1", selectors=("operator",))
        judged = credit.judge(self.store, contract, pairs)["statement"]
        self.assertEqual(judged["exposure"], {"improver": {"cases": False, "verdicts": 1},
                                              "operator": {"cases": False, "verdicts": 0}})
        self.assertEqual(judged["reach"], "within")

    def test_read_cases_stay_read_under_another_registration(self):
        credit.expose(self.store, "improver", self.sealed["pool"])
        moved = dict(self.sealed, environment=credit._digest("environment", "another machine"))
        self.assertNotEqual(credit.measure_id(moved), credit.measure_id(self.sealed))
        contract = credit.locate(self.store, "principal", dict(self.terms("d1"), criterion={"measure": moved}))["id"]
        self.put(contract, "v1")
        origin = self.origin("t")
        pair = (self.side("t", origin, "v0", (0,), registration=moved),
                self.side("t", origin, "v1", (0, 1, 2), registration=moved))
        self.assertEqual(credit.judge(self.store, contract, [pair])["statement"]["reach"], "within")
        larger = dict(self.sealed, pool=credit._digest("pool", "a superset"), includes=[self.sealed["pool"]])
        self.assertEqual(credit.reach(kernel.events(self.store), ["improver"], credit._pools(larger)), "within")

    def test_reading_a_pool_that_includes_these_cases_is_reading_them(self):
        """Terms in the store register a larger pool as including the sealed one, and the author
        has read the larger pool's cases. A judgment on the sealed pool alone is within reach,
        although its own registration does not mention the larger pool."""
        whole = dict(self.sealed, pool=credit._digest("pool", "sealed and more"), includes=[self.sealed["pool"]],
                     cases=credit.case_set(f"c{i}" for i in range(8)), total=8)
        wider = dict(whole, pool=credit._digest("pool", "wider still"), includes=[whole["pool"]])
        beside = dict(self.sealed, pool=credit._digest("pool", "beside"))
        both = dict(whole, pool=credit._digest("pool", "sealed and beside"), includes=[self.sealed["pool"], beside["pool"]])
        for index, registration in enumerate((whole, wider, both)):
            credit.locate(self.store, "principal", dict(self.terms(f"registers {index}"), criterion={"measure": registration}))
        events = kernel.events(self.store)
        self.assertEqual(credit._overlapping(events, self.sealed),
                         [self.sealed["pool"], *sorted([whole["pool"], wider["pool"], both["pool"]])])
        self.assertNotIn(beside["pool"], credit._overlapping(events, self.sealed))       # a shared parent shows no shared case
        child = credit._digest("pool", "inside both")                                    # a pool both include does
        credit.locate(self.store, "principal", dict(self.terms("registers a shared part"),
                                                    criterion={"measure": dict(beside, includes=[child])}))
        self.assertIn(beside["pool"], credit._overlapping(kernel.events(self.store), dict(self.sealed, includes=[child])))
        self.assertEqual(credit._overlapping(events, whole)[:2], [whole["pool"], self.sealed["pool"]])
        credit.expose(self.store, "sibling reader", beside["pool"])
        credit.expose(self.store, "improver", wider["pool"])                             # two inclusions up
        beyond = self.locate("d1")
        credit.propose(self.store, beyond, self.versions["v1"], base=self.versions["v0"], change="a", author="sibling reader")
        self.assertEqual(credit.judge(self.store, beyond, self.forks("v0", "v1", (2,)))["statement"]["reach"], "beyond")
        within = self.locate("d2")
        self.put(within, "v2", change="b")
        statement = credit.judge(self.store, within, self.forks("v0", "v2", (2,)))["statement"]
        self.assertEqual((statement["reach"], statement["exposure"]["improver"]["cases"]), ("within", True))
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):
            credit.adopt(self.store, within)
        # A verdict drawn from a larger pool is a verdict on the cases it includes.
        self.assertEqual(credit.verdicts(kernel.events(self.store), credit._overlapping(kernel.events(self.store), whole)), 2)
        for wrong in ("pool", [1], [""], {"pool": 1}):
            with self.assertRaisesRegex(ValueError, "includes is a list of pools", msg=repr(wrong)):
                credit.measure_id(dict(self.sealed, includes=wrong))

    def larger(self, name="sealed and more"):
        return dict(self.sealed, pool=credit._digest("pool", name), includes=[self.sealed["pool"]],
                    cases=credit.case_set(f"c{i}" for i in range(8)), total=8)

    def register(self, registration, defect="registers a larger pool"):
        credit.locate(self.store, "principal", dict(self.terms(defect), criterion={"measure": registration}))

    def test_a_judgment_retained_before_a_pool_was_known_is_not_finished_as_beyond_reach(self):
        """The author has read a larger pool. A judgment on the sealed pool is retained as beyond
        reach and interrupted before its Support: once because the store did not yet know the
        larger pool includes the sealed one, once because its writer did not derive that. Asked
        again, it is another judgment, made over the pools known now."""
        known = [self.sealed["pool"]]
        for index, writer in enumerate(("the inclusion is registered later", "the writer did not derive it")):
            whole = self.larger(f"larger {index}")
            known.append(whole["pool"])
            credit.expose(self.store, f"author {index}", whole["pool"])
            if index == 1:
                self.register(whole, f"registers {index}")
            contract = self.locate(f"d{index}")
            credit.propose(self.store, contract, self.versions[f"v{index + 1}"], base=self.versions["v0"],
                           change=str(index), author=f"author {index}")
            pairs = self.forks("v0", f"v{index + 1}", (2,))
            underived = mock.patch.object(credit, "_overlapping", lambda events, registration: credit._pools(registration))
            with mock.patch.object(credit, "_conclude", side_effect=RuntimeError("lost")), (
                    underived if index == 1 else contextlib.nullcontext()):
                with self.assertRaises(RuntimeError):
                    credit.judge(self.store, contract, pairs)
            left = credit._statements(kernel.events(self.store), contract)[-1]
            self.assertEqual((left["reach"], "pools" in left), ("beyond", False), writer)
            if index == 0:
                self.register(whole, f"registers {index}")
            judged = credit.judge(self.store, contract, pairs)["statement"]
            self.assertNotEqual(judged["request"], left["request"], writer)
            self.assertEqual((judged["reach"], judged["exposure"][f"author {index}"]["cases"]), ("within", True), writer)
            self.assertEqual(judged["pools"], sorted(known), writer)
            self.assertEqual(len(credit._statements(kernel.events(self.store), contract)), 2, writer)   # the first stays, unconcluded
            self.assertEqual(kernel.contract(self.store, contract)["support"]["coordinate"]["reach"], "within", writer)
            with self.assertRaisesRegex(RuntimeError, "WithinReach"):
                credit.adopt(self.store, contract)
            again = credit.judge(self.store, contract, pairs)["statement"]
            self.assertEqual(again["request"], judged["request"], writer)
            self.assertEqual(len(credit._statements(kernel.events(self.store), contract)), 2, writer)

    def test_a_support_is_not_presumed_once_another_pool_is_known_to_share_its_cases(self):
        """The same, one step later: the Support was already recorded as beyond reach when the
        store learns that a pool the author had read includes the sealed one."""
        whole = self.larger()
        credit.expose(self.store, "improver", whole["pool"])
        contract = self.locate("d1")
        self.put(contract, "v1")
        pairs = self.forks("v0", "v1", (2,))
        first = credit.judge(self.store, contract, pairs)["statement"]
        self.assertEqual((first["reach"], "pools" in first), ("beyond", False))
        self.register(whole)
        with self.assertRaisesRegex(ValueError, "pools now known to share these cases were not read"):
            credit.adopt(self.store, contract)
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v0"])
        with self.assertRaisesRegex(ValueError, "already carries a judgment on other inputs"):
            credit.judge(self.store, contract, pairs)
        credit.withdraw(self.store, contract, "judged before a pool its author had read was known to include these cases")
        again = credit.judge(self.store, contract, pairs)["statement"]
        self.assertEqual((again["reach"], again["pools"]), ("within", sorted([self.sealed["pool"], whole["pool"]])))
        with self.assertRaisesRegex(RuntimeError, "WithinReach"):
            credit.adopt(self.store, contract)
        self.assertEqual(credit.adopt(self.store, contract, explicit="the principal accepts")["standing"], "discharged")
        # A larger pool that nobody shaping the candidate has read: beyond reach, read and recorded, and presumed.
        other = self.locate("d2")
        credit.propose(self.store, other, self.versions["v2"], base=self.versions["v1"], change="b", author="someone else")
        clean = credit.judge(self.store, other, self.forks("v1", "v2", (2,)))["statement"]
        self.assertEqual((clean["reach"], clean["pools"]), ("beyond", sorted([self.sealed["pool"], whole["pool"]])))
        self.assertEqual(credit.adopt(self.store, other)["standing"], "discharged")

    def test_old_forks_do_not_answer_for_a_new_candidate(self):
        contract = self.locate("d1", min_effect=0.3)
        self.put(contract, "v1")
        old = self.forks("v0", "v1", (0,))
        defeated = credit.judge(self.store, contract, old)
        self.assertEqual(credit.judge(self.store, contract, old)["statement"]["request"],
                         defeated["statement"]["request"])
        self.put(contract, "v2", change="b")
        with self.assertRaisesRegex(ValueError, "version and in nothing else"):
            credit.judge(self.store, contract, old)
        self.assertIsNone(kernel.contract(self.store, contract)["support"])

    def test_new_measurements_are_another_judgment(self):
        contract = self.locate("d1", min_effect=0.3)
        self.put(contract, "v1")
        (left, right), = self.forks("v0", "v1", (2,), tasks=1)
        first = credit.judge(self.store, contract, [(left, right)])
        self.assertEqual(first["statement"]["effect"]["mean"], 0.5)
        subject = dict(credit._of(self.store, "capture"))[right]["subject"]
        credit.measure(self.store, self.sealed, subject, outcome())  # the same state measured again
        with self.assertRaisesRegex(ValueError, "already carries a judgment on other inputs"):
            credit.judge(self.store, contract, [(left, right)])
        used = dict(first["statement"]["measurements"])
        replayed = credit.judge(self.store, contract, [(left, right)], receipts=used)
        self.assertEqual(replayed["statement"]["request"], first["statement"]["request"])
        with self.assertRaisesRegex(ValueError, "not a measurement of that state"):
            credit.judge(self.store, contract, [(left, right)], receipts={right: used[left]})
        # The principal's verifier withdraws the judgment; the candidate is judged on the new one.
        credit.withdraw(self.store, contract, "the state was measured again")
        again = credit.judge(self.store, contract, [(left, right)])
        self.assertEqual((again["statement"]["effect"]["mean"], bool(again["statement"]["unmet"])), (-0.25, True))

    def test_an_equal_count_is_not_the_same_cases(self):
        subject = credit._digest("state", "x")
        with self.assertRaisesRegex(ValueError, "not over the measure's registered cases"):
            credit.measure(self.store, self.sealed, subject, {"cases": {f"hard{i}": False for i in range(4)}})
        with self.assertRaisesRegex(ValueError, "not over the measure's registered cases"):
            credit.measure(self.store, self.sealed, subject, {"passed": 4, "total": 4})
        with self.assertRaisesRegex(ValueError, "complete case set"):
            credit.measure_id({"evaluator": "e", "environment": "v", "total": 4})

    def test_one_change_to_the_incumbent(self):
        first, _ = self.improve("d1", "v0", "v1")
        second, _ = self.improve("d2", "v0", "v2")
        credit.adopt(self.store, first)
        with self.assertRaisesRegex(ValueError, "incumbent changed"):
            credit.adopt(self.store, second)
        with self.assertRaisesRegex(ValueError, "one change to the incumbent"):
            self.put(second, "v3", "v0", "x")
        self.put(second, "v3", "v1", "x")
        credit.judge(self.store, second, self.forks("v1", "v3", (2,)))
        credit.adopt(self.store, second)
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v3"])

    def test_withdrawal_voids_the_selection_and_restores_the_backlog(self):
        first, _ = self.improve("d1", "v0", "v1")
        credit.adopt(self.store, first)
        second, _ = self.improve("d2", "v1", "v2")
        credit.adopt(self.store, second)
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v2"])

        withdrawn = credit.withdraw(self.store, first, "the sealed pool was readable by the author")
        self.assertEqual((withdrawn["standing"], withdrawn["candidate"]["subject_hash"]),
                         ("outstanding", self.versions["v1"]))
        # v2 was built on that acceptance: its selection is void before anything reopens it,
        # and no other version is chosen in its place.
        void = credit.incumbent(self.store)
        self.assertIsNone(void["version"])
        self.assertIsNone(credit.serving(self.store))
        self.assertEqual(kernel.contract(self.store, second)["standing"], "discharged")
        with self.assertRaisesRegex(ValueError, "no version serves"):
            credit.propose(self.store, first, self.versions["v3"], base=None, change="x", author="improver")

        restored = credit.restore(self.store)
        self.assertEqual((restored["version"], restored["acceptance"]), (self.versions["v0"], void["last_standing"]))
        self.assertEqual(credit.restore(self.store)["seq"], restored["seq"])
        self.assertEqual(credit.reconcile(self.store), [second])
        self.assertEqual({row["contract"] for row in credit.backlog(self.store)}, {first, second})
        self.assertEqual(credit.reconcile(self.store), [])
        kernel._run(["verify", "--store", str(self.store)])

    def test_the_store_refuses_an_adoption_whose_support_was_withdrawn_meanwhile(self):
        first, _ = self.improve("d1", "v0", "v1")
        credit.adopt(self.store, first)
        second, _ = self.improve("d2", "v1", "v2")
        seen = kernel.incumbent(self.store)
        credit.withdraw(self.store, first, "evidence failed")
        # The reading taken before the withdrawal still says v1 serves; the store decides at commit.
        with mock.patch.object(kernel, "incumbent", return_value=seen):
            with self.assertRaisesRegex(RuntimeError, "SupportNotLive"):
                credit.adopt(self.store, second)
        self.assertEqual(kernel.contract(self.store, second)["standing"], "outstanding")
        self.assertIsNone(credit.incumbent(self.store)["version"])
        self.assertFalse(kernel.live(self.store, seen["acceptance"]["selection"]))

    def test_entries_appended_to_the_journal_select_nothing_and_withdraw_nothing(self):
        contract, _ = self.improve("d1", "v0", "v1")  # judged and supported, not adopted
        root = credit.incumbent(self.store)
        # Another writer appends a selection shaped like a restore of the live root that names
        # the unadopted v1, and one shaped like a fresh choice of the root.
        for body in ({"role": "incumbent", "version": self.versions["v1"], "adoption": contract,
                      "restores": root["acceptance"]},
                     {"role": "incumbent", "version": self.versions["v0"], "adoption": root["adoption"]}):
            kernel.append(self.store, "selection", kernel.identities(), body)
        self.assertEqual(credit.incumbent(self.store), root)
        self.assertEqual(credit.serving(self.store), self.versions["v0"])
        self.assertEqual(credit.reconcile(self.store), [])
        self.assertEqual(kernel.contract(self.store, root["adoption"])["standing"], "discharged")
        credit.adopt(self.store, contract)
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v1"])

    def test_the_reading_that_was_checked_is_the_fence_that_is_sent(self):
        first, _ = self.improve("d1", "v0", "v1")
        root = credit.incumbent(self.store)["acceptance"]
        credit.adopt(self.store, first)
        second, _ = self.improve("d2", "v1", "v2")
        real, fired = kernel.incumbent, []

        def then_another_writer_selects(store):
            view = real(store)
            if not fired:  # right after this reading, v0 is legitimately selected again
                fired.append(kernel.select(store, "external", root, view["selection"], kernel.identities(),
                                           {"type": "explicit", "receipt": "the principal reselected v0"}))
            return view

        with mock.patch.object(kernel, "incumbent", side_effect=then_another_writer_selects):
            with self.assertRaisesRegex(RuntimeError, "IncumbentChanged"):
                credit.adopt(self.store, second)
        self.assertEqual(kernel.contract(self.store, second)["standing"], "outstanding")
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v0"])

    def test_reconcile_takes_back_only_the_settlement_it_checked(self):
        first, _ = self.improve("d1", "v0", "v1")
        credit.adopt(self.store, first)
        second, _ = self.improve("d2", "v1", "v2")
        credit.adopt(self.store, second)
        credit.withdraw(self.store, first, "evidence failed")
        real = credit.withdraw

        def settled_anew_meanwhile(store, contract_id, cause, **more):
            # Another writer takes the old settlement back and settles the same content again
            # through the plain command path: a settlement the store has no acceptance for.
            contract = real(store, contract_id, "another writer")
            coordinate = dict(more["observed"]["settlement"]["coordinate"], generation=contract["candidate"]["generation"])
            apply = lambda key, role, provenance, command: kernel.apply(store, key, {  # noqa: E731
                "contract_id": contract_id, "expected_version": kernel.contract(store, contract_id)["version"],
                "role": role, "provenance": provenance, "command": command})
            apply("again-support", "verifier", "automation", {
                "type": "support", "coordinate": coordinate, "certificate": credit._digest("certificate", coordinate)})
            apply("again-discharge", "settler", "human", {
                "type": "discharge", "coordinate": coordinate, "attestation": credit._digest("attestation", "again"),
                "decision": {"type": "explicit"}})
            return real(store, contract_id, cause, **more)

        with mock.patch.object(credit, "withdraw", side_effect=settled_anew_meanwhile):
            self.assertEqual(credit.reconcile(self.store), [])
        after = kernel.contract(self.store, second)
        self.assertEqual(after["standing"], "discharged")
        self.assertEqual(credit.reconcile(self.store), [])

    def test_a_pinned_other_measurement_is_not_a_replay_of_the_defeat(self):
        contract = self.locate("d1", min_effect=0.9)
        self.put(contract, "v1")
        (left, right), = self.forks("v0", "v1", (2,), tasks=1)
        defeated = credit.judge(self.store, contract, [(left, right)])
        subject = dict(credit._of(self.store, "capture"))[right]["subject"]
        newer = credit.measure(self.store, self.sealed, subject, outcome(0, 1, 2, 3))
        self.assertEqual(credit.judge(self.store, contract, [(left, right)])["statement"]["request"],
                         defeated["statement"]["request"])
        with self.assertRaisesRegex(ValueError, "no candidate to judge"):
            credit.judge(self.store, contract, [(left, right)], receipts={right: newer})

    def test_accepting_the_same_content_again_revives_nothing_built_on_the_old_acceptance(self):
        first, _ = self.improve("d1", "v0", "v1")
        credit.adopt(self.store, first)
        second, _ = self.improve("d2", "v1", "v2")
        credit.adopt(self.store, second)
        credit.withdraw(self.store, first, "evidence failed")
        credit.restore(self.store)
        # The Contract kept its candidate v1; it is judged again on new forks and accepted again,
        # which is a new acceptance.
        credit.judge(self.store, first, self.forks("v0", "v1", (3,)))
        credit.adopt(self.store, first)
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v1"])
        self.assertEqual(credit.reconcile(self.store), [second])
        self.assertEqual([row["contract"] for row in credit.backlog(self.store)], [second])

    def test_repeated_calls_return_the_recorded_result(self):
        contract = self.locate("d1", max_verdicts=3)
        self.assertEqual(self.put(contract, "v1"), self.put(contract, "v1"))
        pairs = self.forks("v0", "v1", (2,))
        first, again = credit.judge(self.store, contract, pairs), credit.judge(self.store, contract, pairs)
        self.assertEqual(first["statement"]["effect"], again["statement"]["effect"])
        verdicts = [body for _, body in credit._of(self.store, "exposure") if body["grain"] == "verdict"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(credit.adopt(self.store, contract),
                         credit.adopt(self.store, contract))
        self.assertEqual(len(kernel.acceptances(self.store)), 2)

    def test_candidates_are_never_erased(self):
        contract = self.locate("d1", min_effect=0.3)
        self.put(contract, "v1")
        credit.judge(self.store, contract, self.forks("v0", "v1", (0,)))
        self.put(contract, "v2", change="b")
        credit.judge(self.store, contract, self.forks("v0", "v2", (3,)))
        credit.adopt(self.store, contract)
        rows = credit.history(self.store, contract)
        self.assertEqual([row["change"] for row in rows], ["a", "b"])
        self.assertEqual([bool(row["judgments"][0]["unmet"]) for row in rows], [True, False])
        self.assertEqual([row["adopted"] is not None for row in rows], [False, True])


if __name__ == "__main__":
    unittest.main()
