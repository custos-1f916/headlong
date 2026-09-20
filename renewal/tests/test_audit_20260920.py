import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_memory as cm
import custos_evidence as ce
import custos_review_scratch as cr
from test_memory import MemoryFixture

SUCCESS = {'phase': 'submitted', 'receipt': {'timestamp': 123, 'results': [{'type': 'SUCCESS'}]}}

class DeliveryTests(MemoryFixture):
    def test_pending_completion_parks_goal_and_suppression_keeps_it_actionable(self):
        goal = self.store.capture(self.payload, 'trigger', deferred=True)['goal_id']
        payload = {'goal_id': goal, 'disposition': 'completed', 'evidence': 'Sent it'}
        with patch.object(cm, 'unresolved_deliveries', return_value=[('outbox:recorded-step', 'pending')]):
            with self.assertRaises(cm.MemoryError): self.store.complete(payload)
        record = self.store.find(goal)[4]
        self.assertEqual(record['status'], 'active')
        self.assertEqual(record['waiting']['receipt'], 'outbox:recorded-step')
        with patch.object(cm, 'delivery_status', return_value={'phase': 'suppressed'}):
            context = self.store.context()
        self.assertEqual(len(context['waiting']), 0)
        self.assertIn('suppressed', self.store.find(goal)[4]['events'][-1]['resumed'])
        with patch.object(cm, 'unresolved_deliveries', return_value=[('outbox:recorded-step', 'suppressed')]):
            with self.assertRaises(cm.MemoryError): self.store.complete(payload)
        with patch.object(cm, 'unresolved_deliveries', return_value=[]):
            self.assertEqual(self.store.complete(payload)['status'], 'completed')

    def test_unchanged_wait_does_not_reopen_or_reset_deadline(self):
        goal = self.store.capture(self.payload, 'trigger', deferred=True)['goal_id']
        self.store.wait({'goal_id': goal, 'reason': 'Await receipt', 'receipt': 'outbox:step'})
        before = self.store.find(goal)[4]['waiting']
        with patch.object(cm, 'delivery_status', return_value={'phase': 'pending'}):
            for _ in range(3): self.assertEqual(len(self.store.context()['waiting']), 1)
        self.assertEqual(self.store.find(goal)[4]['waiting'], before)

    def test_unavailable_receipt_lookup_fails_closed(self):
        import custos_actions_client as client
        with patch.object(client, 'call', side_effect=OSError('private server detail')):
            self.assertEqual(cm.delivery_status('outbox:step'), {'phase':'unavailable'})

    def test_receipt_failures_never_become_submission(self):
        for result in [{'phase':'unknown'}, {'phase':'uncertain'}, {'phase':'submitted'},
                       {**SUCCESS, 'receipt': {**SUCCESS['receipt'], 'results':[{'type':'FAILURE'}]}},
                       {**SUCCESS, 'receipt': {**SUCCESS['receipt'], 'converted_to_reaction':'👍'}}]:
            self.assertFalse(cm.submitted(result))
        self.assertTrue(cm.submitted(SUCCESS))

    def test_successful_correction_resolves_suppressed_duplicate_but_not_uncertain(self):
        outgoing = [{'type':'message','from':'custos','to':'signal-test','reply_to':'trigger',
                     'step_id': str(i), 'delivery_kind':kind} for i,kind in enumerate(['completion','completion','correction'])]
        with patch.dict(os.environ, {'TRAJ_ID':'test'}), patch.object(cm,'trajectory',return_value=outgoing):
            with patch.object(cm,'delivery_status',side_effect=[SUCCESS, {'phase':'suppressed'}, SUCCESS]):
                self.assertEqual(cm.unresolved_deliveries(self.store, {'trigger_step':'trigger'}), [])
            with patch.object(cm,'delivery_status',side_effect=[SUCCESS, {'phase':'uncertain'}, SUCCESS]):
                self.assertEqual(cm.unresolved_deliveries(self.store, {'trigger_step':'trigger'}), [('outbox:1','uncertain')])

    def test_guard_wait_uses_eligibility_not_changing_reason_text(self):
        import custos_actions_client as client
        wait={'signal_target':'dm:fixed'}
        with patch.object(client,'social_guard',side_effect=ValueError('unanswered')):
            self.assertIsNone(cm.wait_event(wait))
        with patch.object(client,'social_guard'), patch.object(client,'call',return_value=(200,{'ok':True,'blocked':True})):
            self.assertIsNone(cm.wait_event(wait))
        with patch.object(client,'social_guard'), patch.object(client,'call',return_value=(200,{'ok':True,'blocked':False})):
            self.assertIn('eligible', cm.wait_event(wait))

    def test_square_receipt_wait_uses_existing_ledger_without_mutation(self):
        path=self.root/'observations.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE outbound(id TEXT,status TEXT,receipt TEXT)')
            db.execute('INSERT INTO outbound VALUES(?,?,?)',('traj:step','uncertain','{}'))
        with patch.dict(os.environ, {'CUSTOS_OBSERVE_DB':str(path)}):
            self.assertIn('uncertain',cm.wait_event({'receipt':'square:traj:step'}))
        with sqlite3.connect(path) as db: self.assertEqual(db.execute('SELECT status FROM outbound').fetchone()[0],'uncertain')

class ReceiptAPITests(unittest.TestCase):
    def test_native_status_reads_suppression_without_claiming_or_leaking_payload(self):
        import custos_actions as ca
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'spool.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE outbox(id TEXT,phase TEXT,receipt TEXT,content TEXT)')
                db.execute('INSERT INTO outbox VALUES(?,?,?,?)',('step','suppressed',json.dumps({'suppressed':'already claimed'}),'private message'))
            channel=ca.Channel.__new__(ca.Channel);channel.spool=str(path)
            result=channel.handle({'action':'delivery-status','reference':'outbox:step'})
            self.assertEqual(result['phase'],'suppressed');self.assertNotIn('private message',json.dumps(result))
            from custos_actions_client import explain_status
            semantics = explain_status(result)['delivery_state']
            self.assertTrue(semantics['terminal']);self.assertFalse(semantics['automatic_retry'])
            self.assertEqual(channel.handle({'action':'delivery-status','reference':'outbox:absent'})['phase'],'unknown')
            with self.assertRaises(ValueError): channel.handle({'action':'delivery-status','reference':'outbox:../../secret'})
            with sqlite3.connect(path) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)

class ScratchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'repo';self.repo.mkdir()
        cr.run('git','init',str(self.repo));cr.run('git','-C',str(self.repo),'config','user.name','test');cr.run('git','-C',str(self.repo),'config','user.email','test@example.invalid')
        (self.repo/'file').write_text('original');cr.run('git','-C',str(self.repo),'add','.');cr.run('git','-C',str(self.repo),'commit','-m','fixture')
        self.head=cr.run('git','-C',str(self.repo),'rev-parse','HEAD');self.registry=cr.Registry(self.root/'registry')
        self.paths=[];self.addCleanup(self.cleanup)
    def cleanup(self):
        import shutil
        for p in self.paths: shutil.rmtree(p,ignore_errors=True)
    def create(self,kind='worktree'):
        row=self.registry.create(str(self.repo),self.head,'1234abcd',kind);self.paths.append(row['path']);return Path(row['path'])
    def test_completion_not_pr_closure_controls_cleanup(self):
        p=self.create();self.assertEqual(self.registry.clean()['removed'],[])
        self.assertEqual(self.registry.clean('1234abcd','wrong head')['removed'],[])
        self.assertEqual(self.registry.clean('1234abcd','Reviewed '+self.head)['removed'],[str(p)])
    def test_dirty_worktree_and_modified_copy_are_preserved(self):
        for kind in ('worktree','copy'):
            p=self.create(kind);(p/'file').write_text('valuable edits')
            self.registry.clean('1234abcd',self.head);self.assertTrue(p.exists())
    def test_clean_archive_copy_is_removed(self):
        p=self.create('copy');self.assertIn(str(p),self.registry.clean('1234abcd',self.head)['removed'])
    def test_unregistered_path_and_budget(self):
        p=self.root/'pr1234';p.mkdir();self.registry.clean('1234abcd',self.head);self.assertTrue(p.exists())
        self.registry.budget=1
        with self.assertRaises(ValueError):self.create()

class ResearchTests(unittest.TestCase):
    def claim(self,variant,field,value,unit,quote):
        return dict(variant=variant,field=field,value=value,unit=unit,sources=[dict(url='https://vendor.example/spec',quote=quote,primary=True)],conflicts=[])
    def test_actual_and_equivalent_focal_length_replay(self):
        claims=[self.claim('Dwarf mini','actual_focal_length',150,'mm','Focal length: 150 mm'),self.claim('Dwarf mini','aperture',30,'mm','Aperture: 30 mm'),self.claim('Dwarf mini','equivalent_focal_length',1016,'mm','Equivalent focal length 1016 mm')]
        ratio=dict(variant='Dwarf mini',numerator='actual_focal_length',denominator='aperture',kind='focal_ratio')
        self.assertEqual(ce.validate({'claims':claims,'ratios':[ratio]})['ratios'][0]['value'],5)
        with self.assertRaises(ValueError):ce.validate({'claims':claims,'ratios':[{**ratio,'numerator':'equivalent_focal_length'}]})
        s50=[self.claim('S50 Pro','actual_focal_length',260,'mm','260 mm'),self.claim('S50 Pro','aperture',50,'mm','50 mm')]
        self.assertEqual(ce.validate({'claims':s50,'ratios':[{**ratio,'variant':'S50 Pro'}]})['ratios'][0]['value'],5.2)
    def test_doorway_and_zenith_are_distinct_and_conflict_is_retained(self):
        claims=[self.claim('POD-S','doorway_height',5,'ft','Just under 5 feet'),self.claim('POD-S','zenith_height',7.5,'ft','7 feet 6 inches')]
        claims[1]['conflicts']=[{'url':'https://vendor.example/faq','quote':'Just under 5 feet'}]
        result=ce.validate({'claims':claims});self.assertFalse(result['ready']);self.assertTrue(result['claims'][1]['conflicts'])
        claims[1]['resolution']='FAQ refers to doorway clearance, not zenith height'
        self.assertTrue(ce.validate({'claims':claims})['ready'])
    def test_missing_primary_and_mismatched_units_refused(self):
        a=self.claim('A','actual_focal_length',150,'mm','150 mm');b=self.claim('A','aperture',3,'cm','3 cm')
        with self.assertRaises(ValueError): ce.validate({'claims':[a,b],'ratios':[dict(variant='A',numerator='actual_focal_length',denominator='aperture')]})
        a['sources'][0]['primary']=False
        with self.assertRaises(ValueError): ce.validate({'claims':[a]})

class FailureProvenanceTests(unittest.TestCase):
    def run_case(self, mode):
        import shutil
        root=Path(tempfile.mkdtemp());self.addCleanup(shutil.rmtree,root)
        runtime=Path(os.environ.get('HEADLONG_ROOT',Path(__file__).resolve().parents[2]/'runtime/headlong'))
        shutil.copytree(runtime/'bin',root/'bin');(root/'wd').mkdir();(root/'home').mkdir()
        llm=root/'bin/llm'
        llm.write_text('''#!/usr/bin/env bash
case "$TEST_MODE" in
 gateway) echo 'private-prompt api-secret-example' >&2; exit 7 ;;
 empty) exit 0 ;;
 success) printf '```bash\\nFINAL=done\\n```\\n' ;;
 blocks) printf '```bash\\nprintf first > first\\n```\\n```bash\\nprintf second > second\\nFINAL=done\\n```\\n' ;;
esac
''');llm.chmod(0o755)
        diagnostic=root/'failure.json';diagnostic.write_text('{}')
        env={**os.environ,'PATH':str(root/'bin')+':'+os.environ['PATH'],'HOME':str(root/'home'),
             'HEADLONG_HOME':str(root/'home/.headlong'),'SHELLM_ENV':'local','TEST_MODE':mode,
             'SHELLM_RUN_SUMMARY':'0','SHELLM_EMPTY_RESPONSE_RETRIES':'0','SHELLM_RUN_ALL_BLOCKS':'1',
             'SHELLM_DIAGNOSTIC_OUT':str(diagnostic),'ANTHROPIC_API_KEY':'offline-fixture-key'}
        for key in ('TRAJ_ID','ROOT_TRAJ_ID','TRAJ_DIR','SHELLM_RUN_ID_OUT','SHELLM_PARENT_PID','IDENTITY_DIR'):
            env.pop(key,None)
        result=subprocess.run([str(root/'bin/shellm'),'--model','test-model','--workdir',str(root/'wd'),'--max-iterations','1','offline fixture'],env=env,input='',capture_output=True,text=True,timeout=45)
        return root,result,json.loads(diagnostic.read_text())
    def test_gateway_failure_has_stage_without_prompt_or_secret(self):
        _,result,diagnostic=self.run_case('gateway')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(diagnostic['stage'],'gateway');self.assertEqual(diagnostic['operation'],'call_llm')
        self.assertNotIn('private-prompt',json.dumps(diagnostic));self.assertNotIn('secret',json.dumps(diagnostic))
    def test_empty_visible_response_is_distinct_from_gateway_failure(self):
        _,result,diagnostic=self.run_case('empty')
        self.assertNotEqual(result.returncode,0);self.assertEqual(diagnostic['operation'],'empty_response_exhausted')
    def test_success_has_no_failure_and_all_closed_blocks_still_execute(self):
        root,result,diagnostic=self.run_case('blocks')
        self.assertEqual(result.returncode,0,result.stderr[-1000:]);self.assertEqual(diagnostic,{})
        self.assertEqual((root/'wd/first').read_text(),'first');self.assertEqual((root/'wd/second').read_text(),'second')
