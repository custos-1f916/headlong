"""Guest interface to the host-owned harness supervisor.

Exit codes:
  0  supervisor answered ok (HTTP 200, no error / ok:True)
  1  supervisor answered ok:False (a logical failure, e.g. a bad request)
  2  usage error (argparse)
  3  transport or build error (supervisor unreachable, timeout, HTTP error,
     non-JSON body, non-object JSON body, or the local build step failed)
"""
import argparse,json,os,socket,sys,urllib.error,urllib.request

DEFAULT_URL='http://192.168.86.44:18082/v1/harness'
DEFAULT_TIMEOUT='15'
class HarnessTransportError(Exception):
 """Supervisor could not be reached, or did not answer in a clean shape."""

def request(payload,url=None,timeout=None):
 """POST payload to the supervisor and return the parsed JSON object.

 Raises HarnessTransportError on any transport-level failure so callers can
 tell 'the supervisor said no' (ok:False) from 'we could not reach it at all'.
 """
 url=url or os.environ.get('CUSTOS_HARNESS_URL',DEFAULT_URL)
 timeout=timeout if timeout is not None else os.environ.get('CUSTOS_HARNESS_TIMEOUT',DEFAULT_TIMEOUT)
 req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=float(timeout)) as r:body=r.read()
 except urllib.error.HTTPError as e:
  raise HarnessTransportError('http %d %s'%(e.code,e.reason))
 except urllib.error.URLError as e:
  raise HarnessTransportError('supervisor unreachable: %s'%e.reason)
 except (socket.timeout,TimeoutError):
  raise HarnessTransportError('timed out after %ss'%timeout)
 try:out=json.loads(body)
 except json.JSONDecodeError as e:
  raise HarnessTransportError('non-JSON response body: %s'%e)
 if not isinstance(out,dict):raise HarnessTransportError('non-object JSON response body: got %s'%type(out).__name__)
 return out

def live_subruns(proc='/proc'):
 """PIDs of nested shellm sub-runs alive in this guest (their prompt file is the subrun temp file).

 A deploy or rollback drains the mind between steps and then stops it; a sub-run
 that is mid-task dies with it and the parent's next wake starts over (Custos,
 2026-09-11 16:19Z). The CLI refuses those two actions while a sub-run is alive
 unless --allow-live-subrun is given.
 """
 found=[]
 try:entries=os.listdir(proc)
 except OSError:return found
 for pid in entries:
  if not pid.isdigit():continue
  try:
   with open(os.path.join(proc,pid,'cmdline'),'rb') as f:cmd=f.read().split(b'\0')
  except OSError:continue
  if any(a.startswith(b'/tmp/subrun.') or b'/subrun.' in a for a in cmd) and any(b'shellm' in a for a in cmd):found.append(int(pid))
 return found

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
 s.add_parser('current');q=s.add_parser('status');q.add_argument('request_id',nargs='?')
 for name in ['qualify','deploy','rollback']:
  q=s.add_parser(name);q.add_argument('artifact');q.add_argument('--request-id',required=True);q.add_argument('--expected-current',required=name!='qualify')
  if name!='qualify':q.add_argument('--allow-live-subrun',action='store_true',help='drain even though a sub-run is mid-task (it will be killed)')
 q=s.add_parser('build');q.add_argument('--repo',default='/opt/custos/repo');q.add_argument('--commit',default='HEAD')
 a=p.parse_args(argv)
 if a.command in('deploy','rollback') and not getattr(a,'allow_live_subrun',False):
  live=live_subruns()
  if live:
   print('harness: %s refused: %d live sub-run(s) (pid %s) would be killed by the drain and their task lost. Wait for them, read their report files, or pass --allow-live-subrun.'%(a.command,len(live),', '.join(map(str,live))),file=sys.stderr);return 1
 if a.command=='build':
  from harness.build import build
  try:out=build(a.repo,a.commit)
  except Exception as e:print('harness: build: %s'%e,file=sys.stderr);return 3
 else:
  payload={'action':a.command};payload.update({k:v for k,v in vars(a).items() if k not in('command','allow_live_subrun') and v is not None})
  try:out=request(payload)
  except HarnessTransportError as e:print('harness: %s'%e,file=sys.stderr);return 3
 print(json.dumps(out,indent=2));return 0 if out.get('ok',True) else 1
if __name__=='__main__':raise SystemExit(main())
