import unittest

from procontract_native_request_outcomes import reconcile


def request(identity, turn, *, terminal='response.completed', failure=None, thread='thread'):
    return {'id': identity, 'turn_id': turn, 'thread_id': thread, 'role': 'worker',
            'http_status': 200, 'terminal': terminal, 'failure': failure}


class RequestOutcomeControls(unittest.TestCase):
    def test_completed_main_does_not_recover_later_worker(self):
        result = reconcile([request(1, 'main'),
                            request(2, 'review', terminal=None, failure='IncompleteRead')])
        self.assertEqual(result['failed_requests_without_later_same_turn_success'], 1)
        self.assertFalse(result['failures'][0]['continuation_observed_in_same_turn'])

    def test_only_later_success_in_same_producer_turn_is_observed(self):
        failed = request(2, 'review', terminal=None, failure='BrokenPipeError')
        result = reconcile([request(1, 'review'), failed, request(3, 'main'),
                            request(4, 'review', thread='other'), request(5, 'review')])
        self.assertEqual(result['failures'][0]['later_successful_requests_same_turn'], [5])
        self.assertEqual(result['failed_requests_without_later_same_turn_success'], 0)
        self.assertEqual(len(result['failures']), 1)  # Failure history survives continuation.

    def test_missing_turn_identity_does_not_match_another_missing_identity(self):
        result = reconcile([request(1, None, terminal=None, failure='IncompleteRead'), request(2, None)])
        self.assertEqual(result['failed_requests_without_later_same_turn_success'], 1)

    def test_missing_producer_identity_does_not_establish_same_turn_continuation(self):
        result = reconcile([request(1, 'review', thread=None, terminal=None, failure='IncompleteRead'),
                            request(2, 'review', thread=None)])
        self.assertEqual(result['failed_requests_without_later_same_turn_success'], 1)

    def test_failed_response_does_not_count_as_success_for_an_earlier_failure(self):
        result = reconcile([request(1, 'review', terminal=None, failure='IncompleteRead'),
                            request(2, 'review', terminal='response.failed')])
        self.assertEqual(result['failed_requests_without_later_same_turn_success'], 2)

    def test_deadline_is_retained_separately_and_not_invented_as_transport_failure(self):
        result = reconcile([request(1, 'main'),
                            request(2, 'review', terminal=None, failure='task_deadline')])
        self.assertEqual(result['deadline_limited_requests'], [2])
        self.assertEqual(result['incomplete_terminal_receipts'], [2])
        self.assertEqual(result['failures'], [])

    def test_duplicate_request_identity_is_not_silently_merged(self):
        with self.assertRaises(ValueError):
            reconcile([request(1, 'main'), request(1, 'review')])


if __name__ == '__main__':
    unittest.main()
