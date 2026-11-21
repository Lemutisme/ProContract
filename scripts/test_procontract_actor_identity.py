import unittest

from procontract_actor_identity import ActorIndex, native_context_packet, native_contexts, ordered_worker_packet


class IdentityTests(unittest.TestCase):
    def test_inline_context_and_transport_guidance_remain_separate(self):
        def inline(text, role='user'):
            return {'type': 'message', 'role': role, 'content': [{'type': 'input_text',
                'text': '<codex_internal_context source="pro_contract_worker">\n'+text+'\n</codex_internal_context>'}]}
        body = '\nActual policy.\n\n'
        self.assertEqual(native_context_packet([inline(body)]), body)
        self.assertIsNone(native_context_packet([inline(body, 'assistant')]))
        self.assertIsNone(native_context_packet([inline(body), inline('another')]))
        fragment = {'type': 'function_call_output', 'namespace': 'agent', 'name': 'pro_contract_packet_0000', 'output': 'actual packet'}
        combined = [inline('Read the separate fragments.'), fragment]
        self.assertEqual(native_context_packet(combined), 'actual packet')
        contexts = native_contexts(combined)
        self.assertEqual([c['text'] for c in contexts], ['actual packet', 'Read the separate fragments.'])
        self.assertEqual(contexts[0]['records'][0]['item_index'], 1)
        self.assertEqual(contexts[1]['records'][0]['content_index'], 0)
        self.assertIsNone(native_context_packet([combined[0], {**fragment, 'name': 'pro_contract_packet_0001'}]))

    def test_current_named_context_and_legacy_fragments_preserve_the_same_packet(self):
        current = [{"type": "function_call_output", "namespace": "agent", "name": f"pro_contract_packet_{i:04}",
                    "output": value} for i, value in enumerate(["Choose useful ", "work.\n{}"])]
        legacy = [{"type": "message", "role": "user", "content": [{"type": "input_text",
                   "text": f'<external_{item["name"]}>{item["output"]}</external_{item["name"]}>'}]} for item in current]
        self.assertEqual(native_context_packet(current), "Choose useful work.\n{}")
        self.assertEqual(native_context_packet(reversed(current)), native_context_packet(legacy))
        self.assertIsNone(native_context_packet(current[1:]))
        self.assertIsNone(native_context_packet(current + current[:1]))
        self.assertIsNone(native_context_packet([{**item, "call_id": "ordinary-tool"} for item in current]))
        self.assertIsNone(native_context_packet([{**item, "namespace": "functions"} for item in current]))
        self.assertIsNone(native_context_packet([{**item, "role": "assistant"} for item in legacy]))

    def test_session_ancestry_cannot_overwrite_root_or_successor(self):
        for order in (("root", "child"), ("child", "root")):
            index = ActorIndex()
            for thread in order:
                index.add(thread, "root", {"thread": thread})
            for thread in order:
                self.assertEqual(index.resolve({"thread-id": thread, "session-id": "root"}),
                                 ({"thread": thread}, "exact_thread"))
            self.assertEqual(index.resolve({"session-id": "root"}), (None, "ambiguous_session"))

    def test_explicit_unknown_thread_never_falls_back_to_parent(self):
        index = ActorIndex()
        index.add("root", "session", {"role": "executor"})
        self.assertEqual(index.resolve({"thread-id": "unretained", "session-id": "session"}),
                         (None, "unknown_thread"))
        self.assertEqual(index.resolve({"session-id": "session"}),
                         ({"role": "executor"}, "unique_session"))
        self.assertEqual(index.resolve({}), (None, "unknown_session"))

    def test_conflicting_records_are_not_resolved_by_scan_order(self):
        index = ActorIndex()
        index.add("root", "session", {"role": "executor"})
        index.add("root", "session", {"role": "executor"})
        with self.assertRaises(ValueError):
            index.add("root", "session", {"role": "reviewer"})

    def test_policy_split_across_native_parts_is_reassembled_exactly(self):
        parts = ["<external_pro_contract_packet_0000>Choose useful </external_pro_contract_packet_0000>",
                 "<external_pro_contract_packet_0001>work.\n\n{}</external_pro_contract_packet_0001>"]
        self.assertEqual(ordered_worker_packet(reversed(parts)), "Choose useful work.\n\n{}")
        self.assertIsNone(ordered_worker_packet(parts[1:]))
        self.assertIsNone(ordered_worker_packet(parts + parts[:1]))
        self.assertIsNone(ordered_worker_packet(["quoted " + parts[0]]))


if __name__ == "__main__":
    unittest.main()
