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
 def test_urlopen_raises_surfaces_as_three(self):
  """The core behavioral regression: a raw URLError becomes exit 3, not a traceback."""
  def boom(*a,**k):raise urllib.error.URLError('no route to host')
  with mock.patch.object(client.urllib.request,'urlopen',side_effect=boom):
   code,stdout,stderr=self.call(['current'])
  self.assertEqual(code,3);self.assertIn('unreachable',stderr);self.assertNotIn('Traceback',stderr)
if __name__=='__main__':unittest.main()
