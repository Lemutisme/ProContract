"""Tests of the fork receipts' reader. Digests use the store binary (PRO_CONTRACT_STORE_BIN)."""

import copy
import unittest

import procontract_credit as credit
import procontract_fork as fork

CLOSED = {"completed": True, "shutdown_confirmed": True, "exit": "handoff"}


def state(**changes) -> dict:
    body = {"record": "c.1", "subject": "s0", "execution": "x0", "allocation": "a-x0", "closure": dict(CLOSED),
            "captured": {name: "d-" + name for name in fork.COMPONENTS}, "omitted": {}}
    body.update(changes)
    return body


def run(parent: dict, execution: str, **changes) -> dict:
    body = {"parent": fork.checkpoint(parent), "execution": execution, "allocation": "a-" + execution,
            "resolved_inputs": {"version": "v0", "model": "m", "seed": 1},
            "restored": dict(parent["captured"]), "omitted": {}, "closure": dict(CLOSED),
            "costs": {"usd": 1.0}, "result": "s-" + execution}
    body.update(changes)
    return body


class CheckpointTest(unittest.TestCase):
    def test_every_component_is_captured_or_named_as_omitted(self):
        fork.checkpoint(state())
        partial = state()
        del partial["captured"]["pending"]
        with self.assertRaisesRegex(ValueError, "pending"):
            fork.checkpoint(partial)
        partial["omitted"]["pending"] = "an operation was still queued"
        fork.checkpoint(partial)
        partial["captured"]["pending"] = "d"
        with self.assertRaisesRegex(ValueError, "captured and omitted"):
            fork.checkpoint(partial)
        with self.assertRaisesRegex(ValueError, "non-empty strings"):
            fork.checkpoint(state(captured={n: "d" for n in fork.COMPONENTS if n != "files"}, omitted={"files": ""}))

    def test_a_field_of_another_type_is_refused_not_read_as_something_close(self):
        for change in ({"record": False}, {"subject": {}}, {"execution": 7}, {"allocation": ["a"]},
                       {"captured": True}, {"captured": {n: True for n in fork.COMPONENTS}},
                       {"captured": {n: 1 for n in fork.COMPONENTS}}, {"omitted": ["files"]},
                       {"closure": "closed"}, {"closure": {"completed": 1, "shutdown_confirmed": 1}}):
            with self.assertRaises(ValueError, msg=str(change)):
                fork.checkpoint(state(**change))
        with self.assertRaises(ValueError):
            fork.checkpoint([state()])

    def test_a_running_execution_is_not_a_checkpoint(self):
        with self.assertRaisesRegex(ValueError, "did not close"):
            fork.checkpoint(state(closure={"completed": True, "shutdown_confirmed": False}))
        with self.assertRaisesRegex(ValueError, "non-empty strings: execution"):
            fork.checkpoint(state(execution=None))

    def test_the_digest_is_of_the_whole_receipt(self):
        self.assertNotEqual(fork.checkpoint(state()), fork.checkpoint(state(subject="s1")))
        reordered = {key: state()[key] for key in reversed(list(state()))}
        self.assertEqual(fork.checkpoint(state()), fork.checkpoint(reordered))


class PairTest(unittest.TestCase):
    def setUp(self):
        self.parent = state()

    def test_a_fork_differs_in_what_it_declares_and_nothing_else(self):
        base = run(self.parent, "x1")
        candidate = run(self.parent, "x2", resolved_inputs={"version": "v1", "model": "m", "seed": 1})
        pairing = fork.pair(self.parent, base, candidate, differs=("version",))
        self.assertEqual(pairing["differs"], {"version": [{"value": "v0"}, {"value": "v1"}]})
        self.assertEqual(pairing["not_shared"], [])
        self.assertEqual(pairing["parent"], fork.checkpoint(self.parent))
        with self.assertRaisesRegex(ValueError, "differ in version; this fork declares nothing"):
            fork.pair(self.parent, base, candidate)
        other_seed = run(self.parent, "x2", resolved_inputs={"version": "v1", "model": "m", "seed": 2})
        with self.assertRaisesRegex(ValueError, "differ in seed, version"):
            fork.pair(self.parent, base, other_seed, differs=("version",))
        extra = run(self.parent, "x2", resolved_inputs={"version": "v1", "model": "m", "seed": 1, "grant": "g"})
        with self.assertRaisesRegex(ValueError, "differ in grant, version"):
            fork.pair(self.parent, base, extra, differs=("version",))

    def test_the_same_input_is_the_same_record_not_a_python_equal(self):
        base = {"version": "v0", "model": "m", "seed": 1}
        for other, name in (({**base, "context_policy": None}, "context_policy"),  # absent is not null
                            ({**base, "seed": True}, "seed"),                        # 1 is not true
                            ({**base, "seed": 1.0}, "seed"),
                            ({**base, "options": {"cached": True}}, "options")):
            left = run(self.parent, "x1", resolved_inputs=dict(base, **({"options": {"cached": 1}} if name == "options" else {})))
            right = run(self.parent, "x2", resolved_inputs=other)
            with self.assertRaisesRegex(ValueError, f"differ in {name}; this fork declares nothing"):
                fork.pair(self.parent, left, right)
            self.assertNotEqual(credit._digest("inputs", left["resolved_inputs"]), credit._digest("inputs", other))
        # A second difference is not hidden behind the declared one, and absence is reported as absence.
        left = run(self.parent, "x1")
        right = run(self.parent, "x2", resolved_inputs={"version": "v1", "model": "m", "seed": 1, "context_policy": None})
        with self.assertRaisesRegex(ValueError, "differ in context_policy, version"):
            fork.pair(self.parent, left, right, differs=("version",))
        pairing = fork.pair(self.parent, left, right, differs=("version", "context_policy"))
        self.assertEqual(pairing["differs"]["context_policy"], [{"absent": True}, {"value": None}])
        with self.assertRaisesRegex(ValueError, "list of names"):
            fork.pair(self.parent, left, right, differs="version")

    def test_a_continuation_of_another_type_is_refused(self):
        good = run(self.parent, "x1")
        for change in ({"restored": {**self.parent["captured"], "files": 1}}, {"restored": True},
                       {"parent": 3}, {"result": None}, {"execution": True}, {"costs": {"usd": True}},
                       {"costs": {"usd": "1"}}, {"costs": {"usd": float("inf")}}, {"costs": {"usd": -1}},
                       {"costs": None}, {"omitted": {"context": True}}, {"resolved_inputs": ["v0"]},
                       {"resolved_inputs": {1: "v0"}}, {"closure": {"completed": True, "shutdown_confirmed": "yes"}}):
            with self.assertRaises(ValueError, msg=str(change)):
                fork.pair(self.parent, good, dict(run(self.parent, "x2"), **change))

    def test_a_null_fork_differs_in_nothing(self):
        pairing = fork.pair(self.parent, run(self.parent, "x1"), run(self.parent, "x2"))
        self.assertEqual(pairing["differs"], {})
        with self.assertRaisesRegex(ValueError, "differ in nothing; this fork declares version"):
            fork.pair(self.parent, run(self.parent, "x1"), run(self.parent, "x2"), differs=("version",))

    def test_both_sides_continue_this_checkpoint_as_new_executions(self):
        other = state(subject="s9")
        with self.assertRaisesRegex(ValueError, "both sides continue this checkpoint"):
            fork.pair(self.parent, run(self.parent, "x1"), run(other, "x2"))
        with self.assertRaisesRegex(ValueError, "new execution"):
            fork.pair(self.parent, run(self.parent, "x1"), run(self.parent, "x1"))
        with self.assertRaisesRegex(ValueError, "new execution"):  # the checkpoint's own execution, resumed
            fork.pair(self.parent, run(self.parent, "x0"), run(self.parent, "x2"))
        with self.assertRaisesRegex(ValueError, "new execution"):
            fork.pair(self.parent, run(self.parent, "x1"), run(self.parent, "x2", allocation="a-x1"))
        with self.assertRaisesRegex(ValueError, "new execution"):  # the original allocation, renewed
            fork.pair(self.parent, run(self.parent, "x1"), run(self.parent, "x2", allocation="a-x0"))
        with self.assertRaisesRegex(ValueError, "inputs it was actually given"):
            fork.pair(self.parent, run(self.parent, "x1"), run(self.parent, "x2", resolved_inputs={}))
        with self.assertRaisesRegex(ValueError, "did not close"):
            fork.pair(self.parent, run(self.parent, "x1"), run(self.parent, "x2", closure={"completed": False}))

    def test_each_side_restored_what_was_captured_or_says_it_did_not(self):
        wrong = run(self.parent, "x2")
        wrong["restored"]["files"] = "another tree"
        with self.assertRaisesRegex(ValueError, "files was not restored"):
            fork.pair(self.parent, run(self.parent, "x1"), wrong)
        silent = run(self.parent, "x2")
        del silent["restored"]["context"]
        with self.assertRaisesRegex(ValueError, "context was not restored"):
            fork.pair(self.parent, run(self.parent, "x1"), silent)
        both = run(self.parent, "x2", omitted={"context": "not reopened"})
        with self.assertRaisesRegex(ValueError, "restored or omitted"):
            fork.pair(self.parent, run(self.parent, "x1"), both)
        said = copy.deepcopy(silent)
        said["omitted"] = {"context": "the session could not be reopened"}
        with self.assertRaisesRegex(ValueError, "did not restore the same state"):
            fork.pair(self.parent, run(self.parent, "x1"), said)
        twin = copy.deepcopy(said)
        twin.update(execution="x3", allocation="a-x3")
        pairing = fork.pair(self.parent, said, twin)
        self.assertEqual(pairing["not_shared"], ["context"])
        with self.assertRaisesRegex(ValueError, "do not share: context"):
            fork.admit(pairing)
        self.assertIs(fork.admit(pairing, tolerated=("context",)), pairing)
        self.assertIs(fork.admit(pairing, tolerated={"context", "files"}), pairing)
        with self.assertRaisesRegex(ValueError, "do not share: context"):
            fork.admit(pairing, tolerated=("context_other",))
        for wrong in ("context", "context_other", b"context", {"context": True}, None, 1, ("context", 1)):
            with self.assertRaisesRegex(ValueError, "list of names", msg=repr(wrong)):  # never a substring
                fork.admit(pairing, tolerated=wrong)

    def test_what_the_checkpoint_never_captured_is_not_shared_either(self):
        parent = state()
        del parent["captured"]["exposure"]
        parent["omitted"]["exposure"] = "not recorded by this harness version"
        pairing = fork.pair(parent, run(parent, "x1"), run(parent, "x2"))
        self.assertEqual(pairing["not_shared"], ["exposure"])
        invented = run(parent, "x2")
        invented["restored"]["exposure"] = "d"
        with self.assertRaisesRegex(ValueError, "exposure was restored but the checkpoint did not capture it"):
            fork.pair(parent, run(parent, "x1"), invented)


class MeasurementTest(unittest.TestCase):
    def setUp(self):
        names = ["a", "b"]
        self.registration = {"pool": "p", "cases": credit.case_set(names), "total": 2, "evaluator": "e1", "environment": "env"}
        self.receipt = {"subject": "s1", "execution": "g1", "measure": credit.measure_id(self.registration),
                        "evaluator": "e1", "environment": "env", "cases": self.registration["cases"],
                        "closure": dict(CLOSED), "admissible": True, "outcome": {"cases": {"a": True, "b": False}}}

    def test_only_the_registered_measure_closed_and_admitted_is_read(self):
        self.assertEqual(fork.measurement(self.receipt, self.registration, "s1"), {"cases": {"a": True, "b": False}})
        for name in ("measure", "evaluator", "environment", "cases"):
            with self.assertRaisesRegex(ValueError, f"{name} is not the registered one"):
                fork.measurement(dict(self.receipt, **{name: "other"}), self.registration, "s1")
        with self.assertRaisesRegex(ValueError, "did not admit"):
            fork.measurement(dict(self.receipt, admissible=False), self.registration, "s1")
        with self.assertRaisesRegex(ValueError, "did not close"):
            fork.measurement(dict(self.receipt, closure={"completed": True}), self.registration, "s1")
        with self.assertRaisesRegex(ValueError, "non-empty strings: subject"):
            fork.measurement(dict(self.receipt, subject={}), self.registration, "s1")
        with self.assertRaisesRegex(ValueError, "evaluator is not the registered one"):
            fork.measurement({k: v for k, v in self.receipt.items() if k != "evaluator"}, self.registration, "s1")

    def test_a_measurement_by_unit_names_its_unit_and_that_unit_s_cases(self):
        by_unit = {"pool": "p", "evaluator": "e1", "environment": "env", "scale": "score",
                   "units": {"q1": {"cases": credit.case_set(["q1-r"]), "total": 1},
                             "q2": {"cases": credit.case_set(["q2-a", "q2-b"]), "total": 2}}}
        receipt = {"subject": "s1", "execution": "g1", "measure": credit.measure_id(by_unit), "evaluator": "e1",
                   "environment": "env", "unit": "q2", "cases": by_unit["units"]["q2"]["cases"], "closure": dict(CLOSED),
                   "admissible": True, "outcome": {"unit": "q2", "cases": {"q2-a": 0.25, "q2-b": 1.0}}}
        self.assertEqual(fork.measurement(receipt, by_unit, "s1"), receipt["outcome"])
        for change, message in (({"unit": "q9"}, "names no unit of the registered measure"),
                                ({"unit": None}, "names no unit of the registered measure"),
                                ({"cases": by_unit["units"]["q1"]["cases"]}, "cases is not the registered one"),
                                ({"unit": "q1"}, "cases is not the registered one"),
                                ({"outcome": {"unit": "q1", "cases": {"q1-r": 0.5}}}, "of unit q1, not of the state's unit q2"),
                                ({"outcome": {"unit": "q2", "cases": {"q2-a": True, "q2-b": False}}}, "a score from 0 to 1")):
            with self.assertRaisesRegex(ValueError, message, msg=str(change)):
                fork.measurement(dict(receipt, **change), by_unit, "s1")

    def test_a_fork_under_a_measure_by_unit_is_measured_as_the_checkpoint_s_unit(self):
        by_unit = {"pool": "p", "evaluator": "e1", "environment": "env", "scale": "score",
                   "units": {"q1": {"cases": credit.case_set(["q1-r"]), "total": 1},
                             "q2": {"cases": credit.case_set(["q2-r"]), "total": 1}}}

        def taken(subject, unit, score):
            return {"subject": subject, "execution": "g-" + subject, "measure": credit.measure_id(by_unit), "evaluator": "e1",
                    "environment": "env", "unit": unit, "cases": by_unit["units"][unit]["cases"], "closure": dict(CLOSED),
                    "admissible": True, "outcome": {"unit": unit, "cases": {unit + "-r": score}}}

        parent = state(measurement_unit="q1", analysis_unit={"id": "2025-07-01", "family": "f"})
        left, right = run(parent, "x1"), run(parent, "x2", resolved_inputs={"version": "v1", "model": "m", "seed": 1})
        row = fork.measured(parent, left, right, [taken("s-x1", "q1", 0.25), taken("s-x2", "q1", 0.75)], by_unit, differs=("version",))
        self.assertEqual([o["cases"]["q1-r"] for o in row["outcomes"]], [0.25, 0.75])
        with self.assertRaisesRegex(ValueError, "of another unit than the state is measured as"):
            fork.measured(parent, left, right, [taken("s-x1", "q1", 0.25), taken("s-x2", "q2", 0.75)], by_unit, differs=("version",))
        for unnamed in (state(), state(measurement_unit="q9"), state(measurement_unit=7)):
            l, r = run(unnamed, "x1"), run(unnamed, "x2", resolved_inputs={"version": "v1", "model": "m", "seed": 1})
            with self.assertRaisesRegex(ValueError, "a checkpoint names the unit its state is measured as"):
                fork.measured(unnamed, l, r, [taken("s-x1", "q1", 0.25), taken("s-x2", "q1", 0.75)], by_unit, differs=("version",))

    def test_a_measurement_is_read_for_the_subject_it_measured(self):
        with self.assertRaisesRegex(ValueError, "another subject"):
            fork.measurement(self.receipt, self.registration, "another-subject")
        for wrong in (None, "", {}, 1):
            with self.assertRaisesRegex(ValueError, "another subject", msg=repr(wrong)):
                fork.measurement(self.receipt, self.registration, wrong)
        with self.assertRaisesRegex(ValueError, "non-empty strings: subject"):
            fork.measurement(dict(self.receipt, subject=None), self.registration, None)

    def test_each_side_of_a_fork_carries_the_measurement_of_what_it_produced(self):
        parent = state()
        left, right = run(parent, "x1"), run(parent, "x2", resolved_inputs={"version": "v1", "model": "m", "seed": 1})
        of_left, of_right = dict(self.receipt, subject="s-x1"), dict(self.receipt, subject="s-x2", execution="g2",
                                                                 outcome={"cases": {"a": True, "b": True}})
        row = fork.measured(parent, left, right, [of_left, of_right], self.registration, differs=("version",))
        self.assertEqual(row["results"], ["s-x1", "s-x2"])
        self.assertEqual(row["outcomes"], [of_left["outcome"], of_right["outcome"]])
        self.assertEqual(row["differs"], {"version": [{"value": "v0"}, {"value": "v1"}]})
        self.assertEqual(len(set(row["measurements"])), 2)
        self.assertFalse(row["measurement_reused"])
        for wrong in ([of_right, of_left], [of_left, of_left], [of_left, dict(of_right, subject="another-subject")]):
            with self.assertRaisesRegex(ValueError, "another subject"):  # swapped, or of a subject that side did not produce
                fork.measured(parent, left, right, wrong, self.registration, differs=("version",))
        # Two sides that produced the same subject can share its one measurement; it is marked as one.
        same_result = run(parent, "x2", resolved_inputs={"version": "v1", "model": "m", "seed": 1}, result="s-x1")
        shared = fork.measured(parent, left, same_result, [of_left, of_left], self.registration, differs=("version",))
        self.assertTrue(shared["measurement_reused"])
        self.assertEqual(len(set(shared["measurements"])), 1)
        for wrong in ([of_left], [of_left, of_right, of_right], {"left": of_left, "right": of_right}, None):
            with self.assertRaisesRegex(ValueError, "one measurement for each side"):
                fork.measured(parent, left, right, wrong, self.registration, differs=("version",))
        with self.assertRaisesRegex(ValueError, "this fork declares nothing"):  # the pairing is still checked
            fork.measured(parent, left, right, [of_left, of_right], self.registration)

    def test_the_outcome_itself_is_over_the_registered_cases_with_definite_results(self):
        for outcome in (None, {}, {"cases": {}}, {"cases": {"a": True}}, {"cases": {"x": True, "y": False}},
                        {"cases": {"a": True, "b": False, "c": True}}, {"cases": ["a", "b"]}, {"passed": 2, "total": 2}):
            with self.assertRaisesRegex(ValueError, "not over the measure's registered cases", msg=repr(outcome)):
                fork.measurement(dict(self.receipt, outcome=outcome), self.registration, "s1")
        for result in ("fail", "pass", 1, 0, None, [True]):
            with self.assertRaisesRegex(ValueError, "exactly true or false", msg=repr(result)):
                fork.measurement(dict(self.receipt, outcome={"cases": {"a": True, "b": result}}), self.registration, "s1")
            with self.assertRaisesRegex(ValueError, "exactly true or false"):  # the same check where it is written
                credit.check_outcome(self.registration, {"cases": {"a": True, "b": result}})
        self.assertIsNone(credit.value({"cases": {"a": "fail", "b": "fail"}}))  # unknown: no pass and no fail


if __name__ == "__main__":
    unittest.main()
