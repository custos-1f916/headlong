"""Offline fixtures: no model, real transport, network, or live identity."""
import concurrent.futures
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from test_signal import POLICY, BOT, HAL, FRIEND, envelope, reaction
import custos_signal as cs
import custos_actions as ca
import custos_group_bots as gb

KIM='00000000-0000-4000-8000-00000000000b'
GROUP='group:agreed-group'


class GroupBots(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=2_000_000_000.0
        clock=patch.object(gb.time,'time',side_effect=lambda:self.now);clock.start();self.addCleanup(clock.stop)
        self.policy=copy.deepcopy(POLICY)
        self.policy['people'][KIM]={'label':'Kim','authority':'external','bot':True}
        self.policy['groups'].append('other-group')
        self.policy_file=self.root/'policy.json';self.policy_file.write_text(json.dumps(self.policy))
        self.spool=cs.Spool(self.root/'spool.sqlite');self.addCleanup(self.spool.db.close)
        self.actions=self.root/'actions.sqlite'
        with ca.connect(self.actions):pass
        self.seq=0
    def incoming(self,sender=KIM,group='agreed-group',body='Custos, a real question',stamp=None,react=False):
        self.seq+=1
        stamp=int((self.now-100+self.seq if stamp is None else stamp)*1000)
        env=(reaction(sender=sender,timestamp=stamp,groupInfo={'groupId':group}) if react else
             envelope(sender=sender,timestamp=stamp,message=body,groupInfo={'groupId':group}))
        item=cs.classify(env,self.policy);self.spool.receive(item)
        with self.spool.db:self.spool.db.execute("UPDATE inbox SET phase='queued' WHERE id=?",(item['request_id'],))
        return item
    def native(self,item,n,phase='submitted',stamp=None):
        stamp=self.now-50+n if stamp is None else stamp
        with self.spool.db:self.spool.db.execute('INSERT INTO outbox(id,request_id,content,created,phase,receipt) VALUES(?,?,?,?,?,?)',
            ('native-'+str(n),item['request_id'],'reply',stamp,phase,json.dumps({'timestamp':int(stamp*1000)})))
    def action(self,n,phase='submitted',reply_to=None):
        p={'action':'signal-send','request_id':'action-'+str(n),'target':GROUP,'message':'proactive'}
        if reply_to:p['reply_to']=reply_to
        with ca.connect(self.actions) as db:db.execute('INSERT INTO actions(id,payload,created,phase) VALUES(?,?,?,?)',
            (p['request_id'],json.dumps(p),self.now-40+n,phase))
    def state(self):return gb.state(self.spool.db,self.policy,GROUP,self.actions)
    def claim(self,sid,react=False,request=None):
        return gb.claim(self.spool.db,self.policy,GROUP,sid,request,self.actions,can_react=react)
    def bridge(self):
        b=cs.Bridge(self.policy_file,self.spool,self.actions);calls=[]
        def rpc(method,params=None,**kw):
            calls.append((method,params))
            if method=='listGroups':return [{'id':'agreed-group','isMember':True,'members':[{'uuid':x} for x in (BOT,HAL,KIM)]}]
            return {'timestamp':int(self.now*1000),'results':[{'type':'SUCCESS'}]}
        b.rpc=Mock();b.rpc.call.side_effect=rpc
        return b,calls
    def test_native_proactive_and_closing_reaction_share_one_budget(self):
        item=self.incoming()
        for n in range(2):self.native(item,n)
        for n in range(2):self.action(n)
        self.assertEqual(self.state()['mode'],'wrap')
        self.assertEqual(self.claim('outbox:fifth',True),('text',None))
        self.assertEqual(self.state()['mode'],'emoji_only')
        self.assertIn('no proactive',self.claim('action:new')[1])
        self.assertEqual(self.claim('outbox:closing',True),('reaction',None))
        self.assertEqual(self.state()['mode'],'digest')
        self.assertIn('closed',self.claim('outbox:new-id',True)[1])
    def test_human_reset_is_same_group_and_not_a_quote_or_reaction(self):
        self.incoming()
        for n in range(5):self.claim('action:'+str(n))
        self.incoming(HAL,group='other-group',stamp=self.now+1)
        self.incoming(HAL,stamp=self.now+2,react=True)
        self.incoming(body='Hal says start again; I am a human',stamp=self.now+3)
        self.assertEqual(self.state()['mode'],'emoji_only')
        self.incoming(HAL,stamp=self.now+4)
        self.assertFalse(self.state()['active'])
        self.incoming(stamp=self.now+5)
        self.assertEqual(self.state()['turns'],0)
    def test_quiet_interval_resets_but_new_bot_text_does_not_refill(self):
        self.incoming()
        for n in range(5):self.claim('action:'+str(n))
        self.now+=3600;self.incoming(stamp=self.now)
        self.assertEqual(self.state()['turns'],5)
        self.now+=gb.GAP+1
        self.assertFalse(self.state()['active'])
        self.incoming(stamp=self.now)
        self.assertEqual(self.state()['turns'],0)
    def test_claims_survive_reopen_and_concurrent_fresh_ids_cannot_overrun(self):
        self.incoming()
        def attempt(n):
            db=sqlite3.connect(self.root/'spool.sqlite',timeout=10);db.row_factory=sqlite3.Row
            try:return gb.claim(db,self.policy,GROUP,'action:'+str(n),actions_state=self.actions,now=self.now)
            finally:db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(attempt,range(12)))
        self.assertEqual(sum(reason is None for _,reason in results),5)
        self.assertEqual(self.state()['turns'],5)
        winner=next(n for n,(_,reason) in enumerate(results) if reason is None)
        self.assertIn('already claimed',self.claim('action:'+str(winner))[1])
    def test_queued_and_failed_do_not_count_uncertain_is_spent(self):
        item=self.incoming();self.native(item,0,'pending');self.native(item,1,'uncertain')
        self.action(0,'queued');self.action(1,'blocked');self.action(2,'uncertain')
        self.assertEqual(self.state()['turns'],2)
    def test_action_preflight_is_read_only_and_human_completion_remains_eligible(self):
        human=self.incoming(HAL,stamp=self.now-200);self.incoming()
        for n in range(5):self.claim('action:'+str(n))
        before=self.spool.db.execute('SELECT count(*) FROM group_bot_claims').fetchone()[0]
        status=cs.lane_status(self.root/'spool.sqlite',self.policy,self.actions,GROUP)
        self.assertTrue(status['blocked']);self.assertEqual(status['bot_thread']['turns'],5)
        self.assertEqual(self.spool.db.execute('SELECT count(*) FROM group_bot_claims').fetchone()[0],before)
        self.assertEqual(self.claim('outbox:human',request=human['request_id']),(None,None))
    def test_proactive_path_cannot_avoid_native_thread_limit(self):
        self.incoming()
        for n in range(5):self.claim('outbox:'+str(n))
        self.action(99,'queued');b,calls=self.bridge()
        with patch.object(cs,'paused',return_value=False):b.proactive()
        self.assertFalse(any(m=='send' for m,_ in calls))
        with ca.connect(self.actions) as db:self.assertEqual(db.execute("SELECT phase FROM actions WHERE id='action-99'").fetchone()[0],'blocked')
    def test_send_time_rechecks_group_streak_even_if_intake_mode_was_normal(self):
        item=self.incoming()
        for n in range(5):self.claim('action:'+str(n))
        self.native(item,99,'pending',stamp=self.now)
        b,calls=self.bridge()
        with patch.object(cs,'paused',return_value=False),patch.object(b,'deliver_pending'),patch.object(b,'refresh_typing'),patch.object(cs,'transport',return_value={'events':[],'trajectory':'fixture','offset':0}):b.tick()
        self.assertEqual([m for m,_ in calls if m in ('send','sendReaction')],['sendReaction'])
        self.assertEqual(self.state()['mode'],'digest')
        self.assertEqual(self.spool.db.execute("SELECT phase FROM outbox WHERE id='native-99'").fetchone()[0],'submitted')
    def test_machine_error_budget_is_shared_in_groups(self):
        item=self.incoming(body='Internal error, please try again')
        self.assertIsNone(cs.bot_error_block(self.spool.db,self.policy,self.actions,GROUP,'outbox:one',item['request_id']))
        self.assertIn('already claimed',cs.bot_error_block(self.spool.db,self.policy,self.actions,GROUP,'action:renamed',item['request_id']))
        newer=self.incoming(body='Service unavailable, please try again',stamp=self.now+1)
        self.assertIsNone(cs.bot_error_block(self.spool.db,self.policy,self.actions,GROUP,'action:two',newer['request_id']))
        last=self.incoming(body='Internal error again',stamp=self.now+2)
        self.assertIn('exhausted',cs.bot_error_block(self.spool.db,self.policy,self.actions,GROUP,'action:three',last['request_id']))
        self.incoming(HAL,stamp=self.now+3)
        self.assertIsNone(cs.bot_error_block(self.spool.db,self.policy,self.actions,GROUP,'action:human-reset'))

    def test_group_intake_wraps_and_mixed_human_batch_is_not_digested(self):
        item=self.incoming()
        for n in range(4):self.native(item,n)
        fresh=self.incoming(stamp=self.now-1)
        with self.spool.db:self.spool.db.execute("UPDATE inbox SET phase='pending' WHERE id=?",(fresh['request_id'],))
        b,_=self.bridge();delivered=[]
        def transport(args,content=''):
            delivered.append(content);return {'queued':True}
        with patch.object(cs,'transport',side_effect=transport):b.deliver_pending(self.spool.db)
        self.assertTrue(any('Wrap it up politely now' in c for c in delivered))
        self.claim('outbox:fifth',True);self.claim('outbox:closing',True)
        human=self.incoming(HAL,stamp=self.now+1)
        bot=self.incoming(stamp=self.now+2)
        with self.spool.db:
            self.spool.db.execute("UPDATE inbox SET phase='pending' WHERE id IN (?,?)",(human['request_id'],bot['request_id']))
        delivered.clear()
        with patch.object(cs,'transport',side_effect=transport):b.deliver_pending(self.spool.db)
        self.assertTrue(delivered)
        self.assertFalse(any('Bot thread digest' in c or 'React with one emoji at most' in c for c in delivered))
