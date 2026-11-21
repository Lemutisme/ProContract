"""Crash recovery around paid learning launches, with only an offline fake runner."""

import json
import os
import tempfile
import unittest
from unittest import mock

import procontract_rsi as rsi
import test_procontract_rsi as fixtures


class LearningClaimTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.camp = fixtures.campaign(temporary.name)
        self.host = fixtures.host(self.camp, fixtures.FakeTasks())
        self.parent = self.host.incumbent()
        self.root = self.host.stage_research(self.parent, 1)
        self.claim = self.root / "launch-claim.json"
        self.summary = self.root / "run" / "run.json"
        self.calls = []
        self.launches = 0
        self.window = "before_spawn"
        self.original_run = rsi.subprocess.run
        environment = mock.patch.dict(os.environ, {"FAKE_RESEARCH_MODE": "method"})
        environment.start()
        self.addCleanup(environment.stop)
        runner = mock.patch.object(rsi.subprocess, "run", side_effect=self.runner)
        runner.start()
        self.addCleanup(runner.stop)

    def runner(self, args, **kwargs):
        if args[: len(fixtures.FAKE_RUNNER)] != fixtures.FAKE_RUNNER:
            return self.original_run(args, **kwargs)  # Keep the real SQLite store.
        phase = args[len(fixtures.FAKE_RUNNER)]
        self.calls.append(phase)
        if phase == "prepare":
            completed = self.original_run(args, **kwargs)
            if getattr(self, "fail_prepare_once", False):
                self.fail_prepare_once = False
                raise rsi.subprocess.CalledProcessError(7, args)
            return completed
        self.assertEqual(phase, "run")
        self.assertTrue(self.claim.exists(), "claim must precede paid process launch")
        if self.window == "before_spawn":
            raise InterruptedError("host stopped before spawning")
        self.launches += 1
        completed = self.original_run(args, **kwargs)
        if self.window == "nonzero":
            completed.returncode = 9
            return completed
        if self.window == "returned":
            return completed
        if self.window == "without_receipt":
            self.child_receipt = self.summary.read_bytes()
            self.summary.unlink()
        raise InterruptedError("host stopped before recording the child exit")

    def invoke_agent(self, instructions="research.md", deadline=None):
        return self.host.run_agent(
            self.root,
            "research-1",
            self.parent,
            instructions,
            self.host.terms["research_deadline_secs"] if deadline is None else deadline,
        )

    def restart_host(self):
        self.host = fixtures.host(self.camp, fixtures.FakeTasks())

    def test_claim_without_spawn_is_spent_unknown_and_never_reprepared(self):
        with self.assertRaises(InterruptedError):
            self.invoke_agent()
        claim = self.claim.read_bytes()
        prepared = self.root / "run" / "workspace" / "policy" / "research.md"
        content = prepared.read_bytes()
        self.restart_host()
        with self.assertRaisesRegex(RuntimeError, "unreconciled"):
            self.invoke_agent()
        self.assertEqual(
            (self.calls, self.launches, self.claim.read_bytes(), prepared.read_bytes()),
            (["prepare", "run"], 0, claim, content),
        )
        self.assertFalse((self.root / "runner-failed.json").exists())
        self.assertFalse((self.root / "launch-exit.json").exists())

    def test_new_exit_binds_the_original_json_across_sorted_claim_readback(self):
        self.window = "returned"
        delivery = self.invoke_agent()
        path = self.root / "launch-exit.json"
        raw = path.read_bytes(); exited = json.loads(raw)
        self.assertTrue(rsi.store.binding_matches(exited["claim_binding"], "learning_launch", json.loads(self.claim.read_text())))
        self.assertEqual(exited["claim"], exited["claim_binding"]["digest"])
        before = self.claim.read_bytes()
        self.restart_host()
        self.assertEqual(self.invoke_agent(), delivery)
        self.assertEqual((self.launches, self.calls, self.claim.read_bytes(), path.read_bytes()),
                         (1, ["prepare", "run"], before, raw))

    def test_invalid_legacy_exit_is_not_reinterpreted_or_relaunched(self):
        self.window = "returned"; self.invoke_agent()
        path = self.root / "launch-exit.json"
        old = {"claim": "0" * 64, "returncode": 0}
        path.write_text(json.dumps(old, sort_keys=True)); raw = path.read_bytes()
        self.restart_host()
        with self.assertRaisesRegex(RuntimeError, "invalid exit receipt"):
            self.invoke_agent()
        self.assertEqual((self.launches, self.calls, path.read_bytes()), (1, ["prepare", "run"], raw))

    def test_qualified_legacy_exit_keeps_its_original_read_rule(self):
        self.window = "returned"; delivery = self.invoke_agent()
        path = self.root / "launch-exit.json"
        old = {"claim": rsi.store.digest("learning_launch", json.loads(self.claim.read_text())), "returncode": 0}
        path.write_text(json.dumps(old, sort_keys=True)); raw = path.read_bytes()
        self.restart_host()
        self.assertEqual(self.invoke_agent(), delivery)
        self.assertEqual((self.launches, self.calls, path.read_bytes()), (1, ["prepare", "run"], raw))

    def test_bad_new_exit_binding_is_not_read_as_a_legacy_receipt(self):
        self.window = "returned"; self.invoke_agent()
        path = self.root / "launch-exit.json"
        exited = json.loads(path.read_text())
        exited["claim"] = rsi.store.digest("learning_launch", json.loads(self.claim.read_text()))
        exited["claim_binding"] = None
        path.write_text(json.dumps(exited, sort_keys=True)); raw = path.read_bytes()
        self.restart_host()
        with self.assertRaisesRegex(RuntimeError, "invalid exit receipt"):
            self.invoke_agent()
        self.assertEqual((self.launches, self.calls, path.read_bytes()), (1, ["prepare", "run"], raw))

    def test_started_child_without_receipt_cannot_qualify_stale_documents(self):
        self.window = "without_receipt"
        with self.assertRaises(InterruptedError):
            self.invoke_agent()
        experiment = self.root / "run" / "workspace" / "EXPERIMENT.md"
        content = experiment.read_bytes()
        self.restart_host()
        with mock.patch.object(self.host, "qualify") as qualify:
            with self.assertRaisesRegex(RuntimeError, "unreconciled"):
                delivery = self.invoke_agent()
                self.host.qualify(delivery, self.parent)
            qualify.assert_not_called()
        self.assertEqual(
            (self.calls, self.launches, experiment.read_bytes()),
            (["prepare", "run"], 1, content),
        )
        self.assertEqual(list(self.host.versions()), [self.parent])

    def test_terminal_child_receipt_recovers_without_host_exit_bookkeeping(self):
        self.window = "after_terminal"
        with self.assertRaises(InterruptedError):
            self.invoke_agent()
        self.assertFalse((self.root / "launch-exit.json").exists())
        receipt = self.summary.read_bytes()
        self.restart_host()
        delivery = self.invoke_agent()
        self.assertEqual(
            (delivery, self.calls, self.launches, self.summary.read_bytes()),
            (self.root / "run" / "workspace", ["prepare", "run"], 1, receipt),
        )
        self.assertTrue(self.host.qualify(delivery, self.parent)[0])

    def test_nonterminal_or_empty_receipt_cannot_reconcile_a_claim(self):
        with self.assertRaises(InterruptedError):
            self.invoke_agent()
        for summary in ({}, {"turn_statuses": []}, {"turn_statuses": ["running"]}):
            with self.subTest(summary=summary):
                self.summary.write_text(json.dumps(summary))
                self.restart_host()
                with self.assertRaisesRegex(RuntimeError, "unreconciled"):
                    self.invoke_agent()
        self.assertEqual((self.calls, self.launches), (["prepare", "run"], 0))

    def test_late_original_child_receipt_reconciles_the_same_spent_claim(self):
        self.window = "without_receipt"
        with self.assertRaises(InterruptedError):
            self.invoke_agent()
        claim = self.claim.read_bytes()
        self.restart_host()
        with self.assertRaisesRegex(RuntimeError, "unreconciled"):
            self.invoke_agent()
        self.summary.write_bytes(self.child_receipt)
        self.restart_host()
        delivery = self.invoke_agent()
        self.assertEqual(
            (delivery, self.calls, self.launches, self.claim.read_bytes()),
            (self.root / "run" / "workspace", ["prepare", "run"], 1, claim),
        )
        self.assertFalse((self.root / "runner-failed.json").exists())
        self.assertTrue(self.host.qualify(delivery, self.parent)[0])

    def test_partial_claim_refuses_relaunch_and_preserves_prepared_workspace(self):
        with self.assertRaises(InterruptedError):
            self.invoke_agent()
        prepared = self.root / "run" / "workspace" / "policy" / "research.md"
        content = prepared.read_bytes()
        partial = b'{"schema":1,"identity":'
        self.claim.write_bytes(partial)
        self.restart_host()
        with self.assertRaisesRegex(RuntimeError, "claim is unreadable"):
            self.invoke_agent()
        self.assertEqual(
            (self.calls, self.launches, self.claim.read_bytes(), prepared.read_bytes()),
            (["prepare", "run"], 0, partial, content),
        )

    def test_prompt_and_source_workspace_drift_cannot_reuse_the_claim(self):
        with self.assertRaises(InterruptedError):
            self.invoke_agent()
        for source in (
            self.root / "prompt.md",
            self.root / "workspace" / "policy" / "research.md",
        ):
            with self.subTest(source=source):
                original = source.read_bytes()
                source.write_bytes(original + b"Changed input.\n")
                self.restart_host()
                with self.assertRaisesRegex(RuntimeError, "inputs changed"):
                    self.invoke_agent()
                source.write_bytes(original)
        self.assertEqual((self.calls, self.launches), (["prepare", "run"], 0))

    def test_role_and_deadline_drift_cannot_reuse_the_claim(self):
        with self.assertRaises(InterruptedError):
            self.invoke_agent()
        for overrides in (
            {"instructions": "analyst.md"},
            {"deadline": self.host.terms["research_deadline_secs"] + 1},
        ):
            with self.subTest(overrides=overrides):
                self.restart_host()
                with self.assertRaisesRegex(RuntimeError, "inputs changed"):
                    self.invoke_agent(**overrides)
        self.assertEqual((self.calls, self.launches), (["prepare", "run"], 0))

    def test_known_nonzero_exit_is_failed_once_even_with_complete_documents(self):
        self.window = "nonzero"
        delivery = self.invoke_agent()
        failure = self.root / "runner-failed.json"
        self.assertEqual(
            json.loads(failure.read_text()),
            {"reason": "runner failed", "returncode": 9},
        )
        self.assertTrue((delivery / "EXPERIMENT.md").exists())
        self.assertTrue((self.root / "launch-exit.json").exists())
        self.restart_host()
        self.assertEqual(self.invoke_agent(), delivery)
        self.assertEqual((self.calls, self.launches), (["prepare", "run"], 1))

    def test_unclaimed_legacy_directory_is_preserved_without_launch(self):
        run = self.root / "run"
        run.mkdir()
        artifact = run / "legacy-output.txt"
        artifact.write_bytes(b"Existing work with unknown process state.\n")
        with self.assertRaisesRegex(
            RuntimeError, "unreconciled legacy learning directory"
        ):
            self.invoke_agent()
        self.assertEqual(
            (self.calls, self.launches, artifact.read_bytes()),
            ([], 0, b"Existing work with unknown process state.\n"),
        )
        self.assertFalse(self.claim.exists())

    def test_partial_prepare_failure_is_retryable_without_spending_two_launches(self):
        self.fail_prepare_once = True
        self.window = "nonzero"
        with self.assertRaises(rsi.subprocess.CalledProcessError):
            self.invoke_agent()
        preparation = self.root / "prepare-only.json"
        self.assertTrue(preparation.exists())
        self.assertTrue((self.root / "run" / "workspace").is_dir())
        self.assertFalse(self.claim.exists())
        self.assertEqual((self.calls, self.launches), (["prepare"], 0))
        self.restart_host()
        delivery = self.invoke_agent()
        self.assertFalse(preparation.exists())
        self.assertTrue(self.claim.exists())
        self.restart_host()
        self.assertEqual(self.invoke_agent(), delivery)
        self.assertEqual(
            (self.calls, self.launches), (["prepare", "prepare", "run"], 1)
        )


if __name__ == "__main__":
    unittest.main()
