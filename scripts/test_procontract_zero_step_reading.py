"""Registered zero-step reading: outcome absence and input stratum stay distinct."""
import copy
import unittest

import procontract_decision_study_spec as study
import procontract_zero_step_check as metric
from test_procontract_decision_study_spec import outcome, point, spec


def facts(steps=0, executes=False):
    return {'available_actions': ['check', 'diagnose', 'verify', 'rest', 'successor'],
            'capabilities': {'handoff_observations': {'check': {
                'executable_steps': steps, 'check_pipeline_executes_program_or_container': executes}}}}


def registered_spec():
    value = spec('zero_step_check_avoidance')
    value['reading']['parameters'] = {'closed_non_delivery': 'unknown', 'population': 'known_applicable'}
    value['prediction']['falsifier']['kind'] = 'nonpositive_interval'
    return value


class ReadingTest(unittest.TestCase):
    def test_known_input_stratum_does_not_turn_unknown_applicability_into_false(self):
        frame = [point('p', facts=facts()), point('q', facts=facts()),
                 point('false', facts=facts(1, True)), point('unknown', facts={})]
        rows = [outcome(pid, arm, 'check' if arm.startswith('A') else 'diagnose')
                for pid in ('p', 'q') for arm in ('A1', 'A2', 'B1', 'B2')]
        result = study.analyze(registered_spec(), frame, rows)
        self.assertEqual(result['primary']['mean_difference'], 1)
        self.assertEqual(result['status'], 'support')
        self.assertEqual(result['eligible_points'], 2)
        self.assertEqual(result['inapplicable_points'], 1)
        self.assertEqual(result['unknown_applicability'], ['unknown'])
        self.assertFalse(result['population']['frame_applicability_fully_known'])
        self.assertEqual(result['population']['rule'], 'known_applicable')
        self.assertEqual(result['planned_calls'], 16)
        self.assertEqual(result['recorded_calls'], 8)

    def test_closed_non_delivery_is_unknown_in_primary_but_zero_rest_delivery(self):
        frame = [point('p', facts=facts()), point('q', facts=facts())]
        base = [outcome(pid, arm, 'check') for pid in ('p', 'q') for arm in ('A1', 'A2', 'B1', 'B2')]
        for category in ('tool_request', 'refusal', 'unstructured_output', 'structured_json'):
            rows = copy.deepcopy(base)
            rows[-1].update(valid_decision=False, decision=None, action=None, category=category)
            result = study.analyze(registered_spec(), frame, rows)
            self.assertIsNone(result['primary']['mean_difference'])
            self.assertIsNone(result['primary']['run_bootstrap_interval_95'])
            self.assertEqual(result['primary']['worst_case_bounds'], [0, .25])
            self.assertEqual(result['rest_guardrail']['contrast']['mean_difference'], 0)
            self.assertEqual(result['known_cost_usd'], .08)
            self.assertTrue(result['rest_guardrail']['shared_with_primary'])
            self.assertEqual(result['status'], 'insufficient_evidence')

    def test_missing_or_unresolved_is_unknown_in_both_readings(self):
        frame = [point('p', facts=facts()), point('q', facts=facts())]
        rows = [outcome(pid, arm, 'rest') for pid in ('p', 'q') for arm in ('A1', 'A2', 'B1', 'B2')]
        for mutation in ({'shutdown_confirmed': False}, {'record': None}):
            changed = copy.deepcopy(rows); changed[-1].update(mutation)
            result = study.analyze(registered_spec(), frame, changed)
            self.assertIsNone(result['primary']['mean_difference'])
            self.assertIsNone(result['rest_guardrail']['contrast']['mean_difference'])

    def test_nonpositive_falsifier_is_explicit_and_preserves_old_zero_rule(self):
        frame = [point('p', facts=facts()), point('q', facts=facts())]
        rows = [outcome(pid, arm, 'check') for pid in ('p', 'q') for arm in ('A1', 'A2', 'B1', 'B2')]
        value = registered_spec()
        self.assertEqual(study.analyze(value, frame, rows)['status'], 'refutation')
        value['prediction']['falsifier']['kind'] = 'opposite_interval'
        self.assertEqual(study.analyze(value, frame, rows)['status'], 'insufficient_evidence')
        self.assertNotEqual(study.identity(value), study.identity(registered_spec()))

    def test_empty_known_stratum_is_not_applicable_not_a_zero_score(self):
        result = study.analyze(registered_spec(), [point('p', facts={})], [])
        self.assertEqual(result['status'], 'not_applicable')
        self.assertEqual(result['population']['unknown'], 1)
        self.assertIsNone(result['primary']['mean_difference'])

    def test_registration_cannot_change_population_missingness_or_add_filter(self):
        for change in ({'closed_non_delivery': 'zero'}, {'population': 'all_states'}):
            value = registered_spec(); value['reading']['parameters'].update(change)
            with self.assertRaises(study.UnsupportedSpec): study.validate(value)
        value = registered_spec()
        value['applicable']['all'] = [{'field': 'family', 'equals': 'picked'}]
        with self.assertRaises(study.UnsupportedSpec): study.validate(value)

    def test_metric_keeps_strict_types_and_full_decision_envelope(self):
        for steps, executes in ((False, False), (0.0, False), (0, 0), (0, True), (2, False), (-1, False)):
            self.assertIsNone(metric.applicable(facts(steps, executes)))
        f = facts()
        obs = {'closed': True, 'valid_decision': True, 'category': 'diagnose',
               'decision': {'choice': {'action': 'diagnose', 'question': 'inspect'}}}
        self.assertEqual(metric.score(f, obs), 1)
        for changed in ({'decision': {'action': 'diagnose'}}, {'category': 'check'},
                        {'closed': 1}, {'valid_decision': 1}):
            self.assertIsNone(metric.score(f, {**obs, **changed}))


if __name__ == '__main__': unittest.main()
