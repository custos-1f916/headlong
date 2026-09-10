"""Guest interface to the host-owned harness supervisor."""
import argparse,json,pathlib,urllib.request,uuid,sys

def request(payload):
 req=urllib.request.Request('http://192.168.86.44:18082/v1/harness',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)

def main():
 p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
 s.add_parser('current');q=s.add_parser('status');q.add_argument('request_id',nargs='?')
 for name in ['qualify','deploy','rollback']:
  q=s.add_parser(name);q.add_argument('artifact');q.add_argument('--request-id',required=True);q.add_argument('--expected-current',required=name!='qualify')
 q=s.add_parser('build');q.add_argument('--repo',default='/opt/custos/repo');q.add_argument('--commit',default='HEAD')
 a=p.parse_args()
 if a.command=='build':
  from harness.build import build
  out=build(a.repo,a.commit)
 else:
  payload={'action':a.command};payload.update({k:v for k,v in vars(a).items() if k!='command' and v is not None});out=request(payload)
 print(json.dumps(out,indent=2));return 0 if out.get('ok',True) else 1
if __name__=='__main__':raise SystemExit(main())
