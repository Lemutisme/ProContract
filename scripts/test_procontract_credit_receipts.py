"""A judgment under a receipt protocol, against the real kernel store.

The host here is a stand-in: typed immutable objects read by id and kind. It restores nothing and
runs nothing; these tests show what the judgment reads and refuses, not that a fork was run.
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import procontract_credit as credit
import procontract_fork as fork
import procontract_store as kernel

AVAILABLE = Path(kernel.store_bin()).exists() or bool(os.environ.get("PRO_CONTRACT_STORE_BIN"))
PROTOCOL = "operations-fork/v1"
METHOD = credit.SAMPLED_METHODS[0]
CLOSED = {"completed": True, "shutdown_confirmed": True, "exit": "handoff"}


def outcome(*passing, total=4):
    return {"cases": {f"c{i}": i in passing for i in range(total)}}


class Host:
    """Objects of a kind, kept as written and returned as written."""

    def __init__(self):
        self.objects = {}

    def put(self, kind: str, body: dict) -> str:
        text = json.dumps(body, sort_keys=True)
        ident = "object-" + hashlib.sha256((kind + text).encode()).hexdigest()[:24]
        self.objects[ident] = (kind, text)
        return ident

    def read(self, ident: str, kind: str) -> dict:
        held, text = self.objects[ident]
        if held != kind:
            raise LookupError(f"it is a {held}")
        return json.loads(text)


@unittest.skipUnless(AVAILABLE, "pro-contract-store binary is not built")
class ReceiptProtocolTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store, self.host, self.count, self.states = Path(directory.name), Host(), 0, {}
        self.sealed = {"pool": credit._digest("pool", "sealed cases"), "evaluator": credit._digest("evaluator", "scripted"),
                       "environment": credit._digest("environment", "scripted"),
                       "total": 4, "cases": credit.case_set(f"c{i}" for i in range(4))}
        self.versions = {name: credit._digest("version", name) for name in ("v0", "v1", "v2", "v3")}
        credit.install(self.store, "principal", "scope", self.versions["v0"], "principal",
                       authorization="the principal chose v0", restore_convention="serve the last standing acceptance")

    def terms(self, defect, **criterion):
        criterion = {"receipt_protocol": PROTOCOL, "differs": ["version"], "tolerated": [], **criterion}
        return {"scope": "scope", "defect": defect, "evidence": ["a located segment"],
                "criterion": {"measure": self.sealed, **{k: v for k, v in criterion.items() if v is not None}},
                "convention": "adopt when the criterion is met beyond reach"}

    def contract(self, defect, candidate="v1", **criterion):
        found = credit.locate(self.store, "principal", self.terms(defect, **criterion))["id"]
        credit.propose(self.store, found, self.versions[candidate], base=self.versions["v0"], change=defect, author="improver")
        return found

    def origin(self, task, part="", **changes):
        """A parent state of `task`, which is also the host's analysis unit; `part` tells apart
        several states of one task."""
        subject = credit._digest("state", [task, part])
        body = {"schema": PROTOCOL, "record": task, "subject": subject, "execution": f"x-{task}{part}",
                "allocation": f"a-{task}{part}", "analysis_unit": {"id": task, "family": "f"},
                "state": f"state-{task}{part}",
                "captured": {name: "d-" + name for name in fork.COMPONENTS}, "omitted": {}, "closure": dict(CLOSED)}
        body.update(changes)
        made = {"record": task, "subject": subject, "task": task, "parent": None, "inputs": None, "cost": None,
                "receipt": self.host.put("checkpoint_receipt", body)}
        seq = credit.state(self.store, kernel.identities(), task, subject, task=task, family="f", receipt=made["receipt"])
        self.states[seq] = made
        return seq, body

    def again(self, seq, *, parent=None, task=None):
        """Record a state once more, with the same record, subject, inputs, cost and host receipt:
        nothing new ran. Optionally under another task label or parent sequence."""
        made = self.states[seq]
        return credit.state(self.store, kernel.identities(), made["record"], made["subject"], task=task or made["task"],
                            family="f", parent=made["parent"] if parent is None else parent, inputs=made["inputs"],
                            receipt=made["receipt"], cost=made["cost"])

    def side(self, task, origin, version, passing, *, inputs=None, receipt=None, measured=None,
             kind="continuation_receipt", reference=True, labelled=True):
        seq, parent = origin
        self.count += 1
        subject = credit._digest("state", [task, version, self.count])
        inputs, cost = inputs or {"version": self.versions[version]}, {"requests": 10}
        body = {"schema": PROTOCOL, "parent": fork.checkpoint(parent), "execution": f"x-{task}-{self.count}",
                "allocation": f"a-{task}-{self.count}", "resolved_inputs": inputs, "restored": dict(parent["captured"]),
                "omitted": {}, "closure": dict(CLOSED), "costs": cost, "result": subject}
        body.update(receipt or {})
        made = {"record": task, "subject": subject, "task": task, "parent": seq, "inputs": inputs, "cost": cost,
                "receipt": self.host.put(kind, body) if reference else credit._digest("execution", self.count)}
        state = credit.state(self.store, kernel.identities(harness=version), task, subject, task=task, family="f",
                             parent=seq, inputs=inputs, cost=cost, receipt=made["receipt"])
        self.states[state] = made
        result = outcome(*passing)
        taken = {"schema": PROTOCOL, "subject": subject, "execution": f"g-{self.count}", "measure": credit.measure_id(self.sealed),
                 "evaluator": self.sealed["evaluator"], "environment": self.sealed["environment"],
                 "cases": self.sealed["cases"], "closure": dict(CLOSED), "admissible": True, "outcome": result}
        taken.update(measured or {})
        credit.measure(self.store, self.sealed, subject, result,
                       receipt=self.host.put("measurement_receipt", taken) if labelled else None)
        return state

    def pairs(self, name, tasks=4, **candidate_side):
        made = []
        for index in range(tasks):
            origin = self.origin(f"{name}-{index}")
            made.append((self.side(f"{name}-{index}", origin, "v0", (0,)),
                         self.side(f"{name}-{index}", origin, "v1", (0, 1, 2), **(candidate_side if index == 0 else {}))))
        return made

    def refused(self, contract, pairs, message, **more):
        before = len(kernel.events(self.store))
        with self.assertRaisesRegex(ValueError, message):
            credit.judge(self.store, contract, pairs, reader=self.host.read, **more)
        self.assertEqual(len(kernel.events(self.store)), before)  # nothing retained, no Support

    def test_a_judgment_reads_the_hosts_receipts(self):
        contract, pairs = self.contract("d1"), self.pairs("read")
        with self.assertRaisesRegex(ValueError, "the host's reader is required"):
            credit.judge(self.store, contract, pairs)
        judged = credit.judge(self.store, contract, pairs, reader=self.host.read)
        statement = judged["statement"]
        self.assertEqual(statement["unmet"], [])
        self.assertEqual(statement["receipt_protocol"], PROTOCOL)
        self.assertEqual(len(statement["fork_receipts"]), 4)
        first = statement["fork_receipts"][0]
        self.assertEqual(first["not_shared"], [])
        self.assertFalse(first["measurement_reused"])
        # The request's shape is part of a retained judgment's identity. Without a sampling design
        # it is exactly what it was before sampling existed: a repeat or a recovery of an older
        # judgment must find its own request, not make a second statement and a second exposure.
        self.assertEqual(set(first), {"unit", "executions", "measured_by", "checkpoint", "continuations", "measurements",
                                      "differs", "not_shared", "measurement_reused"})
        self.assertEqual(set(statement) & {"sampling", "shared", "fork_receipts", "receipt_protocol"},
                         {"shared", "fork_receipts", "receipt_protocol"})
        asked = {"contract": contract, "revision": statement["revision"], "terms_hash": statement["terms_hash"],
                 "generation": statement["generation"], "candidate": statement["candidate"], "proposal": statement["proposal"],
                 "views": credit._proposal(kernel.events(self.store), contract, statement["candidate"])["views"],
                 "forks": statement["forks"], "null": statement["null"], "measurements": statement["measurements"],
                 "receipt_protocol": PROTOCOL, "fork_receipts": statement["fork_receipts"], "shared": statement["shared"]}
        self.assertEqual(credit._digest("judgment", asked), statement["request"])
        self.assertEqual(first["differs"], {"version": [{"value": self.versions["v0"]}, {"value": self.versions["v1"]}]})
        reference, digest = first["checkpoint"]  # the host's object id and the receipt's own digest stay distinct
        self.assertEqual(fork.checkpoint(self.host.read(reference, "checkpoint_receipt")), digest)
        self.assertNotEqual(reference, digest)
        self.assertEqual(kernel.contract(self.store, contract)["support"]["coordinate"]["reach"], "beyond")
        again = credit.judge(self.store, contract, pairs, reader=self.host.read)
        self.assertEqual(again["statement"]["request"], statement["request"])
        with self.assertRaisesRegex(ValueError, "the host's reader is required"):  # a repeat reads them again
            credit.judge(self.store, contract, pairs)

    def test_a_missing_receipt_is_not_read_as_an_older_kind_of_record(self):
        contract = self.contract("d1")
        self.refused(contract, self.pairs("legacy", reference=False), "no continuation_receipt is retained as")
        self.refused(contract, self.pairs("unlabelled", labelled=False), "references its measurement_receipt")
        self.refused(contract, self.pairs("kind", kind="measurement_receipt"), "no continuation_receipt is retained as .*it is a measurement_receipt")
        self.refused(contract, self.pairs("schema", receipt={"schema": "operations-fork/v0"}), "is not a operations-fork/v1 continuation_receipt")
        bare = []
        for index in range(4):
            subject = credit._digest("state", f"bare-{index}")
            seq = credit.state(self.store, kernel.identities(), f"bare-{index}", subject, task=f"bare-{index}", family="f")
            body = {"schema": PROTOCOL, "record": f"bare-{index}", "subject": subject, "execution": "x", "allocation": "a",
                    "captured": {name: "d-" + name for name in fork.COMPONENTS}, "omitted": {}, "closure": dict(CLOSED)}
            bare.append((self.side(f"bare-{index}", (seq, body), "v0", (0,)), self.side(f"bare-{index}", (seq, body), "v1", (0, 1, 2))))
        self.refused(contract, bare, "references its checkpoint_receipt")

    def test_receipts_are_joined_to_what_the_journal_records(self):
        contract = self.contract("d1")
        other = self.origin("elsewhere")[1]
        for name, changes, message in (
                ("result", {"receipt": {"result": "another-subject"}}, "continuation receipt's result is not what is recorded"),
                ("inputs", {"receipt": {"resolved_inputs": {"version": self.versions["v1"], "seed": 1}}},
                 "continuation receipt's resolved_inputs is not what is recorded"),
                ("costs", {"receipt": {"costs": {"requests": 1}}}, "continuation receipt's costs is not what is recorded"),
                ("outcome", {"measured": {"outcome": outcome(0, 1, 2, 3)}}, "measurement receipt's outcome is not the outcome of label"),
                ("subject", {"measured": {"subject": "another-subject"}}, "of another subject"),
                ("parent", {"receipt": {"parent": fork.checkpoint(other)}}, "both sides continue this checkpoint"),
                ("allocation", {"receipt": {"allocation": "a-allocation-0"}}, "new execution under its own allocation"),
                ("closure", {"receipt": {"closure": {"completed": True, "shutdown_confirmed": False}}}, "did not close"),
                ("admitted", {"measured": {"admissible": False}}, "did not admit")):
            self.refused(contract, self.pairs(name, **changes), message)
        wrong = []
        for index in range(4):
            origin = self.origin(f"origin-{index}", subject="another-subject")
            wrong.append((self.side(f"origin-{index}", origin, "v0", (0,)), self.side(f"origin-{index}", origin, "v1", (0, 1, 2))))
        self.refused(contract, wrong, "checkpoint receipt is of another record or subject")
        credit.judge(self.store, contract, self.pairs("sound"), reader=self.host.read)  # none of that stood in the way

    def test_the_terms_decide_what_may_go_unshared(self):
        unrestored = {"restored": {name: "d-" + name for name in fork.COMPONENTS if name != "context"},
                      "omitted": {"context": "the session could not be reopened"}}

        def partial(name):
            made = []
            for index in range(4):
                origin = self.origin(f"{name}-{index}")
                made.append((self.side(f"{name}-{index}", origin, "v0", (0,), receipt=unrestored),
                             self.side(f"{name}-{index}", origin, "v1", (0, 1, 2), receipt=unrestored)))
            return made

        strict = self.contract("d1")
        self.refused(strict, partial("strict"), "the sides do not share: context")
        lenient = self.contract("d2", candidate="v2", tolerated=["context"])
        made = []
        for index in range(4):
            origin = self.origin(f"lenient-{index}")
            made.append((self.side(f"lenient-{index}", origin, "v0", (0,), receipt=unrestored),
                         self.side(f"lenient-{index}", origin, "v2", (0, 1, 2), receipt=unrestored)))
        judged = credit.judge(self.store, lenient, made, reader=self.host.read)
        self.assertEqual([row["not_shared"] for row in judged["statement"]["fork_receipts"]], [["context"]] * 4)
        one_sided = []
        for index in range(4):
            origin = self.origin(f"one-{index}")
            one_sided.append((self.side(f"one-{index}", origin, "v0", (0,)),
                              self.side(f"one-{index}", origin, "v1", (0, 1, 2), receipt=unrestored)))
        self.refused(strict, one_sided, "did not restore the same state")

    def test_the_terms_decide_what_may_differ(self):
        def made(name, candidate, left, right):
            pairs = []
            for index in range(4):
                origin = self.origin(f"{name}-{index}")
                pairs.append((self.side(f"{name}-{index}", origin, "v0", (0,), inputs={"version": self.versions["v0"], **left}),
                              self.side(f"{name}-{index}", origin, candidate, (0, 1, 2),
                                        inputs={"version": self.versions[candidate], **right})))
            return pairs

        only_version = self.contract("d1")
        self.refused(only_version, made("extra", "v1", {"strategy": "s0"}, {"strategy": "s1"}), "version and in nothing else")
        both = self.contract("d2", candidate="v2", differs=["version", "strategy"])
        self.refused(both, made("less", "v2", {"strategy": "s0"}, {"strategy": "s0"}), "exactly what the terms declare: strategy, version")
        self.refused(both, made("typed", "v2", {"strategy": 1}, {"strategy": True, "seed": None}), "exactly what the terms declare")
        judged = credit.judge(self.store, both, made("both", "v2", {"strategy": "s0"}, {"strategy": "s1"}), reader=self.host.read)
        self.assertEqual(sorted(judged["statement"]["fork_receipts"][0]["differs"]), ["strategy", "version"])

    def test_a_task_is_the_hosts_unit_not_a_label_in_the_journal(self):
        contract = self.contract("d1", min_tasks=4, min_effect=0.25, lower_bound=0)
        origin = self.origin("one-actual-task")
        base = self.side("one-actual-task", origin, "v0", (0,))
        candidate = self.side("one-actual-task", origin, "v1", (0, 1, 2))
        objects = len(self.host.objects)
        aliased = []
        for index in range(4):  # the same five host objects, recorded under four task names
            parent = self.again(origin[0], task=f"alias-{index}")
            aliased.append((self.again(base, parent=parent, task=f"alias-{index}"),
                            self.again(candidate, parent=parent, task=f"alias-{index}")))
        self.assertEqual(len(self.host.objects), objects)
        self.refused(contract, aliased, "another task or family than the analysis unit")
        repeated = [(base, candidate)]
        for _ in range(3):  # recorded again under its true unit, it is still one pair of executions
            parent = self.again(origin[0])
            repeated.append((self.again(base, parent=parent), self.again(candidate, parent=parent)))
        self.refused(contract, repeated, "the same two executions are named twice")
        judged = credit.judge(self.store, contract, [(base, candidate)], reader=self.host.read)
        self.assertEqual(judged["statement"]["effect"]["tasks"], 1)  # one pair is one task
        self.assertEqual(judged["statement"]["unmet"], ["too few tasks"])
        self.assertIsNone(kernel.contract(self.store, contract)["support"])

    def test_a_checkpoint_names_the_unit_it_belongs_to(self):
        contract = self.contract("d1")

        def made(name, **changes):
            pairs = []
            for index in range(4):
                origin = self.origin(f"{name}-{index}", **changes)
                pairs.append((self.side(f"{name}-{index}", origin, "v0", (0,)), self.side(f"{name}-{index}", origin, "v1", (0, 1, 2))))
            return pairs

        self.refused(contract, made("none", analysis_unit=None), "names its analysis unit")
        self.refused(contract, made("shape", analysis_unit={"id": "u"}), "names its analysis unit")
        self.refused(contract, made("typed", analysis_unit={"id": 7, "family": "f"}), "names its analysis unit")
        self.refused(contract, made("family", analysis_unit={"id": "family-0", "family": "another"}),
                     "another task or family than the analysis unit")

    def test_states_of_one_unit_are_one_task_and_new_executions_are_new_forks(self):
        contract = self.contract("d1", cost_unit="requests")
        first, second = self.origin("unit", "-a"), self.origin("unit", "-b")  # two parent states of one task
        pairs = [(self.side("unit", first, "v0", (0,)), self.side("unit", first, "v1", (0, 1, 2))),
                 (self.side("unit", first, "v0", (0,)), self.side("unit", first, "v1", (0, 1))),  # continued again
                 (self.side("unit", second, "v0", (0,)), self.side("unit", second, "v1", (0, 1, 2)))]
        statement = credit.judge(self.store, contract, pairs, reader=self.host.read)["statement"]
        self.assertEqual(statement["effect"]["tasks"], 1)
        self.assertEqual(len(statement["fork_receipts"]), 3)
        self.assertEqual({row["unit"]["id"] for row in statement["fork_receipts"]}, {"unit"})
        self.assertEqual(statement["shared"], {"executions": [], "measurements": []})
        self.assertEqual((statement["effect"]["cost_base"], statement["effect"]["cost_candidate"]), (30, 30))

    def test_a_shared_execution_needs_the_terms_and_is_counted_once(self):
        def one_control_two_candidates(name, candidate):
            pairs = []
            for index in range(4):
                origin = self.origin(f"{name}-{index}")
                control = self.side(f"{name}-{index}", origin, "v0", (0,))
                pairs += [(control, self.side(f"{name}-{index}", origin, candidate, (0, 1, 2))),
                          (control, self.side(f"{name}-{index}", origin, candidate, (0, 1)))]
            return pairs

        strict = self.contract("d1", cost_unit="requests")
        self.refused(strict, one_control_two_candidates("strict", "v1"), "used in more than one pair")
        provided = self.contract("d2", candidate="v2", shared_executions=True, cost_unit="requests")
        statement = credit.judge(self.store, provided, one_control_two_candidates("provided", "v2"),
                                 reader=self.host.read)["statement"]
        self.assertEqual(statement["effect"]["tasks"], 4)
        self.assertEqual(len(statement["shared"]["executions"]), 4)    # each control, named by two pairs
        self.assertEqual(len(statement["shared"]["measurements"]), 4)  # and its one measurement
        self.assertEqual((statement["effect"]["cost_base"], statement["effect"]["cost_candidate"]), (40, 80))

    def population(self, name):
        """Eight units with one state each, four where the candidate's first stage found the
        decision changed and four where it did not, and a design, fixed before any candidate, that
        draws every changed state and each unchanged one with probability one half."""
        origins = {f"{name}-{index}": self.origin(f"{name}-{index}") for index in range(8)}
        frame = [{"state": body["state"], "unit": task, "stratum": "changed" if index < 4 else "unchanged"}
                 for index, (task, (_, body)) in enumerate(origins.items())]
        design = {"estimand": "state_mean", "method": METHOD, "population": credit.population_id(frame),
                  "strata": {"changed": 1.0, "unchanged": 0.5}}
        return origins, frame, design

    def draw(self, contract, frame, candidate="v1", seed=None, generation=None, changes=None):
        """What the host retains once the strata are fixed: the frame, then one draw on it. The
        seed here is the first that draws some but not all unchanged states."""
        body = {"schema": PROTOCOL, "candidate": self.versions[candidate], "first_stage": "object-first-stage", "frame": frame}
        body.update(changes or {})
        reference = self.host.put("sampling_frame", body)
        design = credit._terms(kernel.events(self.store), kernel.contract(self.store, contract))["criterion"]["sampling"]
        for attempt in range(100):
            chosen = seed or f"host-seed-{attempt}"
            try:
                drawn = credit.sample(design, body["frame"], chosen)
            except ValueError:
                drawn = {}
                break
            if seed or 5 <= len(drawn) <= 7:
                break
        made = {"schema": PROTOCOL, "contract": contract, "candidate": self.versions[candidate],
                "generation": kernel.contract(self.store, contract)["candidate"]["generation"] if generation is None else generation,
                "frame": [reference, credit._digest("sampling_frame", body)], "seed": chosen}
        return self.host.put("sampling_draw", made), drawn

    def forks_of(self, origins, frame, drawn, candidate="v1"):
        unit = {row["state"]: row["unit"] for row in frame}
        return [(self.side(unit[state], origins[unit[state]], "v0", (0,)),
                 self.side(unit[state], origins[unit[state]], candidate, (0, 1, 2))) for state in drawn]

    def test_a_sampled_judgment_weights_its_forks_by_the_design(self):
        origins, frame, design = self.population("drawn")
        contract = self.contract("d1", sampling=design, cost_unit="requests")
        reference, drawn = self.draw(contract, frame)
        pairs = self.forks_of(origins, frame, drawn)
        self.refused(contract, pairs, "references its sampling_draw")                      # no draw named
        self.refused(contract, pairs[:-1], "1 of %d drawn states are not judged" % len(drawn), sampling=reference)
        undrawn = next(row for row in frame if row["state"] not in drawn)
        extra = (self.side(undrawn["unit"], origins[undrawn["unit"]], "v0", (0,)),
                 self.side(undrawn["unit"], origins[undrawn["unit"]], "v1", (0, 1, 2)))
        self.refused(contract, pairs + [extra], "is judged but was not drawn", sampling=reference)
        self.refused(contract, pairs, "no sampling_draw is retained as", sampling="object-unknown")

        statement = credit.judge(self.store, contract, pairs, reader=self.host.read, sampling=reference)["statement"]
        effect = statement["effect"]
        self.assertTrue(effect["sampled"])
        self.assertEqual(effect["tasks"], len(drawn))
        scale = sum(drawn.values()) / 8                    # the drawn weights over the population's eight states
        self.assertAlmostEqual(effect["mean"], 0.5 * scale)                                # every drawn state gains 0.5
        whole = credit.population(design, frame)
        self.assertAlmostEqual(effect["high"] - effect["low"], 2 * (20 * whole["variance_bound"]) ** 0.5)
        self.assertEqual(effect["method"], METHOD)
        self.assertEqual(statement["sampling"]["design"]["method"], METHOD)
        self.assertAlmostEqual(effect["cost_base"], 10 * scale)                            # per state, estimated
        self.assertAlmostEqual(effect["cost_ratio"], 1.0)
        self.assertEqual((effect["paid_base"], effect["paid_candidate"]), (10 * len(drawn), 10 * len(drawn)))
        self.assertEqual(statement["unmet"], [])                                           # these terms ask for no bound
        self.assertEqual(statement["sampling"]["states"], 8)
        self.assertEqual(statement["sampling"]["drawn"], len(drawn))
        self.assertEqual(statement["sampling"]["strata"]["changed"], {"frame": 4, "drawn": 4})
        self.assertEqual(statement["sampling"]["strata"]["unchanged"], {"frame": 4, "drawn": len(drawn) - 4})
        self.assertEqual(statement["sampling"]["draw"][0], reference)
        self.assertTrue(all(row["state"] in drawn for row in statement["fork_receipts"]))
        self.assertEqual(statement["sampling"]["first_stage"], "object-first-stage")
        again = credit.judge(self.store, contract, pairs, reader=self.host.read, sampling=reference)
        self.assertEqual(again["statement"]["request"], statement["request"])

    def test_a_draw_is_for_this_candidate_and_this_frame(self):
        origins, frame, design = self.population("bound")
        contract = self.contract("d1", sampling=design)
        reference, drawn = self.draw(contract, frame)
        pairs = self.forks_of(origins, frame, drawn)
        seed = self.host.read(reference, "sampling_draw")["seed"]
        # Strata set differently after the draw are another frame: the draw was not made on it.
        flipped = [dict(row, stratum="unchanged" if row["stratum"] == "changed" else "changed") for row in frame]
        other = self.host.put("sampling_frame", {"schema": PROTOCOL, "candidate": self.versions["v1"],
                                                 "first_stage": "object-first-stage", "frame": flipped})
        swapped = dict(self.host.read(reference, "sampling_draw"), frame=[other, self.host.read(reference, "sampling_draw")["frame"][1]])
        self.refused(contract, pairs, "not the one the draw was made on", sampling=self.host.put("sampling_draw", swapped))
        for change, message in (({"contract": "another.1"}, "another contract"), ({"generation": 99}, "another generation"),
                                ({"candidate": self.versions["v2"]}, "another candidate"), ({"seed": ""}, "names its frame by id and digest"),
                                ({"frame": "object"}, "names its frame by id and digest")):
            altered = dict(self.host.read(reference, "sampling_draw"), **change)
            self.refused(contract, pairs, message, sampling=self.host.put("sampling_draw", altered))
        for change, message in (({"candidate": self.versions["v2"]}, "measured for another candidate"),
                                ({"first_stage": ""}, "names the record of the first stage"),
                                ({"frame": frame[:-1]}, "not the population the design names"),
                                ({"frame": [dict(row, unit="elsewhere") for row in frame]}, "not the population the design names")):
            self.refused(contract, pairs, message, sampling=self.draw(contract, frame, seed=seed, changes=change)[0])
        credit.judge(self.store, contract, pairs, reader=self.host.read, sampling=reference)

    def test_a_judged_state_is_the_population_s_state_not_a_receipt_digest(self):
        origins, frame, design = self.population("identity")
        contract = self.contract("d1", sampling=design)
        reference, drawn = self.draw(contract, frame)
        unit = {row["state"]: row["unit"] for row in frame}
        pairs = []
        for state in drawn:  # the same state under a receipt whose other fields differ is the same state
            task = unit[state]
            origin = self.origin(task, note="recorded again with another note")
            pairs.append((self.side(task, origin, "v0", (0,)), self.side(task, origin, "v1", (0, 1, 2))))
        credit.judge(self.store, contract, pairs, reader=self.host.read, sampling=reference)
        nameless, drawn2 = self.contract("d2", candidate="v2", sampling=design), None
        reference2, drawn2 = self.draw(nameless, frame, candidate="v2")
        missing = []
        for state in drawn2:
            task = unit[state]
            origin = self.origin(task, state=None)
            missing.append((self.side(task, origin, "v0", (0,)), self.side(task, origin, "v2", (0, 1, 2))))
        self.refused(nameless, missing, "names the state it is in the population", sampling=reference2)

    def test_terms_that_name_no_method_are_read_back_not_judged(self):
        """Terms issued before the method was part of a design, with a statement an earlier writer
        left before its Support: finishing it now would settle an estimate nobody named."""
        origins, frame, design = self.population("unnamed")
        older = {k: v for k, v in design.items() if k != "method"}
        checked = credit._design
        credit._design = lambda value: value                     # as an earlier module accepted them
        try:
            contract = self.contract("d1", sampling=older)
        finally:
            credit._design = checked
        body = {"schema": PROTOCOL, "candidate": self.versions["v1"], "first_stage": "object-first-stage", "frame": frame}
        reference = self.host.put("sampling_frame", body)
        drawn = credit.sample(design, frame, "host-seed")
        draw = self.host.put("sampling_draw", {
            "schema": PROTOCOL, "contract": contract, "candidate": self.versions["v1"],
            "generation": kernel.contract(self.store, contract)["candidate"]["generation"],
            "frame": [reference, credit._digest("sampling_frame", body)], "seed": "host-seed"})
        pairs = self.forks_of(origins, frame, drawn)
        kernel.append(self.store, "insight", kernel.identities(), {
            "request": "what the earlier writer asked", "contract": contract, "unmet": [], "forks": [list(p) for p in pairs],
            "null": [], "effect": {"mean": 0.5, "low": 0.5, "high": 0.5}, "reach": "beyond"})
        self.refused(contract, pairs, "names no estimation method this module implements", sampling=draw)
        self.assertIsNone(kernel.contract(self.store, contract)["support"])

    def test_forks_of_one_drawn_state_share_its_weight(self):
        origins, frame, design = self.population("twice")
        contract = self.contract("d1", sampling=design)
        reference, drawn = self.draw(contract, frame)
        unit = {row["state"]: row["unit"] for row in frame}
        pairs = []
        for state in drawn:
            task = unit[state]
            for passing in ((0, 1, 2), (0, 1)):  # the same state continued twice
                pairs.append((self.side(task, origins[task], "v0", (0,)), self.side(task, origins[task], "v1", passing)))
        effect = credit.judge(self.store, contract, pairs, reader=self.host.read, sampling=reference)["statement"]["effect"]
        self.assertEqual((effect["tasks"], effect["forks"]), (len(drawn), 2 * len(drawn)))
        self.assertAlmostEqual(effect["mean"], 0.375 * sum(drawn.values()) / 8)   # (0.5 + 0.25) / 2 in every drawn state

    def test_a_sampled_judgment_settles_only_through_its_design_interval(self):
        origins, frame, design = self.population("bounded")
        contract = self.contract("d1", sampling=design, lower_bound=0)
        reference, drawn = self.draw(contract, frame)
        pairs = self.forks_of(origins, frame, drawn)
        quiet = [(self.side(task, origin, "v0", (0,)), self.side(task, origin, "v0", (0,)))
                 for task, origin in list(origins.items())[:1]]
        self.refused(contract, pairs, "it reads no null forks", sampling=reference, null=quiet)
        statement = credit.judge(self.store, contract, pairs, reader=self.host.read, sampling=reference)["statement"]
        self.assertLess(statement["effect"]["low"], 0)        # eight states cannot bound an effect away from zero
        self.assertEqual(statement["unmet"], ["interval reaches the stated bound or is undefined"])
        self.assertIsNone(kernel.contract(self.store, contract)["support"])

    def series(self, name, units=6, lag=3):
        """Units in an order, the first with two states, and a design, fixed before any candidate,
        over exactly them. Returns the roster, each state's (task, origin), and the design."""
        roster, origins = [], {}
        for index in range(units):
            task = f"{name}-{index}"
            parts = ("a", "b") if index == 0 else ("",)
            for part in parts:
                origins[f"state-{task}{part}"] = (task, self.origin(task, part))
            roster.append({"unit": task, "states": [f"state-{task}{part}" for part in parts]})
        return roster, origins, {"method": credit.SERIES_METHODS[0], "roster": credit.roster_id(roster), "lag": lag}

    def over(self, origins, gains, candidate="v1"):
        """One fork of each state; the candidate adds `gains` cases, in turn, to the one the base passes."""
        return [(self.side(task, origin, "v0", (0,)),
                 self.side(task, origin, candidate, tuple(range(1 + gains[index % len(gains)]))))
                for index, (task, origin) in enumerate(origins.values())]

    def test_a_judgment_over_a_series_reads_the_roster_the_terms_name(self):
        roster, origins, design = self.series("ordered")
        contract = self.contract("d1", series=design, lower_bound=0, min_tasks=6, cost_unit="requests")
        pairs = self.over(origins, (2, 1, 2, 2, 1, 2, 1))
        self.refused(contract, pairs, "the roster they name is required")                         # none given
        self.refused(contract, pairs, "the roster they name is required", roster=roster[::-1])    # another order
        self.refused(contract, pairs, "the roster they name is required", roster=roster[:-1])     # a unit dropped
        self.refused(contract, pairs[1:], "1 of 7 states of the roster have no fork", roster=roster)
        stray = self.origin("elsewhere")
        extra = (self.side("elsewhere", stray, "v0", (0,)), self.side("elsewhere", stray, "v1", (0, 1)))
        self.refused(contract, pairs + [extra], "which the roster does not list", roster=roster)
        quiet = [(self.side("elsewhere", stray, "v0", (0,)), self.side("elsewhere", stray, "v0", (0,)))]
        self.refused(contract, pairs, "it reads no null forks", roster=roster, null=quiet)

        statement = credit.judge(self.store, contract, pairs, reader=self.host.read, roster=roster)["statement"]
        effect = statement["effect"]
        # Unit 0 has two states, +0.5 and +0.25; then +0.5, +0.5, +0.25, +0.5, +0.25: six units.
        points = [0.375, 0.5, 0.5, 0.25, 0.5, 0.25]
        mean = sum(points) / 6
        plain = (sum((x - mean) ** 2 for x in points) / 5 / 6) ** 0.5
        g = [sum((points[i] - mean) * (points[i - l] - mean) for i in range(l, 6)) / 6 for l in range(4)]
        bartlett = ((g[0] + 2 * (0.75 * g[1] + 0.5 * g[2] + 0.25 * g[3])) / 5) ** 0.5
        self.assertEqual((effect["tasks"], effect["forks"], effect["method"]), (6, 7, credit.SERIES_METHODS[0]))
        self.assertAlmostEqual(effect["mean"], mean, places=14)
        self.assertAlmostEqual(effect["interval"]["plain_se"], plain, places=14)
        self.assertAlmostEqual(effect["interval"]["bartlett_se"], bartlett, places=14)
        self.assertAlmostEqual(effect["low"], mean - 2.5705818356363146 * max(plain, bartlett), places=13)  # t(.975, 5)
        self.assertEqual(statement["unmet"], [])
        self.assertEqual(statement["series"], {"design": design, "units": 6, "states": 7})
        self.assertEqual(sorted(row["state"] for row in statement["fork_receipts"]), sorted(origins))
        self.assertEqual((effect["cost_base"], effect["cost_candidate"]), (70, 70))
        self.assertEqual(kernel.contract(self.store, contract)["support"]["coordinate"]["reach"], "beyond")
        again = credit.judge(self.store, contract, pairs, reader=self.host.read, roster=roster)
        self.assertEqual(again["statement"]["request"], statement["request"])
        self.assertEqual(len(credit._statements(kernel.events(self.store), contract)), 1)

    def test_a_support_made_without_the_stated_method_is_not_adopted(self):
        """A writer older than the series design reads the same terms without it: it needs no
        roster, resamples the units and can support the candidate. This module does not adopt
        that Support, with or without the principal; withdrawn, the candidate is judged as the
        terms state."""
        roster, origins, design = self.series("older", units=8)
        contract = self.contract("d1", series=design, lower_bound=0.01, cost_unit="requests")
        pairs = self.over(origins, (2,) * 5 + (0,) * 4)          # four units gain, four do not, in two blocks
        # Resampled, the lower end is one gaining unit in eight, 0.0625; over the series it is below zero.
        stated = credit._terms

        def as_an_older_writer_reads_them(events, found):
            terms = stated(events, found)
            return dict(terms, criterion={k: v for k, v in terms["criterion"].items() if k != "series"})

        with mock.patch.object(credit, "_terms", as_an_older_writer_reads_them):
            old = credit.judge(self.store, contract, pairs, reader=self.host.read)["statement"]
        self.assertEqual((old["unmet"], "method" in old["effect"], "series" in old), ([], False, False))
        self.assertIsNotNone(kernel.contract(self.store, contract)["support"])
        for authority in ({}, {"explicit": "the principal accepts"}):
            with self.assertRaisesRegex(ValueError, "did not use the method the terms state"):
                credit.adopt(self.store, contract, **authority)
        self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v0"])
        self.refused(contract, pairs, "already carries a judgment on other inputs", roster=roster)
        credit.withdraw(self.store, contract, "judged without the method the terms state")
        judged = credit.judge(self.store, contract, pairs, reader=self.host.read, roster=roster)["statement"]
        self.assertEqual(judged["effect"]["method"], design["method"])
        self.assertEqual(judged["unmet"], ["interval reaches the stated bound or is undefined"])   # in blocks the bound is lower
        self.assertIsNone(kernel.contract(self.store, contract)["support"])
        # Judged as stated and met, the same kind of Support is adopted.
        roster2, origins2, design2 = self.series("stated-method", units=8)
        met = self.contract("d2", candidate="v2", series=design2, lower_bound=0.1)
        credit.judge(self.store, met, self.over(origins2, (2,), candidate="v2"), reader=self.host.read, roster=roster2)
        self.assertEqual(credit.adopt(self.store, met)["standing"], "discharged")

    def test_adoption_rejects_designs_an_older_writer_did_not_validate(self):
        """An absent method cannot match another absent method; an empty design is not absence.

        The old writer accepted the terms and ignored their design while judging. Reject a new
        adoption without changing that Support or the incumbent, under either authority.
        """
        from unittest import mock
        invalid = [
            ("series", {}),
            ("series", {"roster": "fixed-roster", "lag": 3}),
            ("series", None),
            ("series", "old-writer-ignored-this"),
            ("sampling", {}),
            ("sampling", {"estimand": "state_mean", "population": "fixed-population",
                          "strata": {"all": 1.0}}),
        ]
        for index, (name, design) in enumerate(invalid):
            with self.subTest(design=name, value=design):
                with mock.patch.object(credit, "_check", return_value=None):
                    # The normal fixture omits None fields; this old-writer control retains
                    # the key even for a null design, just as the unvalidated terms state it.
                    terms = self.terms(f"old-unvalidated-{index}")
                    terms["criterion"][name] = design
                    contract = credit.locate(self.store, "principal", terms)["id"]
                    credit.propose(self.store, contract, self.versions["v1"], base=self.versions["v0"],
                                   change=f"old-unvalidated-{index}", author="improver")
                pairs = self.pairs(f"old-unvalidated-{index}")
                stated = credit._terms

                def old_terms(events, found):
                    terms = stated(events, found)
                    return dict(terms, criterion={k: v for k, v in terms["criterion"].items() if k != name})

                with mock.patch.object(credit, "_terms", old_terms):
                    credit.judge(self.store, contract, pairs, reader=self.host.read)
                supported = kernel.contract(self.store, contract)
                self.assertIsNotNone(supported["support"])
                count = len(kernel.events(self.store))
                for authority in ({}, {"explicit": "the principal accepts"}):
                    with self.assertRaises(ValueError):
                        credit.adopt(self.store, contract, **authority)
                    self.assertEqual(len(kernel.events(self.store)), count)
                    self.assertEqual(kernel.contract(self.store, contract), supported)
                    self.assertEqual(credit.incumbent(self.store)["version"], self.versions["v0"])

    def test_a_series_design_is_stated_whole_and_a_roster_is_never_ignored(self):
        roster, origins, design = self.series("stated")
        _, _, sampling = self.population("both")
        for change, message in (({"receipt_protocol": None, "differs": None, "tolerated": None}, "read only under a"),
                                ({"sampling": sampling}, "over a sample or over a series, not both"),
                                ({"above_noise": True}, "does not define: above_noise")):
            with self.assertRaisesRegex(ValueError, message, msg=str(change)):
                credit.locate(self.store, "principal", self.terms("d0", series=design, **change))
        for wrong in (dict(design, lag=-1), dict(design, lag=True), dict(design, lag=3.0), dict(design, roster=""),
                      dict(design, roster=roster), dict(design, seed="s"), {k: v for k, v in design.items() if k != "lag"}):
            with self.assertRaisesRegex(ValueError, "a series design states", msg=str(wrong)):
                credit.locate(self.store, "principal", self.terms("d0", series=wrong))
        for method in (None, "resampled-units", credit.SERIES_METHODS[0][:-1] + "2"):
            with self.assertRaisesRegex(ValueError, "names no estimation method this module implements", msg=repr(method)):
                credit.locate(self.store, "principal", self.terms("d0", series=dict(design, method=method)))
        # Terms an earlier writer accepted, which knew neither key: they are not judged by this module.
        with mock.patch.object(credit, "_check", lambda terms: None):
            older = self.contract("d4", receipt_protocol=None, differs=None, tolerated=None, series=design)
            wrong = self.contract("d5", max_pool_verdicts="1")
        self.refused(older, self.over(origins, (2, 1)), "read only under a receipt protocol", roster=roster)
        self.refused(wrong, self.pairs("wrong"), "max_pool_verdicts is a whole number")
        # Terms under the protocol without a series, and terms without a protocol at all.
        plain = self.contract("d1")
        pairs = self.pairs("plain")
        self.refused(plain, pairs, "read only when the terms state a series design", roster=roster)
        legacy = self.contract("d2", candidate="v2", receipt_protocol=None, differs=None, tolerated=None)
        old = []
        for index in range(4):
            origin = self.origin(f"legacy-{index}")
            old.append((self.side(f"legacy-{index}", origin, "v0", (0,)), self.side(f"legacy-{index}", origin, "v2", (0, 1, 2))))
        self.refused(legacy, old, "read only when the terms state a series design", roster=roster)
        self.assertEqual(credit.judge(self.store, legacy, old)["statement"]["unmet"], [])
        # A completed Defeat is read back without the roster, or with it, but not under another.
        strict = self.contract("d3", candidate="v3", series=design, min_effect=0.9)
        defeated = self.over(origins, (2, 1), candidate="v3")
        first = credit.judge(self.store, strict, defeated, reader=self.host.read, roster=roster)
        self.assertEqual(first["statement"]["unmet"], ["effect below the stated minimum"])
        self.assertEqual(credit.judge(self.store, strict, defeated)["statement"]["request"], first["statement"]["request"])
        self.assertEqual(credit.judge(self.store, strict, defeated, roster=roster)["statement"]["request"],
                         first["statement"]["request"])
        with self.assertRaisesRegex(ValueError, "no candidate to judge"):
            credit.judge(self.store, strict, defeated, roster=roster[::-1])

    def test_a_named_draw_is_never_ignored(self):
        origins, frame, design = self.population("plain")
        sampled = self.contract("d0", candidate="v3", sampling=design)
        reference, drawn = self.draw(sampled, frame, candidate="v3")
        # Terms under the protocol but without a design, and terms without a protocol at all.
        under_protocol = self.contract("d1")
        pairs = [(self.side(task, origin, "v0", (0,)), self.side(task, origin, "v1", (0, 1, 2)))
                 for task, origin in list(origins.items())[:4]]
        self.refused(under_protocol, pairs, "read only when the terms state a sampling design", sampling=reference)
        legacy = self.contract("d2", candidate="v2", receipt_protocol=None, differs=None, tolerated=None)
        old = []
        for index in range(4):
            origin = self.origin(f"legacy-{index}")
            old.append((self.side(f"legacy-{index}", origin, "v0", (0,)), self.side(f"legacy-{index}", origin, "v2", (0, 1, 2))))
        self.refused(legacy, old, "read only when the terms state a sampling design", sampling="unretained-unrequested-frame")
        self.assertEqual(credit.judge(self.store, legacy, old)["statement"]["unmet"], [])
        # A completed Defeat is read back without a reader, but not as the answer to another draw.
        strict = self.contract("d3", sampling=design, min_effect=0.9)
        reference3, drawn3 = self.draw(strict, frame)
        defeated = self.forks_of(origins, frame, drawn3)
        first = credit.judge(self.store, strict, defeated, reader=self.host.read, sampling=reference3)
        self.assertEqual(first["statement"]["unmet"], ["effect below the stated minimum"])
        events = len(kernel.events(self.store))
        self.assertEqual(credit.judge(self.store, strict, defeated)["statement"]["request"], first["statement"]["request"])
        self.assertEqual(credit.judge(self.store, strict, defeated, sampling=reference3)["statement"]["request"],
                         first["statement"]["request"])
        with self.assertRaisesRegex(ValueError, "no candidate to judge"):
            credit.judge(self.store, strict, defeated, sampling="object-another-draw")
        self.assertEqual(len(kernel.events(self.store)), events)
        for criterion, message in (
                ({"sampling": {"estimand": "best", "method": METHOD, "population": "p", "strata": {"a": 1.0}}}, "a sampling design states"),
                ({"sampling": {"estimand": "state_mean", "method": METHOD, "population": "p", "strata": {"a": 0}}}, "a sampling design states"),
                ({"sampling": {"estimand": "state_mean", "method": METHOD, "population": "p", "seed": "s", "strata": {"a": 1.0}}},
                 "a sampling design states"),
                ({"sampling": {"estimand": "state_mean", "population": "p", "strata": {"a": 1.0}}}, "names no estimation method"),
                ({"sampling": dict(design, method="bootstrap-of-drawn-units")}, "names no estimation method"),
                ({"sampling": design, "shared_executions": True}, "does not define: shared_executions"),
                ({"sampling": design, "above_noise": True}, "does not define: above_noise"),
                ({"sampling": design, "families_nonnegative": True}, "does not define: families_nonnegative"),
                ({"receipt_protocol": None, "differs": None, "tolerated": None, "sampling": design}, "read only under a receipt")):
            with self.assertRaisesRegex(ValueError, message, msg=str(criterion)):
                credit.locate(self.store, "principal", self.terms("bad sampling terms", **criterion))

    def test_under_a_measure_by_unit_a_state_is_measured_as_its_checkpoint_says(self):
        """Two dates, two questions each: the checkpoint names the date as the unit to count and the
        question as the unit to measure, and the journal and the measurement must agree with it."""
        dates = {"date-a": ["qa1", "qa2"], "date-b": ["qb1", "qb2"]}
        graded = {"pool": credit._digest("pool", "unseen dates"), "evaluator": self.sealed["evaluator"],
                  "environment": self.sealed["environment"], "scale": "score",
                  "units": {q: {"cases": credit.case_set([q + "-resolution"]), "total": 1} for names in dates.values() for q in names}}
        contract = self.contract("d1", measure=graded)

        def side(date, question, origin, version, score, journal=None, header=None):
            seq, parent = origin
            self.count += 1
            subject = credit._digest("state", [question, version, self.count])
            inputs, cost = {"version": self.versions[version]}, {"requests": 10}
            body = {"schema": PROTOCOL, "parent": fork.checkpoint(parent), "execution": f"x-{question}-{self.count}",
                    "allocation": f"a-{question}-{self.count}", "resolved_inputs": inputs, "restored": dict(parent["captured"]),
                    "omitted": {}, "closure": dict(CLOSED), "costs": cost, "result": subject}
            state = credit.state(self.store, kernel.identities(harness=version), date, subject, task=date, family="f",
                                 parent=seq, inputs=inputs, cost=cost, receipt=self.host.put("continuation_receipt", body),
                                 measurement_unit=journal or question)
            result = {"unit": question, "cases": {question + "-resolution": score}}
            named = header or question
            taken = {"schema": PROTOCOL, "subject": subject, "execution": f"g-{self.count}", "measure": credit.measure_id(graded),
                     "evaluator": graded["evaluator"], "environment": graded["environment"], "unit": named,
                     "cases": graded["units"][named]["cases"], "closure": dict(CLOSED), "admissible": True, "outcome": result}
            credit.measure(self.store, graded, subject, result, receipt=self.host.put("measurement_receipt", taken))
            return state

        pairs = []
        for date, names in dates.items():
            for question in names:
                origin = self.origin(date, "-" + question, measurement_unit=question)
                pairs.append((side(date, question, origin, "v0", 0.25), side(date, question, origin, "v1", 0.75)))
        origin = self.origin("date-a", "-header", measurement_unit="qa1")
        self.refused(contract, pairs + [(side("date-a", "qa1", origin, "v0", 0.25), side("date-a", "qa1", origin, "v1", 0.75, header="qa2"))],
                     "of another unit than the state is measured as")
        unnamed = self.origin("date-a", "-unnamed")                      # the checkpoint names no measurement unit
        self.refused(contract, pairs + [(side("date-a", "qa1", unnamed, "v0", 0.25), side("date-a", "qa1", unnamed, "v1", 0.75))],
                     "recorded as measured on another unit than its checkpoint names")
        statement = credit.judge(self.store, contract, pairs, reader=self.host.read)["statement"]
        self.assertEqual((statement["effect"]["tasks"], statement["effect"]["mean"], statement["unmet"]), (2, 0.5, []))
        self.assertEqual(len(statement["fork_receipts"]), 4)

    def test_null_forks_are_read_the_same_way(self):
        contract, pairs = self.contract("d1"), self.pairs("effect")

        def nulls(name, **second):
            made = []
            for index in range(4):
                origin = self.origin(f"{name}-{index}")
                made.append((self.side(f"{name}-{index}", origin, "v0", (0,)),
                             self.side(f"{name}-{index}", origin, "v0", (0,), **(second if index == 0 else {}))))
            return made

        self.refused(contract, pairs, "no continuation_receipt is retained as", null=nulls("legacy", reference=False))
        self.refused(contract, pairs, "of another subject", null=nulls("subject", measured={"subject": "another-subject"}))
        judged = credit.judge(self.store, contract, pairs, null=nulls("quiet"), reader=self.host.read)
        receipts = judged["statement"]["fork_receipts"]
        self.assertEqual(len(receipts), 8)
        self.assertEqual([row["differs"] for row in receipts[4:]], [{}] * 4)

    def test_terms_name_the_protocol_and_what_it_reads_before_any_candidate(self):
        for criterion, message in (
                ({"receipt_protocol": "operations-fork/v0"}, "unknown receipt protocol"),
                ({"differs": None}, "state differs as a list of names"),
                ({"tolerated": None}, "state tolerated as a list of names"),
                ({"differs": "version"}, "state differs as a list of names"),
                ({"tolerated": "context_other"}, "state tolerated as a list of names"),
                ({"differs": ["version", "version"]}, "state differs as a list of names"),
                ({"differs": ["strategy"]}, "include the version"),
                ({"shared_executions": "yes"}, "shared_executions is true or false"),
                ({"shared_executions": 0}, "shared_executions is true or false"),
                ({"shared_executions": 1}, "shared_executions is true or false"),
                ({"shared_executions": 0.0}, "shared_executions is true or false"),
                ({"shared_executions": 1.0}, "shared_executions is true or false"),
                ({"receipt_protocol": None, "differs": None, "tolerated": None, "shared_executions": True},
                 "read only under a receipt protocol"),
                ({"receipt_protocol": None}, "read only under a receipt protocol"),
                ({"receipt_protocol": None, "differs": None}, "read only under a receipt protocol")):
            with self.assertRaisesRegex(ValueError, message, msg=str(criterion)):
                credit.locate(self.store, "principal", self.terms("bad terms", **criterion))
        plain = self.terms("older terms", receipt_protocol=None, differs=None, tolerated=None)
        self.assertNotIn("receipt_protocol", plain["criterion"])
        credit.locate(self.store, "principal", plain)  # terms without a protocol stay as they were


if __name__ == "__main__":
    unittest.main()
