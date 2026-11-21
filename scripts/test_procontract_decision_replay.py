"""Operations-to-wire controls. REPLAY_PROVIDER_FIXTURE names the owner's fake_provider.py.

These use the actual registered runner subprocess, never a replacement HTTP client.
REPLAY_TEST_ARTIFACTS optionally retains all isolated hosts and raw wire evidence.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import procontract_decision_replay as replay
import procontract_operations as ops


class ReplayTest(unittest.TestCase):
    def setUp(self):
        fixture = os.environ.get("REPLAY_PROVIDER_FIXTURE")
        if not fixture:
            self.skipTest("set REPLAY_PROVIDER_FIXTURE to the owner's loopback provider")
        spec = importlib.util.spec_from_file_location("replay_fixture", fixture)
        self.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixture)
        retained = os.environ.get("REPLAY_TEST_ARTIFACTS")
        if retained:
            self.root = Path(retained) / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)
        self.server, self.thread = self.fixture.start(self.root / "provider")
        self.addCleanup(self.stop_provider)
        self.payload = (b'{ "model": "gpt-6-luna", "reasoning":{"effort":"max"},'
                        b' "stream":true, "input":[{"role":"user","content":"old snapshot 37"}],'
                        b' "tools":[{"type":"function","name":"inspect",'
                        b'"parameters":{"type":"object","properties":{}}}],'
                        b' "text":{"format":{"type":"json_object"}},'
                        b' "max_output_tokens":1234 }\n')
        self.payload_path = self.root / "payload.json"
        self.payload_path.write_bytes(self.payload)
        self.counter = 0

    def stop_provider(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def make_host(self, *, parents=("parent-one",), auth_env=None, path=None):
        self.counter += 1
        location = self.root / f"fixture-{self.counter}"
        location.mkdir()
        checkpoints = [replay.checkpoint(path or self.payload_path, parent,
            {"id": "old-task", "family": "qualification"}, "synthetic", [],
            {"solver": "single request only; no solver restored"}) for parent in parents]
        profile_path = location / "profile.json"
        ops.atomic(profile_path, replay.profile(location / "host", self.server.endpoint, checkpoints,
                                               auth_env=auth_env))
        authority = ops.Authority("fixture-owner", "zero-model-qualification", "request-only", (),
                                  ("replay",), allow_adoption=False, operation_limit=8)
        host = ops.Operations.create(location / "host", authority, {
            "replay": ops.Measurement((sys.executable, "-B", str(Path(replay.__file__).resolve()),
                                       "--profile", str(profile_path)), replay.implementation(),
                                      "loopback", external_effects=True)})
        host.subscribe("research", "task", ["operation_outcome"])
        version = host.register({"kind": "harness", "files": {}, "build": {"source": replay.implementation()},
                                 "model": {"name": "gpt-6-luna", "effort": "max"},
                                 "resolver": "retained-request/v1", "tools": {}})
        return host, version, checkpoints, profile_path

    def complete(self, host, version, cp, name="one"):
        host.submit(name, "research", version, "replay", {"checkpoint": cp["id"]}, "wire qualification")
        host.run_worker(name)
        return host.inspect(name)

    def test_identical_body_new_assignments_raw_custody_and_duplicate_delivery(self):
        with patch.dict(os.environ, {"REPLAY_TEST_TOKEN": "synthetic-secret-not-for-logs"}):
            host, version, points, _ = self.make_host(auth_env="REPLAY_TEST_TOKEN")
            left = self.complete(host, version, points[0], "left")
            right = self.complete(host, version, points[0], "right")
        a, b = (x["outcome"]["measurement"] for x in (left, right))
        self.assertEqual([Path(r["body_file"]).read_bytes() for r in self.server.records], [self.payload]*2)
        self.assertEqual(len({r["headers"]["X-Execution-ID"] for r in self.server.records}), 2)
        self.assertEqual(len({r["headers"]["X-Allocation-ID"] for r in self.server.records}), 2)
        for item, result in ((left, a), (right, b)):
            self.assertTrue(item["outcome"]["shutdown_confirmed"])
            self.assertFalse(result["admissible"])
            self.assertEqual(result["allocation"], ops.sha(item["request"]))
            self.assertEqual(result["category"], "structured_json")
            kept = host.get(result["payload"], "decision_request_bytes")
            self.assertEqual(base64.b64decode(kept["body"]), self.payload)
            trace = host.get(result["trace"], "decision_replay_trace")
            self.assertEqual(trace["assignment"], result["allocation"])
            raw = host.get(trace["files"]["response.raw"], "decision_replay_raw")
            self.assertIn(b"response.completed", base64.b64decode(raw["body"]))
            for file in (host.operation_dir(result["execution"])).rglob("*"):
                if file.is_file():
                    self.assertNotIn(b"synthetic-secret-not-for-logs", file.read_bytes())
        self.assertEqual(a["observation"], b["observation"])
        self.assertNotEqual(a["trace"], b["trace"])
        self.assertFalse(host.authority.allow_adoption)
        self.assertEqual(host.dispatch("left"), left)
        host.run_worker("left")
        self.assertEqual(len(self.server.records), 2)

    def test_error_and_disconnect_retain_unknown_remote_without_retry(self):
        self.server.modes = ("http_error", "disconnect")
        host, version, points, _ = self.make_host()
        error = self.complete(host, version, points[0], "error")
        lost = self.complete(host, version, points[0], "lost")
        a, b = (x["outcome"]["measurement"] for x in (error, lost))
        self.assertTrue(error["outcome"]["shutdown_confirmed"])
        self.assertFalse(lost["outcome"]["shutdown_confirmed"])
        self.assertIsNone(a["usage"])
        self.assertIsNone(b["usage"])
        trace = host.get(a["trace"], "decision_replay_trace")
        raw = host.get(trace["files"]["response.raw"], "decision_replay_raw")
        self.assertIn(b"rate_limit_error", base64.b64decode(raw["body"]))
        host.dispatch("error"); host.dispatch("lost")
        self.assertEqual(len(self.server.records), 2)

    def test_preflight_rejections_do_not_send_or_leave_unresolved_effects(self):
        cases = [("changed_bytes", None), ("missing_auth", None),
                 ("provider_state", {"previous_response_id": "old-remote-id"}),
                 ("hosted_tool", {"tools": [{"type": "web_search"}]})]
        for name, change in cases:
            with self.subTest(name=name):
                path = self.root / f"{name}.json"
                body = json.loads(self.payload)
                body.update(change or {})
                path.write_text(json.dumps(body))
                host, version, points, _ = self.make_host(path=path,
                    auth_env="ABSENT_REPLAY_CREDENTIAL" if name == "missing_auth" else None)
                if name == "changed_bytes":
                    path.write_bytes(path.read_bytes()+b" ")
                with patch.dict(os.environ):
                    os.environ.pop("ABSENT_REPLAY_CREDENTIAL", None)
                    done = self.complete(host, version, points[0], name)
                result = done["outcome"]["measurement"]
                self.assertTrue(done["outcome"]["shutdown_confirmed"])
                self.assertEqual(result["category"], "preflight_rejected")
                self.assertEqual(result["counts"]["reserved_http_attempts"], 0)
                self.assertFalse((host.operation_dir(name)/"dispatch-intent.json").exists())
        self.assertEqual(self.server.records, [])

    def test_existing_dispatch_intent_is_spent_even_without_acknowledgement(self):
        host, version, points, _ = self.make_host()
        directory = host.operation_dir("uncertain")
        directory.mkdir(parents=True)
        (directory / "dispatch-intent.json").write_bytes(b'{"partial":')
        done = self.complete(host, version, points[0], "uncertain")
        self.assertEqual(done["outcome"]["measurement"]["category"], "dispatch_uncertain")
        self.assertFalse(done["outcome"]["shutdown_confirmed"])
        host.dispatch("uncertain")
        self.assertEqual(self.server.records, [])

    def test_lost_operation_acknowledgement_reconciles_without_another_post(self):
        host, version, points, _ = self.make_host()
        host.submit("lost-ack", "research", version, "replay", {"checkpoint": points[0]["id"]}, "test")
        with patch.object(host, "reconcile", side_effect=RuntimeError("lost acknowledgement")):
            with self.assertRaises(RuntimeError):
                host.run_worker("lost-ack")
        done = host.reconcile("lost-ack")
        self.assertTrue(done["outcome"]["shutdown_confirmed"])
        host.dispatch("lost-ack")
        self.assertEqual(len(self.server.records), 1)

    def test_same_decision_cannot_reuse_another_parent_identity(self):
        host, version, points, _ = self.make_host(parents=("parent-a", "parent-b"))
        left = self.complete(host, version, points[0], "left")["outcome"]["measurement"]
        right = self.complete(host, version, points[1], "right")["outcome"]["measurement"]
        self.assertNotEqual(left["observation"], right["observation"])
        self.assertEqual(host.get(left["observation"])["output"], host.get(right["observation"])["output"])

    def test_profile_drift_is_rejected_by_actual_operations_worker(self):
        host, version, points, profile = self.make_host()
        profile.write_bytes(profile.read_bytes()+b" ")
        done = self.complete(host, version, points[0])
        self.assertEqual(done["outcome"]["execution"], "configuration_changed")
        self.assertTrue(done["outcome"]["shutdown_confirmed"])
        self.assertIsNone(done["outcome"]["measurement"])
        self.assertEqual(self.server.records, [])

    def test_output_categories_retain_tool_calls_refusals_and_complete_decisions(self):
        tool = {"type": "function_call", "name": "do_not_run", "arguments": "{}", "call_id": "c"}
        self.assertEqual(replay.observation({"output": [tool]}),
                         {"category": "tool_request", "decision": None, "calls": [tool]})
        content = lambda part: {"output": [{"type": "message", "content": [part]}]}
        self.assertEqual(replay.observation(content({"type": "refusal", "refusal": "no"}))["category"], "refusal")
        self.assertEqual(replay.observation(content({"type": "output_text", "text": "not JSON"}))["category"],
                         "unstructured_output")
        decision = {"choice": {"action": "successor", "next_step": "preserve full text", "scopes": ["x"]}}
        self.assertEqual(replay.observation(content({"type": "output_text", "text": json.dumps(decision)}))["decision"], decision)


if __name__ == "__main__":
    unittest.main()
