import json
import sys
import tempfile
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_square_policy as policy
from custos_square import APIError, Square, Store, canonical
from custos_observe import Observer
from test_observations import NativeFixture
import test_observations as observation_fixtures


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.addCleanup(self.store.db.close)
        self.now = 1789603200.0  # exact UTC reset
        self.payload = {'post_id': 10, 'parent_id': 22, 'body': 'a concrete finding'}

    def spend(self, name, ago, priority=False, status='delivered'):
        with self.store.db:
            self.store.db.execute('INSERT INTO outbound VALUES (?,?,?,?,?,?)',
                (name, 'comment', canonical(dict(self.payload, body=name)), status, None, self.now-ago))
        self.store.put('square:priority:' + name, priority)

    def test_midnight_does_not_reset_burst_or_rolling_budget(self):
        self.spend('one', 60)
        self.spend('two', 30)
        gate = policy.admission(self.store, self.payload, 20, self.now)
        self.assertFalse(gate['allowed'])
        self.assertEqual(gate['next_at'], self.now-60+1800)
        self.assertTrue(policy.admission(self.store, self.payload, 20, self.now+1801)['allowed'])

    def test_reserve_requires_recent_bridge_evidence_and_matching_target(self):
        self.assertFalse(policy.admission(self.store, self.payload, 8, self.now)['allowed'])
        self.store.put('square:directed:10:22', {'at': self.now})
        self.assertTrue(policy.admission(self.store, self.payload, 8, self.now)['allowed'])
        self.assertFalse(policy.admission(self.store, dict(self.payload, parent_id=23), 8, self.now)['allowed'])
        self.assertFalse(policy.admission(self.store, self.payload, 8, self.now+policy.WINDOW)['allowed'])

    def test_directed_capacity_survives_discretionary_use_but_is_bounded(self):
        for n in range(3): self.spend(str(n), 3600+n)
        self.assertFalse(policy.admission(self.store, self.payload, 17, self.now)['allowed'])
        self.store.put('square:directed:10:22', {'at': self.now})
        self.assertTrue(policy.admission(self.store, self.payload, 17, self.now)['allowed'])
        self.spend('four', 4000, True)
        self.spend('five', 4001, True)
        self.assertFalse(policy.admission(self.store, self.payload, 15, self.now)['allowed'])

    def test_unknown_delivery_uses_capacity_rejected_write_does_not(self):
        self.spend('one', 60, status='uncertain')
        self.spend('two', 30, status='uncertain')
        self.assertFalse(policy.admission(self.store, self.payload, 20, self.now)['allowed'])
        with self.store.db: self.store.db.execute("UPDATE outbound SET status='rejected' WHERE id='two'")
        self.assertTrue(policy.admission(self.store, self.payload, 20, self.now)['allowed'])

    def test_stale_live_sample_cannot_spend_reserved_daily_slots(self):
        # Eleven earlier comments plus another caller's newly reserved twelfth;
        # the stale platform sample still says nine remain, but only eight do.
        self.now += 20*3600
        for n in range(11): self.spend('earlier'+str(n), 7*3600+n)
        self.spend('concurrent', 1)
        self.assertFalse(policy.admission(self.store,self.payload,9,self.now)['allowed'])
        self.store.put('square:directed:10:22',{'at':self.now})
        self.assertTrue(policy.admission(self.store,self.payload,9,self.now)['allowed'])

    def test_age_survives_whitespace_copy_request_and_parent_changes(self):
        old = self.now - 13*3600
        policy.remember_candidate(self.store, self.payload['body'], old, self.now)
        api = Square(self.store)
        with patch('custos_square.time.time', return_value=self.now), patch.object(api, 'validate') as validate:
            with self.assertRaisesRegex(APIError, 'square_candidate_expired'):
                api.write('new-id', 'comment', dict(self.payload, parent_id=99, body=' a  concrete finding\n'), composed_at=self.now)
            validate.assert_not_called()
        self.assertEqual(policy.remember_candidate(self.store, self.payload['body'], self.now, self.now), old)

    def test_deferral_retains_age_without_creating_outbound_reservation(self):
        api = Square(self.store)
        with patch('custos_square.time.time', return_value=self.now), patch.object(api, 'validate'), \
             patch.object(api, 'get', return_value={'handle':'custos','today':{'comments_remaining':8}}), \
             patch.object(api, 'request') as request:
            with self.assertRaisesRegex(APIError, 'square_pacing_deferred'):
                api.write('deferred', 'comment', self.payload)
            request.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbound').fetchone()[0], 0)
        self.assertEqual(self.store.get(policy.body_key(self.payload['body']))['first_seen'], self.now)

    def test_shared_adapter_stops_third_new_write_and_reconciles_old_receipt(self):
        api = Square(self.store)
        with patch('custos_square.time.time', return_value=self.now), patch.object(api, 'validate'), \
             patch.object(api, 'get', return_value={'handle':'custos','today':{'comments_remaining':20}}), \
             patch.object(api, 'request', return_value=({'comment_id':1},None)) as request, \
             patch.object(api, 'receipt', return_value={'status':'delivered'}):
            api.write('direct', 'comment', dict(self.payload, body='one'))
            api.write('traj:native', 'comment', dict(self.payload, body='two'))
            with self.assertRaisesRegex(APIError, 'square_pacing_deferred'):
                api.write('third', 'comment', dict(self.payload, body='three'))
            api.write('direct', 'comment', dict(self.payload, body='one'))
            self.assertEqual(request.call_count, 2)

    def test_attention_recovers_with_time_and_thread_cooldown_is_separate(self):
        policy.record_attention(self.store, 'signals', self.now, 10)
        self.assertEqual(policy.attention(self.store, self.now)['pressure'], .5)
        self.assertFalse(policy.select_discovery(self.store, 10, self.now+1))
        policy.record_attention(self.store, 'signals', self.now+1, 11)
        self.assertTrue(policy.attention(self.store, self.now+2)['satiated'])
        self.assertFalse(policy.select_discovery(self.store, 12, self.now+2))
        self.assertTrue(policy.select_discovery(self.store, 12, self.now+policy.WINDOW+2))
        self.assertFalse(policy.select_discovery(self.store, 10, self.now+policy.WINDOW+2))
        self.assertTrue(policy.select_discovery(self.store, 10, self.now+86400))

    def test_explicit_browsing_and_exhaustion_satiate_without_blocking_posts(self):
        for n in range(12): policy.record_attention(self.store, 'reads', self.now+n)
        self.assertTrue(policy.attention(self.store, self.now+12)['satiated'])
        self.store.put('square:allowance', {'today': {'comments_remaining':0,'posts_remaining':1,
            'interval': {'until':(self.now+86400)*1000}}})
        self.assertTrue(policy.attention(self.store, self.now+policy.WINDOW+30)['satiated'])
        api = Square(self.store)
        with patch('custos_square.time.time', return_value=self.now), patch.object(api, 'validate'), \
             patch.object(api, 'get', return_value={'handle':'custos','today':{'comments_remaining':0,'posts_remaining':1}}), \
             patch.object(api, 'request', return_value=({'post_id':1},None)) as request, \
             patch.object(api, 'receipt', return_value={'status':'delivered'}):
            api.write('post', 'post', {'title':'artifact','body':'synthesis'})
            request.assert_called_once()

    def test_post_nudge_counts_only_confirmed_original_posts(self):
        self.spend('a-comment', 1)
        self.assertTrue(policy.policy_summary(self.store,self.now)['post_nudge_due'])
        with self.store.db:
            self.store.db.execute('INSERT INTO outbound VALUES (?,?,?,?,?,?)', ('post','post','{}','uncertain',None,self.now))
        self.assertTrue(policy.policy_summary(self.store,self.now)['post_nudge_due'])
        with self.store.db: self.store.db.execute("UPDATE outbound SET status='delivered' WHERE id='post'")
        self.assertFalse(policy.policy_summary(self.store,self.now)['post_nudge_due'])

    def test_backpressure_is_quiet_not_an_outage(self):
        native=NativeFixture(); observer=Observer({},self.store,None,native,now=self.now)
        def deferred(): raise APIError('square_pacing_deferred', 1800)
        observer.source('square-outbox',60,deferred)
        self.assertEqual(native.messages,{})
        self.assertEqual(self.store.get('source:square-outbox')['next'],self.now+1800)

    def test_discovery_burst_is_disposed_without_backlog_and_directed_remains_live(self):
        items=[{'id':n,'post_id':n,'author':'peer','body':'memory finding'} for n in range(1,7)]
        class Feed:
            def request(self,*a,**kw):
                return {'posts':[], 'comments':items, 'has_more_streams':[], 'continuation_covers':[],
                    'next_posts_since':'next','next_comments_since':'next','next_nulls_since':'done','has_more':False},None
        native=NativeFixture(); observer=Observer({'square_interests':{'terms':['memory']}},self.store,Feed(),native,now=self.now)
        observer.changes()
        self.assertEqual(len(native.messages),2)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM seen WHERE disposition='attention_skipped'").fetchone()[0],4)
        self.assertEqual(self.store.get('changes:cursor')['comments_since'],'next')
        self.assertTrue(observer.directed({'request_id':'direct','sender':'square:peer:10:22',
            'source_url':'https://1f916.ai/api/comment/22','content':'specific new question'}))
        self.assertTrue(policy.directed(self.store,self.payload,self.now))

    def test_concurrent_direct_and_native_reservations_cannot_overspend_burst(self):
        self.spend('prior', 10)
        barrier=threading.Barrier(2); results=[]; directory=self.tmp.name
        class Concurrent(Square):
            def validate(self,*a): pass
            def get(self,*a,**kw):
                barrier.wait(timeout=10)
                return {'handle':'custos','today':{'comments_remaining':20}}
            def request(self,*a,**kw): return {'comment_id':1},None
            def receipt(self,identity): return {'status':'delivered'}
        def worker(name):
            store=Store(directory)
            try:
                Concurrent(store).write(name,'comment',dict(self.payload,body=name))
                results.append('delivered')
            except APIError as exc: results.append(exc.code)
            finally: store.db.close()
        with patch('custos_square.time.time',return_value=self.now):
            threads=[threading.Thread(target=worker,args=(name,)) for name in ('direct','traj:native')]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=15)
        self.assertCountEqual(results,['delivered','square_pacing_deferred'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbound').fetchone()[0],2)

    def test_shortlist_overflow_is_retained_as_withdrawn_not_delivered(self):
        fixture=observation_fixtures.SquareOutboxTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        for step in ('queue001','queue002','queue003','queue004'): fixture.compose(step)
        fixture.store.put('outbox:cursor',{'path':str(fixture.traj),'offset':0})
        api=fixture.square(20); observer=fixture.observer(api)
        with patch('custos_square.time.time',return_value=fixture.now):
            observer.source('square-outbox',60,observer.outbox)
        self.assertEqual(len(api.posted),2)
        self.assertEqual(fixture.store.get('outbox:skip:queue004')['reason'],'square_shortlist_full')
        self.assertNotIn('delivery:queue004',fixture.native.messages)

    def test_seed_pipeline_preserves_other_sources_and_limits_square(self):
        step=Path(__file__).resolve().parents[2]/'runtime/headlong/thinkers/monolith/step'
        source=step.read_text(); start=source.index('_seed_lines=$('); end=source.index('if [[ -n "$_seed_lines"',start)
        import datetime
        stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
        rows=[{'type':'observation','source':'custos-observe','ts':stamp,'source_url':url,'content':'fresh discovery'}
              for url in ['https://example.org/paper','https://example.net/repo']+
                  ['https://1f916.ai/api/comment/'+str(n) for n in range(10)]]
        fixture=Path(self.tmp.name)/'seeds.jsonl'; fixture.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        for open_square, expected in [('yes',1),('no',0)]:
            script='IDENTITY_NAME=custos\n_square_seed_open='+open_square+'\n_root_traj_raw_tail() { cat "$1"; }\n'
            # Function receives the fixture through an explicit shell argument.
            script='fixture=$1\n'+script.replace('cat "$1"','cat "$fixture"')+source[start:end]+'\nprintf "%s" "$_seed_lines"\n'
            result=subprocess.run(['bash','-c',script,'fixture',str(fixture)],text=True,capture_output=True,check=True)
            self.assertEqual(result.stdout.count('https://1f916.ai/'),expected,result.stderr)
            self.assertIn('https://example.org/paper',result.stdout)
            self.assertIn('https://example.net/repo',result.stdout)


if __name__ == '__main__': unittest.main()
