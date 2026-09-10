"""Dream safety and lifecycle behavior; temporary identities, no inference/network."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import sys
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import custos_dream as d
import custos_memory as cm
from test_memory import MemoryFixture

class DreamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=cm.Store(self.root/'memories')
        self.env=mock.patch.dict(os.environ,{'TRAJ_ID':'','IDENTITY_DIR':str(self.root)})
        self.env.start();self.addCleanup(self.env.stop)
        self.now=dt.datetime(2026,9,10,9,30,tzinfo=dt.timezone.utc)
        self.d=d.Dream(self.store,clock=lambda:self.now)
        self.put('11111111','Fact A');self.put('22222222','Fact B')
    def put(self,key,body,kind='fact'):
        p=self.store.directory/(key+'.md')
        p.write_text('---\nid: '+key+'\ntype: '+kind+'\ncreated: 2026-09-09 00:00:00\nsummary: Fact\n---\n\n'+body+'\n')
        return d.sha(p.read_bytes())
    def digest(self,key):return d.sha(self.store.find(key)[0].read_bytes())
    def test_window_and_once_per_local_day(self):
        self.now-=dt.timedelta(seconds=1);self.assertEqual(self.d.due(),'')
        self.now+=dt.timedelta(seconds=1);self.assertIn('dream',self.d.due())
        self.d.begin();self.d.finish('Partial is honest');self.assertEqual(self.d.due(),'')
        self.now+=dt.timedelta(days=1);self.assertIn('dream',self.d.due())
    def test_dst_uses_denver_calendar(self):
        self.now=dt.datetime(2026,11,1,10,30,tzinfo=dt.timezone.utc)
        self.assertTrue(self.d.window());self.assertEqual(self.d.day(),'2026-11-01')
        self.d.begin();self.d.finish('done');self.assertEqual(self.d.due(),'')
    def test_disabled_and_missed_window_do_not_start(self):
        self.d.config['enabled']=False;self.assertEqual(self.d.due(),'')
        self.d.config['enabled']=True;self.now+=dt.timedelta(hours=2)
        self.assertEqual(self.d.due(),'');self.assertFalse(self.d.session_path().exists())
        with self.assertRaises(ValueError):self.d.begin()
    def test_expiry_after_window_and_across_midnight_leaves_original_report(self):
        self.d.begin();day=self.d.day();self.now+=dt.timedelta(days=1,hours=2)
        self.assertEqual(self.d.due(),'')
        old=d.read_json(self.d.root/day/'session.json');self.assertEqual(old['status'],'expired')
        self.assertTrue((self.d.root/day/'report.md').exists());self.assertFalse(self.d.session_path().exists())
    def test_deadline_prevents_edit(self):
        self.d.begin();self.now+=dt.timedelta(minutes=20)
        with self.assertRaises(ValueError):self.d.revise('11111111',self.digest('11111111'),'B','source')
        self.d.due();self.assertEqual(d.read_json(self.d.session_path())['status'],'expired')
    def test_cas_preserves_concurrent_change(self):
        self.d.begin();old=self.digest('11111111');self.put('11111111','New source')
        with self.assertRaises(ValueError):self.d.revise('11111111',old,'Lost update','source')
        self.assertEqual(self.store.find('11111111')[2],'New source')
        self.assertEqual(list((self.d.root/self.d.day()/'changes').glob('*')),[])
    def test_revision_backup_and_keep_uncertain_rotation(self):
        self.d.begin();p=self.store.find('11111111')[0];raw=p.read_bytes()
        e=self.d.revise('11111111',d.sha(raw),'Verified fact','artifact X')
        self.assertEqual(Path(e['backup']).read_bytes(),raw)
        self.assertEqual(Path(e['backup']).stat().st_mode&0o777,0o600)
        self.d.review('11111111',e['after_sha256'],'revised','artifact X')
        self.d.review('22222222',self.digest('22222222'),'uncertain','conflicting sources')
        self.assertEqual(self.d.finish('Both reviewed')['status'],'complete')
        self.now+=dt.timedelta(days=1);self.put('33333333','New question')
        self.assertEqual(self.d.begin()['selected'][0]['id'],'33333333')
    def test_protected_identity_values_and_goals(self):
        self.put('33333333','Value','value');self.put('44444444','Active objective','objective')
        meta={'person_key':'hal','display':'Hal','aliases':[],'routes':['hal']}
        self.put('55555555','Person: Hal\n\nNotes'+cm.PERSON_MARKER+json.dumps(meta),'person')
        self.d.begin()
        for key in ['33333333','44444444','55555555']:
            with self.assertRaises(ValueError):self.d.revise(key,self.digest(key),None,'source',True,'11111111')
        with self.assertRaises(ValueError):self.d.revise('33333333',self.digest('33333333'),'New value','source')
        meta['routes']=['attacker']
        with self.assertRaises(ValueError):self.d.revise('55555555',self.digest('55555555'),'Person: Hal'+cm.PERSON_MARKER+json.dumps(meta),'source')
    def test_archive_requires_canonical_and_still_readable(self):
        self.d.begin();before=self.digest('11111111')
        with self.assertRaises(ValueError):self.d.revise('11111111',before,None,'duplicate',True)
        e=self.d.revise('11111111',before,None,'same fact as 22222222',True,'22222222')
        self.assertFalse(Path(e['source']).exists());self.assertTrue(Path(e['target']).exists())
        self.d.review('11111111',e['after_sha256'],'archived','canonical 22222222')
    def test_crash_after_write_reconciles_and_consumes_budget(self):
        self.d.config['max_edits']=1;self.d.begin();real=d.write_json
        def crash(path,value):
            if path.parent.name=='changes' and value.get('status')=='applied':raise OSError('simulated power loss')
            return real(path,value)
        with mock.patch.object(d,'write_json',side_effect=crash):
            with self.assertRaises(OSError):self.d.revise('11111111',self.digest('11111111'),'Changed','source')
        active=self.d.active();self.assertEqual(len(active['edits']),1)
        self.assertEqual(d.read_json(Path(active['edits'][0]))['status'],'applied')
        with self.assertRaises(ValueError):self.d.revise('22222222',self.digest('22222222'),'Extra','source')
        self.d.review('11111111',self.digest('11111111'),'revised','source')
    def test_prepared_not_applied_is_not_revision_evidence(self):
        self.d.begin()
        with mock.patch.object(d,'atomic',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.d.revise('11111111',self.digest('11111111'),'Changed','source')
        with self.assertRaises(ValueError):self.d.review('11111111',self.digest('11111111'),'revised','source')

class LifecycleTests(MemoryFixture):
    def test_native_edit_waits_for_reviewer_lock(self):
        key=self.store.commit("Original fact",memory_type="fact")
        with self.store.lock():
            proc=subprocess.Popen([self.native_mem,"--dir",str(self.store.directory),"edit",key,"Fresh fact"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):proc.communicate(timeout=0.2)
                self.assertEqual(self.store.find(key)[2],"Original fact")
            except BaseException:
                proc.kill();proc.communicate();raise
        stdout,stderr=proc.communicate(timeout=5)
        self.assertEqual(proc.returncode,0,stderr)
        self.assertEqual(self.store.find(key)[2],"Fresh fact")

    def age(self,key):
        with self.store.lock():
            item=self.store.find(key);item[4]['received_at']='2026-01-01T00:00:00+00:00';self.store.save(item,item[4])
    def test_only_untouched_ambient_settles(self):
        keys=[]
        for i in range(4):
            p={**self.payload,'request_id':str(i)}
            if i!=1:p['ambient']=True
            key=self.store.capture(p)['goal_id'];keys.append(key);self.age(key)
        with self.store.lock():
            item=self.store.find(keys[2]);item[4]['responder_attempt']={'state':'failed'};self.store.save(item,item[4])
        item=self.store.find(keys[3]);item[4]['response']={'state':'sent','plan':{'decision':'defer','goal':item[4]['goal']}};self.store.save(item,item[4])
        self.assertEqual(self.store.settle_ambient(),1)
        self.assertEqual(self.store.find(keys[0])[4]['status'],'completed')
        self.assertTrue(all(self.store.find(k)[4]['status']=='active' for k in keys[1:]))
        self.assertEqual(self.store.settle_ambient(),0)
    def test_fresh_ambient_is_not_expired(self):
        self.store.capture({**self.payload,'ambient':True})
        self.assertEqual(self.store.settle_ambient(),0)
    def test_structured_origin_status_and_archive_protected(self):
        key=self.store.capture(self.payload)['goal_id']
        dream=d.Dream(self.store,clock=lambda:dt.datetime(2026,9,10,9,30,tzinfo=dt.timezone.utc))
        # Ordinary unattempted directed receipt is a conversation; flag explicitly
        # by using a real deferred goal, just as production's semantic batch does.
        item=self.store.find(key);item[4]['response']={'state':'sent','plan':{'decision':'defer','goal':item[4]['goal']}};self.store.save(item,item[4])
        dream.begin();item=self.store.find(key);body=item[2];digest=d.sha(item[0].read_bytes())
        with self.assertRaises(ValueError):dream.revise(key,digest,body.replace('agent','operator'),'bad source')
        with self.assertRaises(ValueError):dream.revise(key,digest,None,'old',True,'11111111')
        e=dream.revise(key,digest,'Current evidence incomplete; retain historical receipt.\n\n'+body,'audit')
        self.assertEqual(self.store.find(key)[4],item[4])

if __name__=='__main__':unittest.main()
