import json,pathlib,tempfile,unittest,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from harness.controller import Controller
from harness.common import atomic_json,overlay_action
A='a'*64;B='b'*64;C='c'*40
class Fake:
 version='fixed-1'
 def __init__(self):self.selected=A;self.pause=False;self.fail=None;self.calls=[];self.receipts=['already-delivered'];self.memory=['retained']
 def step(self,n):
  self.calls.append(n)
  if self.fail==n:raise RuntimeError('injected '+n)
 def paused(self):return self.pause
 def qualify(self,a):self.step('qualify');return {'artifact':a}
 def preflight(self,*a):self.step('preflight')
 def drain(self,r):self.step('drain')
 def abort_drain(self,r):self.step('abort-drain')
 def stop(self,r):self.step('stop')
 def switch(self,a,r):self.selected=a;self.step('switch');return {'commit':C}
 def verify(self,a,r):self.step('verify');self.receipts.append('new-delivery');self.memory.append('new-memory')
 def restore(self,a,r):self.selected=a;self.step('restore')
class TestController(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.f=Fake();self.c=Controller(self.tmp.name,self.f);atomic_json(self.c.state/'current.json',{'artifact':A,'commit':C})
 def tearDown(self):self.tmp.cleanup()
 def request(self,action='qualify',rid='q',**kw):
  p={'action':action,'request_id':rid,'artifact':B};p.update(kw);return self.c.handle(p)
 def ready(self):self.request();self.c.process_one()
 def deploy(self):self.request('deploy','d',expected_current=A);self.c.process_one();return self.c.handle({'action':'status','request_id':'d'})['request']
 def test_success_and_duplicate(self):
  self.ready();self.assertEqual(self.deploy()['phase'],'committed');self.assertTrue(self.request('deploy','d',expected_current=A)['replayed']);self.assertFalse(self.c.process_one());self.assertEqual(self.f.calls.count('switch'),1)
 def test_id_collision(self):
  self.request()
  with self.assertRaises(ValueError):self.request(artifact=A)
 def test_cas(self):
  self.ready();atomic_json(self.c.state/'current.json',{'artifact':B});self.assertEqual(self.deploy()['phase'],'failed');self.assertNotIn('drain',self.f.calls)
 def test_unqualified(self):self.assertEqual(self.deploy()['phase'],'failed')
 def test_failed_canary(self):
  self.ready();self.f.fail='verify';self.assertEqual(self.deploy()['phase'],'rolled-back');self.assertEqual(self.f.selected,A)
 def test_stuck_start(self):
  self.ready();self.f.fail='switch';self.assertEqual(self.deploy()['phase'],'rolled-back')
 def test_failed_migration_preflight(self):
  self.ready();self.f.fail='preflight';self.assertEqual(self.deploy()['phase'],'failed');self.assertNotIn('stop',self.f.calls)
 def test_pause_before(self):
  self.ready();self.f.pause=True;self.assertEqual(self.deploy()['phase'],'failed');self.assertNotIn('drain',self.f.calls)
 def test_pause_mid(self):
  self.ready();orig=self.f.verify
  def verify(a,r):orig(a,r);self.f.pause=True
  self.f.verify=verify;self.assertEqual(self.deploy()['phase'],'paused-rolled-back');self.assertTrue(self.f.pause);self.assertEqual(self.f.receipts,['already-delivered','new-delivery']);self.assertIn('new-memory',self.f.memory)
 def test_crash_every_mutating_phase(self):
  for phase in ['stopped','switching','verifying','rolling-back']:
   with self.subTest(phase=phase):
    self.c.handle({'action':'deploy','request_id':phase,'artifact':B,'expected_current':A})
    r=self.c.read(self.c.state/'requests'/(phase+'.json'));self.c.save(r,phase,previous={'artifact':A,'commit':C});self.f.selected=B
    Controller(self.tmp.name,self.f).recover();self.assertEqual(self.f.selected,A);self.assertEqual(self.c.read(self.c.state/'requests'/(phase+'.json'))['phase'],'rolled-back')
 def test_drain_timeout_does_not_kill_active_work(self):
  self.ready();self.f.fail='drain';self.assertEqual(self.deploy()['phase'],'failed');self.assertIn('abort-drain',self.f.calls);self.assertNotIn('stop',self.f.calls);self.assertNotIn('restore',self.f.calls)
 def test_interrupted_drain_resumes_old_release(self):
  self.request('deploy','d',expected_current=A);r=self.c.read(self.c.state/'requests/d.json');self.c.save(r,'draining',previous={'artifact':A,'commit':C})
  Controller(self.tmp.name,self.f).process_one();self.assertIn('abort-drain',self.f.calls);self.assertNotIn('restore',self.f.calls)
 def test_failed_restore_remains_recoverable(self):
  self.request('deploy','d',expected_current=A);r=self.c.read(self.c.state/'requests/d.json');self.c.save(r,'switching',previous={'artifact':A,'commit':C});self.f.fail='restore'
  with self.assertRaises(RuntimeError):self.c.process_one()
  self.assertEqual(self.c.read(self.c.state/'requests/d.json')['phase'],'rolling-back');self.f.fail=None;self.c.process_one();self.assertEqual(self.c.read(self.c.state/'requests/d.json')['phase'],'rolled-back')
 def test_conflict_overlay(self):
  self.assertEqual(overlay_action('a','local','a'),'keep');self.assertEqual(overlay_action('a','a','b'),'replace')
  with self.assertRaises(ValueError):overlay_action('a','local','b')
 def test_no_arbitrary_host_args(self):
  with self.assertRaises(ValueError):self.request(command='shutdown',container=123)
if __name__=='__main__':unittest.main()
