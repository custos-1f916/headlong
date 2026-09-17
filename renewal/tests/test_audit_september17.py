"""Real store lifecycle, transport races, sealed cursor recovery and huge output."""
import concurrent.futures
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from test_memory import MemoryFixture, HEADLONG
from test_observations import NativeFixture, InboxFixture, page, item
from test_signal import POLICY, HAL, FRIEND, envelope
import custos_memory as cm
import custos_signal as cs
import custos_actions as ca
from custos_observe import Observer
from custos_square import Store, APIError

class WaitingTests(MemoryFixture):
    def test_wait_removes_urgency_replay_and_reactions_do_not_resume(self):
        p = dict(self.payload, authority='operator', sender='signal-fixture', content='Add milk to grocery cart', allow_reaction=True)
        gid = self.store.capture(p, 'old', deferred=True)['goal_id']
        self.store.wait({'goal_id': gid, 'reason': 'Await order confirmation'})
        self.store.capture(p, 'old')
        self.store.capture(dict(p, request_id='reaction', content=cm.REACTION_PREFIX + 'thumbs up'), 'emoji')
        c = self.store.context()
        self.assertNotIn(gid, [g['goal_id'] for g in c['goals']])
        self.assertEqual(c['waiting'][0]['goal_id'], gid)
        self.assertNotIn('OVERDUE', cm.context_text(c))
        self.store.capture(dict(p, request_id='other', sender='someone-else'), 'other-step')
        self.assertEqual(len(self.store.context()['waiting']), 1)
        self.store.capture(dict(p, request_id='new', content='Order confirmed', authority='external'), 'new-step')
        self.assertEqual(self.store.context()['waiting'], [])
        self.assertIn('New inbound', self.store.find(gid)[4]['events'][-1]['resumed'])

    def test_wait_rejects_stale_inspection_and_replaces_old_timer(self):
        gid=self.store.capture(self.payload,deferred=True)['goal_id']
        with self.assertRaisesRegex(cm.MemoryError,'changed'):
            self.store.wait({'goal_id':gid,'reason':'gate','expected_sha256':'0'*64})
        future=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat()
        self.store.update({'goal_id':gid,'not_before':future})
        self.store.wait({'goal_id':gid,'reason':'human gate'})
        self.assertNotIn('not_before',self.store.find(gid)[4])
        self.assertEqual(len(self.store.context()['waiting']),1)

    def test_timed_check_resumes_once_and_wait_is_not_expired(self):
        gid = self.store.capture(self.payload, deferred=True)['goal_id']
        self.store.wait({'goal_id': gid, 'reason': 'External result pending', 'resume_sender': ''})
        self.assertEqual(cm.expire_asks(self.store, older_than_hours=0), [])
        obj=self.store.find(gid);obj[4]['waiting']['check_at']='2020-01-01T00:00:00+00:00';self.store.save(obj,obj[4])
        self.assertEqual(self.store.context()['total'],1)
        count=len(self.store.find(gid)[4]['events']);self.store.context()
        self.assertEqual(len(self.store.find(gid)[4]['events']),count)
        self.assertFalse(self.store.context()['goals'][0].get('stale'))

    def test_correction_is_durable_idempotent_and_preserves_original(self):
        gid=self.store.capture(self.payload, deferred=True)['goal_id']
        self.store.complete({'goal_id':gid,'disposition':'completed','evidence':'outbox:sent'})
        before=self.store.find(gid)[0].read_bytes()
        payload={'goal_id':gid,'receipt':'outbox:sent','evidence':'Primary source says difference was not significant'}
        a=self.store.correction(payload);b=self.store.correction(payload)
        self.assertEqual(a['goal_id'],b['goal_id']);self.assertFalse(b['created'])
        self.assertEqual(before,self.store.find(gid)[0].read_bytes())
        self.assertTrue(cm.is_task(self.store.find(a['goal_id'])[4]))

class CursorTests(unittest.TestCase):
    def test_sealed_partial_page_and_uncertain_ack_recover_without_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root);self.addCleanup(store.db.close);native=NativeFixture()
            response=page([item(11),item(12)]);response['ack_cursor']['seal']='x'*43
            api=InboxFixture([response],native.events)
            Observer({'max_signals_per_run':1},store,api,native,now=2000).inbox()
            self.assertFalse(api.acks);api.fail_ack=True
            with self.assertRaises(APIError):Observer({},store,api,native,now=2000).inbox()
            api.fail_ack=False;Observer({},store,api,native,now=2000).inbox()
            self.assertEqual(api.acks,[response['ack_cursor']]);self.assertEqual(len(native.goals),2)
            self.assertIsNone(store.get('inbox:page'))
    def test_malformed_seal_and_unknown_cursor_fields_fail_closed(self):
        for extra in ({'seal':''},{'seal':123},{'seal':'../bad'},{'unknown':1}):
            with tempfile.TemporaryDirectory() as root:
                store=Store(root);native=NativeFixture();response=page([]);response['ack_cursor'].update(extra)
                api=InboxFixture([response],native.events)
                try:
                    with self.assertRaises(APIError):Observer({},store,api,native,now=2000).inbox()
                    self.assertFalse(api.acks)
                finally:store.db.close()

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.path=self.root/'spool.sqlite';self.spool=cs.Spool(self.path);self.addCleanup(self.spool.db.close)
        self.incoming=cs.classify(envelope(),POLICY);self.spool.receive(self.incoming)
        self.conv=self.incoming['conversation'];self.req=self.incoming['request_id']
    def test_native_and_actions_race_has_exactly_one_substantive_owner(self):
        def claim(n):
            spool=cs.Spool(self.path)
            try:return spool.claim_delivery(self.conv,self.req,('action:' if n%2 else 'outbox:')+str(n))
            finally:spool.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(claim,range(16)))
        self.assertEqual(results.count(None),1)
        # A restart/uncertain delivery cannot be retried under a new ID.
        self.assertIn('already claimed',claim(100))
    def test_ack_completion_and_verified_correction_are_separate(self):
        claim=self.spool.claim_delivery
        self.assertIsNone(claim(self.conv,self.req,'outbox:ack','acknowledgment'))
        self.assertIsNone(claim(self.conv,self.req,'outbox:done'))
        self.assertIn('late acknowledgment',claim(self.conv,self.req,'outbox:ack2','acknowledgment'))
        self.assertIn('not submitted',claim(self.conv,self.req,'outbox:fix','correction','outbox:done'))
        with self.spool.db:
            self.spool.db.execute("INSERT INTO outbox(id,request_id,content,phase,created) VALUES(?,?,?,'submitted',0)",('done',self.req,'result'))
        self.assertIsNone(claim(self.conv,self.req,'outbox:fix','correction','outbox:done'))
        self.assertIn('already claimed',claim(self.conv,self.req,'action:fix2','correction','outbox:done'))
        self.assertIn('not submitted',claim('dm:wrong',self.req,'outbox:wrong','correction','outbox:done'))
    def test_proactive_correction_verifies_original_conversation_without_inbound(self):
        actions=self.root/'actions.sqlite'
        with ca.connect(actions) as db:
            db.execute("INSERT INTO actions(id,payload,created,phase) VALUES(?,?,0,'submitted')", ('news',json.dumps({'action':'signal-send','target':self.conv,'message':'Initial finding'})))
            db.commit()
            self.assertIsNone(self.spool.claim_delivery(self.conv,None,'action:fix','correction','action:news',db))
            self.assertIn('already claimed',self.spool.claim_delivery(self.conv,None,'action:fix2','correction','action:news',db))
            self.assertIn('not submitted',self.spool.claim_delivery('dm:other',None,'action:wrong','correction','action:news',db))
    def test_real_bridge_routes_native_and_action_completions_through_one_claim(self):
        from unittest.mock import Mock
        pp=self.root/'policy.json';pp.write_text(json.dumps(POLICY));actions=self.root/'actions.sqlite'
        channel=ca.Channel(actions,pp,self.path);bridge=cs.Bridge(pp,self.spool,actions)
        bridge.rpc=Mock();bridge.rpc.call.return_value={'timestamp':88,'results':[{'type':'SUCCESS'}]}
        with self.spool.db:self.spool.db.execute("UPDATE inbox SET phase='queued'")
        self.spool.batch(self.incoming['route'], {'trajectory':'test','offset':1,'events':[{'step_id':'native','request_id':self.req,'content':'Result'}]})
        channel.handle({'action':'signal-send','request_id':'parallel','target':self.conv,'message':'Same result','reply_to':self.req})
        with patch.object(cs,'paused',return_value=False), patch.object(cs,'transport',return_value={'trajectory':'test','offset':1,'events':[]}):
            bridge.tick();bridge.last_send.clear();bridge.proactive()
        sends=[c for c in bridge.rpc.call.call_args_list if c.args[0]=='send']
        self.assertEqual(len(sends),1)
        self.assertEqual(channel.handle({'action':'status','request_id':'parallel'})['phase'],'blocked')
    def test_malformed_native_metadata_fails_closed(self):
        self.assertIn('invalid',self.spool.claim_delivery(self.conv,self.req,'bad',[]))
        self.assertIn('invalid',self.spool.claim_delivery(self.conv,self.req,'bad','correction',{'secret':'not a receipt'}))
    def test_lane_preflight_does_not_queue_renamed_attempt_or_mutate_claims(self):
        policy=copy.deepcopy(POLICY);policy['people'][FRIEND]['bot']=True
        pp=self.root/'policy.json';pp.write_text(json.dumps(policy));actions=self.root/'actions.sqlite'
        channel=ca.Channel(actions,pp,self.path);bridge=cs.Bridge(pp,self.spool,actions)
        incoming=cs.classify(envelope(sender=FRIEND,message='Internal error. Please try again.'),policy)
        self.spool.receive(incoming)
        self.assertIsNone(bridge.claim_bot_error(incoming['conversation'],'outbox:first',incoming['request_id']))
        for key in ('retry1','retry2'):
            result=channel.handle({'action':'signal-send','request_id':key,'target':'Friend','message':'Try again'})
            self.assertEqual(result['error'],'lane_blocked')
        with ca.connect(actions) as db:self.assertEqual(db.execute('SELECT count(*) FROM actions').fetchone()[0],0)
        self.assertEqual(self.spool.db.execute('SELECT count(*) FROM bot_error_claims').fetchone()[0],1)
        result=channel.handle({'action':'signal-lane-status','target':'Friend'});self.assertTrue(result['blocked'])
        self.spool.receive(cs.classify(envelope(sender=FRIEND,message='Recovered; substantive answer.',timestamp=123457),policy))
        self.assertFalse(channel.handle({'action':'signal-lane-status','target':'Friend'})['blocked'])

class OutputTests(unittest.TestCase):
    def test_large_sparse_output_is_bounded_before_formatting_and_retained(self):
        helper=HEADLONG/'bin/shellm-output'
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'output';saved=Path(root)/'bounded.txt'
            with path.open('wb') as f:
                f.write(b'first\n');f.seek(617552000);f.write(b'\nlast\n')
            result=subprocess.run([sys.executable,str(helper),'stream',str(path),'0','1'],capture_output=True,timeout=3)
            self.assertEqual(int(result.stdout),path.stat().st_size);self.assertFalse(result.stderr)
            result=subprocess.run([sys.executable,str(helper),'capture',str(path),str(saved)],capture_output=True,timeout=3)
            self.assertEqual(result.returncode,0);self.assertLess(len(result.stdout),263000)
            self.assertIn(b'first',result.stdout);self.assertIn(b'last',result.stdout);self.assertIn(b'not retained',result.stdout)
            self.assertEqual(saved.stat().st_mode & 0o777,0o600)
    def test_diagnostic_prefers_failure_in_this_run_not_other_or_later_success(self):
        source=(HEADLONG/'thinkers/monolith/step').read_text();start=source.index('_last_diagnostic_id() {');end=source.index('before_visible=""',start)
        rows=[{'type':'shell-output','run_id':r,'step_id':s,'stdout':'evidence','exit':e} for r,s,e in [('mine','failure',1),('mine','later',0),('other','wrong',2)]]
        script=source[start:end]+'\n_probe_tail=$(cat)\n_last_diagnostic_id mine\n'
        result=subprocess.run(['bash','-c',script],input='\n'.join(map(json.dumps,rows)),text=True,capture_output=True,timeout=3)
        self.assertEqual(result.stdout.strip(),'failure')
