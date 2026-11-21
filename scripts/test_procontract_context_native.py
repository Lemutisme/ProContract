"""Exercise actual native reset, durable state and public evidence with no model spending."""
import json
import os
from pathlib import Path
import sqlite3
import unittest

import test_procodex_native as fixture


@unittest.skipUnless(os.environ.get("PROCODEX_NATIVE_PACKAGE"), "set native package for local integration")
class ContextWindowTests(unittest.TestCase):
    def run_reset(self, task_seconds):
        seen = []
        sentinel = "context-local-observation-7ad31"
        before = []

        def admitted_state():
            path = case.native_home / "pro_contract/ledger_1.sqlite"
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                return {
                    "contracts": db.execute("select id,version,state_json from contracts order by id").fetchall(),
                    "grant": db.execute("select json from records where kind='succession' order by key").fetchall(),
                }

        def executor(body, stage):
            seen.append(body)
            if stage == 1:
                self.assertIn("Keep working context proportional to the next decision.", "\n".join(fixture.text_values(body)))
                code = 'text(await tools.exec_command(' + json.dumps({
                    "cmd": "printf 'ready\\n' > answer.txt; printf '" + sentinel + "\\n' > work-state.txt; cat work-state.txt",
                    "yield_time_ms": 1000, "max_output_tokens": 1000,
                }) + '));text(await tools.publish_progress({request_id:"before-context-switch"}));'
                return [fixture.custom_call(body, code, "save-and-observe")]
            if stage == 2:
                before.append(admitted_state())
                self.assertTrue(before[0]["contracts"])
                self.assertIn(sentinel, "\n".join(fixture.text_values(body.get("input", []))))
                return [fixture.named_call(body, "new_context", {}, "choose-new-context")]
            if stage == 3:
                # Native history was actually replaced; no hidden transcript replay.
                self.assertNotIn(sentinel, "\n".join(fixture.text_values(body.get("input", []))))
                self.assertIn("Keep working context proportional to the next decision.", "\n".join(fixture.text_values(body)))
                self.assertEqual(admitted_state(), before[0], "context switch changed the task or grant")
                return [fixture.custom_call(body,
                    'text(await tools.exec_command({cmd:"cat work-state.txt; cat answer.txt",yield_time_ms:1000,max_output_tokens:1000}));'
                    'text(await tools.contract_read_evidence({op:"index",offset:0}));', "recover-state")]
            if stage == 4:
                text = "\n".join(fixture.text_values(body.get("input", [])))
                self.assertIn(sentinel, text)
                self.assertIn("progress", text)
                return "Fixture completed."
            raise AssertionError(f"unexpected executor stage {stage}")

        case = fixture.NativeEvidenceTest("test_real_executor_and_reviewer_use_frozen_handles")
        case.executor_model = executor
        case.context_windows_fixture = True
        case.strategy_fixture_path = Path(__file__).resolve().parent.parent / "codex-rs/ext/pro-contract/policies"
        case.task_seconds = task_seconds
        case.debug()
        self.assertEqual(case.native_commands, ["issue", "propose", "support"])
        self.assertEqual(case.native_counts["executor"], 4)
        self.assertNotIn("successor", case.native_counts)
        root_keys = {b.get("prompt_cache_key") for b in seen}
        self.assertEqual(len(root_keys), 1, "context switch must not create a new actor")
        self.assertEqual(len([e for e in case.native_events if e["body"].get("phase") == "progress_assessed"]), 1)

    def test_open_ended_task_recovers_state_after_native_context_switch(self):
        self.run_reset(None)

    def test_finite_task_recovers_state_without_a_new_executor(self):
        self.run_reset(120)


if __name__ == "__main__":
    unittest.main()
