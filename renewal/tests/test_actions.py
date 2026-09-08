import copy
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import custos_actions as ca
import custos_signal as cs
import custos_automata_deploy as cad
from test_signal import POLICY, HAL, BOT, STRANGER


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.policy=self.root/'policy.json';self.policy.write_text(json.dumps(POLICY))
        self.state=self.root/'actions.sqlite';self.channel=ca.Channel(self.state,self.policy)
        self.bridge=cs.Bridge(self.policy,cs.Spool(self.root/'signal.sqlite'),self.state)
        self.addCleanup(self.bridge.spool.db.close)
        self.bridge.rpc=Mock()
        self.bridge.rpc.call.return_value={'timestamp':1234,'results':[{'type':'SUCCESS'}]}
        self.p={'action':'signal-send','request_id':'one','target':'Hal','message':'Hello'}

    def status(self): return self.channel.handle({'action':'status','request_id':'one'})

    def test_proactive_dm_without_any_inbound_and_replay(self):
        self.assertEqual(self.channel.handle(self.p)['phase'],'queued')
        self.assertEqual(self.channel.handle(self.p)['phase'],'queued')
        with patch.object(cs,'paused',return_value=False):
            self.bridge.proactive(); self.bridge.proactive()
        self.bridge.rpc.call.assert_called_once_with('send',{'message':'Hello','recipient':[HAL]})
        self.assertEqual(self.status()['phase'],'submitted')
        self.assertEqual(self.channel.handle(self.p)['phase'],'submitted')
        with self.assertRaises(ValueError): self.channel.handle({**self.p,'message':'different'})

    def test_every_allowlisted_person_and_only_the_approved_group_resolve(self):
        for dest in ca.destinations(POLICY): self.assertEqual(ca.resolve(dest['target'],POLICY),dest['target'])
        for target in ('dm:'+STRANGER,'group:other','wife-not-verified'):
            with self.assertRaises(ValueError): self.channel.handle({**self.p,'target':target})
        for key in ('socket','command','container','recipient'):
            with self.assertRaises(ValueError): self.channel.handle({**self.p,key:'ignored?'})

    def test_group_topic_needs_live_safe_membership(self):
        self.p['target']='Group';self.channel.handle(self.p)
        group={'id':'agreed-group','isMember':True,'members':[BOT,HAL]}
        self.bridge.rpc.call.side_effect=[[group],{'timestamp':12,'results':[{'type':'SUCCESS'}]}]
        with patch.object(cs,'paused',return_value=False): self.bridge.proactive()
        self.assertEqual(self.status()['phase'],'submitted')
        self.assertEqual(self.bridge.rpc.call.call_args.args,('send',{'message':'Hello','groupId':'agreed-group'}))

    def test_changed_allowlist_or_group_blocks_queued_message(self):
        self.channel.handle(self.p)
        policy=copy.deepcopy(POLICY);del policy['people'][HAL];self.policy.write_text(json.dumps(policy))
        with patch.object(cs,'paused',return_value=False): self.bridge.proactive()
        self.assertEqual(self.status()['phase'],'blocked');self.bridge.rpc.call.assert_not_called()

    def test_new_group_member_blocks_send(self):
        self.channel.handle({**self.p,'target':'Group'})
        self.bridge.rpc.call.return_value=[{'id':'agreed-group','isMember':True,'members':[BOT,HAL,STRANGER]}]
        with patch.object(cs,'paused',return_value=False): self.bridge.proactive()
        self.assertEqual(self.status()['phase'],'blocked')
        self.bridge.rpc.call.assert_called_once_with('listGroups',{'detailed':True})

    def test_pause_and_shared_route_pacing(self):
        self.channel.handle(self.p)
        with patch.object(cs,'paused',return_value=True): self.bridge.proactive()
        self.assertEqual(self.status()['phase'],'queued');self.bridge.rpc.call.assert_not_called()
        route='signal-'+cs.hashlib.sha256(('dm:'+HAL).encode()).hexdigest()[:24]
        self.bridge.last_send[route]=cs.time.monotonic()
        with patch.object(cs,'paused',return_value=False): self.bridge.proactive()
        self.bridge.rpc.call.assert_not_called()

    def test_ambiguous_send_and_restart_never_retry(self):
        self.channel.handle(self.p);self.bridge.rpc.call.side_effect=TimeoutError()
        with patch.object(cs,'paused',return_value=False),self.assertRaises(TimeoutError): self.bridge.proactive()
        self.assertEqual(self.status()['phase'],'uncertain')
        with patch.object(cs,'paused',return_value=False): self.bridge.proactive()
        self.assertEqual(self.bridge.rpc.call.call_count,1)
        with ca.connect(self.state) as db: db.execute("UPDATE actions SET phase='sending'")
        cs.Bridge(self.policy,self.bridge.spool,self.state)
        self.assertEqual(self.status()['phase'],'uncertain')

    def test_deploy_requires_exact_fields_commit_and_goal(self):
        p={'action':'automata-deploy','request_id':'deploy-one','goal_id':'0123abcd','commit':'a'*40}
        self.assertEqual(self.channel.handle(p)['phase'],'queued')
        for extra in ({'commit':'main'},{'commit':'a'*40+';reboot'},{'container':122},{'path':'/'},{'goal_id':'bad'}):
            with self.assertRaises(ValueError): self.channel.handle({**p,**extra})

    def test_host_command_output_is_bounded_and_failure_is_not_success(self):
        with self.assertRaises(ValueError): ca.run_bounded([sys.executable,'-c','print("x"*1000)'],maximum=10)
        with self.assertRaises(RuntimeError): ca.run_bounded([sys.executable,'-c','raise SystemExit(2)'])


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def archive(self,name,kind=tarfile.REGTYPE):
        path=self.root/'source.tar'
        with tarfile.open(path,'w') as tf:
            item=tarfile.TarInfo(name);item.type=kind
            item.linkname='/outside';item.size=0;tf.addfile(item,io.BytesIO(b''))
        return path
    def test_archive_links_traversal_and_persistent_data_rejected(self):
        for name,kind in [('../evil',tarfile.REGTYPE),('/evil',tarfile.REGTYPE),
                          ('link',tarfile.SYMTYPE),('hard',tarfile.LNKTYPE),('device',tarfile.CHRTYPE),
                          ('species/saved.json',tarfile.REGTYPE),('.env',tarfile.REGTYPE)]:
            dest=self.root/'dest';dest.mkdir(exist_ok=True)
            with self.subTest(name=name),self.assertRaises(ValueError): cad.extract(self.archive(name,kind),dest)
        self.assertFalse((self.root/'evil').exists())
    def test_health_failure_restores_previous_release(self):
        state=self.root/'state.json';original={'current':'/previous','commit':'a'*40,'previous':None,'previous_commit':None}
        state.write_text(json.dumps(original))
        with patch.object(cad,'STATE',state),patch.object(cad,'switch') as switch,patch.object(cad,'run'), \
             patch.object(cad,'wait_healthy',side_effect=[RuntimeError(),None]),patch.object(cad,'status',return_value={'healthy':True}):
            result=cad.activate(Path('/candidate'),'b'*40)
        self.assertFalse(result['ok']);self.assertTrue(result['rolled_back'])
        self.assertEqual([c.args[0] for c in switch.call_args_list],[Path('/candidate'),Path('/previous')])
        self.assertEqual(json.loads(state.read_text()),original)
    def test_success_retains_previous_commit_for_explicit_rollback(self):
        state=self.root/'state.json';state.write_text(json.dumps({'current':'/previous','commit':'a'*40}))
        with patch.object(cad,'STATE',state),patch.object(cad,'switch'),patch.object(cad,'run'), \
             patch.object(cad,'wait_healthy'),patch.object(cad,'status',return_value={'healthy':True}):
            self.assertTrue(cad.activate(Path('/candidate'),'b'*40)['ok'])
        self.assertEqual(json.loads(state.read_text())['previous_commit'],'a'*40)

if __name__=='__main__': unittest.main()
