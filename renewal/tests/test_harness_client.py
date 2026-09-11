"""Exit-code and error-surface contract for the guest harness CLI.

Drives the real request()/main() through an in-process HTTP server (no live
supervisor needed) and a monkeypatched urlopen, so it runs hermetically.
"""
import contextlib,http.server,io,json,os,socket,sys,threading,unittest
import urllib.error
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness import client

class Handler(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  status,payload=self.server.response
  data=json.dumps(payload).encode() if isinstance(payload,dict) else payload
  self.send_response(status);self.send_header('Content-Type','application/json')
  self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def log_message(self,*a):pass

class ClientTests(unittest.TestCase):
 def setUp(self):
  self.srv=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
  self.url='http://127.0.0.1:%d/v1/harness'%self.srv.server_address[1]
  threading.Thread(target=self.srv.serve_forever,daemon=True).start()
 def tearDown(self):
  self.srv.shutdown();self.srv.server_close()
 def call(self,args,url=None):
  out=io.StringIO();err=io.StringIO()
  with mock.patch.dict(os.environ,{'CUSTOS_HARNESS_URL':url or self.url},clear=False),\
       contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
   code=client.main(args)
  return code,out.getvalue(),err.getvalue()
 def dead_port(self):
  s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close();return port
 def test_ok_true_exit_zero(self):
  self.srv.response=(200,{'ok':True,'current':{'artifact':'a'*40}})
  code,stdout,stderr=self.call(['current'])
  self.assertEqual(code,0);self.assertEqual(json.loads(stdout),{'ok':True,'current':{'artifact':'a'*40}});self.assertEqual(stderr,'')
 def test_ok_false_exit_one(self):
  self.srv.response=(200,{'ok':False,'error':'bad request id'})
  code,stdout,stderr=self.call(['status','nope'])
  self.assertEqual(code,1);self.assertEqual(json.loads(stdout)['error'],'bad request id')
 def test_http_error_exit_three_no_traceback(self):
  self.srv.response=(500,{'ok':False,'error':'boom'})
  code,stdout,stderr=self.call(['current'])
  self.assertEqual(code,3);self.assertIn('harness:',stderr);self.assertNotIn('Traceback',stderr);self.assertEqual(stdout,'')
 def test_unreachable_exit_three_no_traceback(self):
  code,stdout,stderr=self.call(['current'],url='http://127.0.0.1:%d/v1/harness'%self.dead_port())
  self.assertEqual(code,3);self.assertIn('unreachable',stderr);self.assertNotIn('Traceback',stderr);self.assertEqual(stdout,'')
 def test_non_json_body_exit_three(self):
  self.srv.response=(200,b'not-json')
  code,stdout,stderr=self.call(['current'])
  self.assertEqual(code,3);self.assertIn('non-JSON',stderr);self.assertNotIn('Traceback',stderr)
 def test_non_object_json_body_exit_three_no_traceback(self):
  for body in (b'[]', b'null'):
   self.srv.response=(200,body)
   code,stdout,stderr=self.call(['current'])
   self.assertEqual(code,3);self.assertIn('non-object',stderr);self.assertNotIn('Traceback',stderr);self.assertEqual(stdout,'')
 def test_urlopen_raises_surfaces_as_three(self):
  """Core transport regression, old-compatible: patched sys.argv + mocked urlopen, main() called with no argv. On the original client this errors with the raw URLError escaping main(); on the new client it is a clean exit 3."""
  def boom(*a,**k):raise urllib.error.URLError('no route to host')
  out=io.StringIO();err=io.StringIO()
  with mock.patch.object(client.urllib.request,'urlopen',side_effect=boom),\
       mock.patch.object(sys,'argv',['custos-harness','current']),\
       contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
   code=client.main()
  self.assertEqual(code,3)
  self.assertIn('unreachable',err.getvalue())
  self.assertNotIn('Traceback',err.getvalue())
  self.assertEqual(out.getvalue(),'')
 # --- deploy/rollback refuse while a sub-run is alive (2026-09-11: a drain killed one mid-task) ---
 def test_deploy_refused_while_subrun_alive_and_no_request_sent(self):
  self.srv.response=(200,{'ok':True,'accepted':True})
  hits=[]
  orig=client.request
  def spy(payload,url=None,timeout=None):hits.append(payload);return orig(payload,url,timeout)
  with mock.patch.object(client,'live_subruns',return_value=[4242,4243]),mock.patch.object(client,'request',side_effect=spy):
   code,stdout,stderr=self.call(['deploy','a'*64,'--request-id','r1','--expected-current','b'*64])
  self.assertEqual(code,1);self.assertIn('refused',stderr);self.assertIn('4242',stderr);self.assertIn('--allow-live-subrun',stderr)
  self.assertEqual(hits,[]);self.assertEqual(stdout,'')
 def test_allow_live_subrun_sends_without_the_flag_in_payload(self):
  self.srv.response=(200,{'ok':True,'accepted':True})
  hits=[]
  orig=client.request
  def spy(payload,url=None,timeout=None):hits.append(payload);return orig(payload,url,timeout)
  with mock.patch.object(client,'live_subruns',return_value=[4242]),mock.patch.object(client,'request',side_effect=spy):
   code,stdout,stderr=self.call(['rollback','a'*64,'--request-id','r2','--expected-current','b'*64,'--allow-live-subrun'])
  self.assertEqual(code,0);self.assertEqual(len(hits),1);self.assertNotIn('allow_live_subrun',hits[0]);self.assertEqual(hits[0]['action'],'rollback')
 def test_qualify_never_checks_subruns(self):
  self.srv.response=(200,{'ok':True,'accepted':True})
  with mock.patch.object(client,'live_subruns',side_effect=AssertionError('must not be called')):
   code,stdout,stderr=self.call(['qualify','a'*64,'--request-id','q1'])
  self.assertEqual(code,0)
 def test_live_subruns_reads_proc_cmdlines(self):
  import tempfile
  with tempfile.TemporaryDirectory() as proc:
   for pid,cmd in(('101',b'bash\0/x/bin/shellm\0--prompt-file\0/tmp/subrun.Ab12\0'),('102',b'bash\0/x/bin/shellm\0--prompt-file\0/state/monolith_prompt.x\0'),('103',b'python3\0/tmp/subrun.Zz\0'),('notpid',b'')):
    os.mkdir(os.path.join(proc,pid));open(os.path.join(proc,pid,'cmdline'),'wb').write(cmd)
   self.assertEqual(client.live_subruns(proc),[101])
if __name__=='__main__':unittest.main()
