"""Actual native digest/Issue controls; temporary stores, zero model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import procontract_operations as ops
import procontract_store as kernel


class TermsBindingControls(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.host=ops.Operations.create(self.root/'host',ops.Authority('owner','fixture','test',(),()),{})
        self.terms={'z_scope':'retain 原始 evidence','a_conditions':{'z':2,'a':1},'sequence':['first','second']}

    def reordered(self):
        path=self.root/'terms.json';ops.atomic(path,self.terms)
        return json.loads(path.read_text())

    def bindings(self):
        with self.host.tx() as db:
            rows=db.execute("SELECT id FROM objects WHERE kind='issued_terms' ORDER BY rowid").fetchall()
        return [self.host.get(r['id'],'issued_terms') for r in rows]

    def event_count(self):
        # These are test-owned scratch databases, not historical originals.
        import sqlite3
        with sqlite3.connect(self.host.store/'ledger_1.sqlite') as db:
            return db.execute('SELECT count(*) FROM events').fetchone()[0]

    def legacy_issue(self):
        return kernel.apply(self.host.store,'legacy:issue',dict(contract_id='task',expected_version=0,
            role='issuer',provenance='delegate',command=dict(type='issue',owner='owner',bindings=dict(
                terms_hash=kernel.digest('terms',self.terms),capture_policy_hash=kernel.digest('capture_policy','version_v2'),
                evidence_policy_hash=kernel.digest('evidence_policy',self.host.config)))))

    def test_actual_native_order_difference_is_preserved_not_rehashed(self):
        reordered=self.reordered()
        old=kernel.digest('terms',self.terms);sorted_hash=kernel.digest('terms',reordered)
        self.assertNotEqual(old,sorted_hash,'This regression control requires the original order-preserving native binary')
        state=self.host.issue('task',self.terms)
        self.assertEqual(state['bindings']['terms_hash'],old)
        receipt=self.bindings()[0]['binding']
        self.assertEqual(kernel.digest_input('terms',receipt['input_json']),old)
        self.assertNotEqual(state['bindings']['terms_hash'],sorted_hash)

    def test_restart_and_sorted_roundtrip_preserve_original_contract(self):
        first=self.host.issue('task',self.terms)
        self.host=ops.Operations(self.host.root)
        self.assertEqual(self.host.issue('task',self.reordered()),first)
        self.assertEqual(self.event_count(),1)
        self.assertEqual(len(self.bindings()),1)

    def test_value_array_and_numeric_kind_changes_are_not_reformats(self):
        first=self.host.issue('task',self.terms)
        for changed in [dict(self.terms,z_scope='other'),dict(self.terms,sequence=['second','first']),
                        dict(self.terms,a_conditions={'z':2,'a':1.0})]:
            with self.assertRaisesRegex(ValueError,'different terms'):
                self.host.issue('task',changed)
        self.assertEqual(kernel.contract(self.host.store,'task'),first)
        self.assertEqual(self.event_count(),1)

    def test_prepared_binding_recovers_before_kernel_issue(self):
        with patch.object(kernel,'apply',side_effect=RuntimeError('before Issue')):
            with self.assertRaisesRegex(RuntimeError,'before Issue'):self.host.issue('task',self.terms)
        self.assertIsNone(kernel.contract(self.host.store,'task'))
        state=ops.Operations(self.host.root).issue('task',self.reordered())
        self.assertEqual(state['bindings']['terms_hash'],kernel.digest('terms',self.terms))
        self.assertEqual(self.event_count(),1)

    def test_lost_issue_ack_does_not_repeat_the_transition(self):
        apply=kernel.apply
        def lose(*args,**kwargs):
            apply(*args,**kwargs);raise RuntimeError('ack lost')
        with patch.object(kernel,'apply',side_effect=lose):
            with self.assertRaisesRegex(RuntimeError,'ack lost'):self.host.issue('task',self.terms)
        state=ops.Operations(self.host.root).issue('task',self.reordered())
        self.assertEqual(state['version'],1);self.assertEqual(self.event_count(),1)

    def test_legacy_backfill_requires_an_input_matching_original_hash(self):
        first=self.legacy_issue()
        with self.assertRaisesRegex(ValueError,'legacy digest preimage'):
            self.host.issue('task',self.reordered())
        self.assertEqual(self.bindings(),[])
        self.assertEqual(self.host.issue('task',self.terms),first)
        self.assertEqual(self.host.issue('task',self.reordered()),first)
        self.assertEqual(self.event_count(),1)

    def test_invalid_binding_codec_domain_and_content_are_rejected(self):
        receipt=kernel.json_binding('terms',self.terms)
        for changed in [dict(receipt,codec='unknown'),dict(receipt,domain='other'),
                        dict(receipt,input_json='{}'),dict(receipt,digest='0'*64)]:
            with self.assertRaises(ValueError):kernel.binding_matches(changed,'terms',self.terms)

    def test_retaining_terms_does_not_emit_an_event_or_select_work(self):
        self.host.subscribe('observer','*',['issued_terms'])
        before=self.host.inbox('observer')
        self.host.issue('task',self.terms)
        self.assertEqual(self.host.inbox('observer'),before)
        self.assertEqual(self.host.read()['events'],[])
        with self.host.tx() as db:self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0],0)

    def test_existing_issue_readback_after_action_deadline_does_not_renew_it(self):
        import time
        deadline=time.time()+60
        self.host=ops.Operations.create(self.root/'bounded-host',ops.Authority('owner','fixture','test',(),(),deadline=deadline),{})
        first=self.host.issue('task',self.terms)
        with patch.object(ops.time,'time',return_value=deadline+1):
            self.assertEqual(self.host.issue('task',self.reordered()),first)
        self.assertEqual(self.host.authority.deadline,deadline)
        self.assertEqual(self.event_count(),1)


if __name__=='__main__':unittest.main()
