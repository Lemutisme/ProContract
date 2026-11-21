"""Byte preservation controls, including escaped Unicode and legacy packets."""
import json
import unittest

import procontract_policy_splice as splice


def packet(policy,style='named',cut=5,ascii=False):
    text=policy.decode()+'\n\n'+json.dumps({'contract_id':'c','subject_hash':'s','available_actions':['rest'],
                                        'quoted_policy_is_fact':policy.decode()},ensure_ascii=ascii)
    fragments=[text[:cut],text[cut:]]
    if style=='named':
        items=[{'type':'function_call_output','id':f'id-{i}','namespace':'agent',
                'name':f'pro_contract_packet_{i:04}','output':part}for i,part in enumerate(fragments)]
    else:
        items=[{'type':'message','role':'user','content':[{'type':'input_text',
            'text':f'<external_pro_contract_packet_{i:04}>{part}</external_pro_contract_packet_{i:04}>'}]}for i,part in enumerate(fragments)]
    return json.dumps({'model':'frozen','input':items,'history':[policy.decode(),{'encrypted':'literal\\u0041'}],
        'tools':[{'name':'same'}],'schema':{'const':True},'number':100.0},ensure_ascii=ascii,indent=2).encode()+b'\n'


class SpliceTest(unittest.TestCase):
    def unchanged(self,before,after,receipt):
        a=b=0
        for edit in receipt['edits']:
            start,end=edit['wire'];new_start,new_end=edit['new_wire']
            self.assertEqual(before[a:start],after[b:new_start])
            self.assertEqual(splice.sha(before[start:end]),edit['before_sha256'])
            self.assertEqual(splice.sha(after[new_start:new_end]),edit['after_sha256'])
            a,b=end,new_end
        self.assertEqual(before[a:],after[b:])

    def test_identity_keeps_escape_spelling_whitespace_and_numbers(self):
        old='A😀/é\\\n"more policy'.encode()
        for style in ('named','external'):
            raw=packet(old,style,ascii=True).replace(b'A',b'\\u0041').replace(b'100.0',b'1e+02')
            # Replacement above is inside values (the fixture has no uppercase keys).
            output,receipt=splice.splice(raw,splice.sha(raw),old,old)
            self.assertEqual(output,raw);self.assertTrue(receipt['identity'])
            self.unchanged(raw,output,receipt)

    def test_only_policy_spans_change_for_long_short_and_empty_candidates(self):
        old=b'original policy with enough content'
        for style in ('named','external'):
            for new in ('new policy much longer than the old policy 😀'*3,'x',''):
                with self.subTest(style=style,new=new):
                    raw=packet(old,style,cut=10);output,receipt=splice.splice(raw,splice.sha(raw),old,new.encode())
                    self.unchanged(raw,output,receipt)
                    self.assertEqual(splice.locate(output,splice.sha(output),new.encode())['facts_sha256'],receipt['facts_sha256'])
                    self.assertEqual(json.loads(raw)['history'],json.loads(output)['history'])
                    self.assertEqual(json.loads(raw)['schema'],json.loads(output)['schema'])

    def test_unicode_escape_boundaries_are_whole_scalars(self):
        old='😀é\\"policy'.encode();new='🧠另一段\n\\'.encode()
        for ascii in (True,False):
            raw=packet(old,cut=1,ascii=ascii);output,receipt=splice.splice(raw,splice.sha(raw),old,new)
            self.unchanged(raw,output,receipt)
            self.assertEqual(splice.locate(output,splice.sha(output),new)['policy_sha256'],splice.sha(new))

    def test_wrong_source_policy_duplicate_keys_and_packet_ambiguity_refuse(self):
        old=b'long policy';raw=packet(old)
        with self.assertRaisesRegex(ValueError,'digest'):splice.splice(raw,'0'*64,old,b'new')
        with self.assertRaisesRegex(ValueError,'prefix'):splice.splice(raw,splice.sha(raw),b'long',b'new')
        duplicate=b'{"input":[],"input":[]}'
        with self.assertRaisesRegex(ValueError,'duplicate'):splice.splice(duplicate,splice.sha(duplicate),old,b'new')
        for change in ('duplicate','reorder','real_call','mix'):
            value=json.loads(raw)
            if change=='duplicate':value['input'].append(value['input'][0])
            elif change=='reorder':value['input'].reverse()
            elif change=='real_call':value['input'][0]['call_id']='real-tool-call'
            else:value['input']+=json.loads(packet(old,'external'))['input']
            bad=json.dumps(value).encode()
            with self.subTest(change=change),self.assertRaises(ValueError):splice.splice(bad,splice.sha(bad),old,b'new')


if __name__=='__main__':unittest.main()
