#!/usr/bin/env python3
"""Configure existing Custos GitHub credentials in LXC122, never grant permissions.

Reads the preserved PAT privately and verifies account identity. No token is
printed, passed on argv, or exported to model processes. Missing repository
grants are recorded as blockers; this helper never invites or grants access.
"""
from pathlib import Path
import subprocess,json,os,shlex,urllib.request,urllib.error,stat
assert subprocess.check_output(['hostname'],text=True).strip()=='custos'
p=Path('/etc/custos-github.env');assert p.stat().st_mode&0o777==0o600
line=next(l for l in p.read_text().splitlines() if l.startswith('CUSTOS_GITHUB_TOKEN='));parts=shlex.split(line.split('=',1)[1]);assert len(parts)==1;token=parts[0]
def api(path):
 req=urllib.request.Request('https://api.github.com/'+path,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'Custos-account-configuration'})
 with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
user=api('user');assert user['login']=='custos-1f916' and user['id']==320211121
permissions={}
for name in ['collettiquette/custos','collettiquette/voidle']:

 try:
  data=api('repos/'+name);permissions[name]=data['permissions']
 except urllib.error.HTTPError as e:
  assert e.code==404
  permissions[name]={'pull':False,'push':False,'blocker':'Account has no repository grant; operator permission pending'}
os.umask(0o077)
subprocess.run(['gh','auth','login','--hostname','github.com','--git-protocol','https','--with-token'],input=token+'\n',text=True,check=True,capture_output=True)
subprocess.run(['gh','auth','setup-git','--hostname','github.com'],check=True,capture_output=True)
for root in ['/opt/custos/repo','/opt/custos/work/voidle']:
 for path in [root]+[l[9:] for l in subprocess.check_output(['git','-C',root,'worktree','list','--porcelain'],text=True).splitlines() if l.startswith('worktree ')]:
  repo='custos' if root.endswith('/repo') else 'voidle'
  for args in [['config','--unset-all','core.sshCommand'],['config','user.name','Custos'],['config','user.email','320211121+custos-1f916@users.noreply.github.com'],['remote','set-url','origin','https://github.com/collettiquette/'+repo+'.git']]:
   result=subprocess.run(['git','-C',path,*args],capture_output=True);assert result.returncode==0 or args[1]=='--unset-all'
 # Read access through the standard helper, no env token and no deploy-key fallback.
 if permissions['collettiquette/'+repo]['pull']:
  subprocess.run(['git','-C',root,'ls-remote','--exit-code','origin','HEAD'],check=True,capture_output=True)
subprocess.run(['git','config','--global','user.name','Custos'],check=True)
subprocess.run(['git','config','--global','user.email','320211121+custos-1f916@users.noreply.github.com'],check=True)
proof={'login':user['login'],'id':user['id'],'permissions':permissions,'git_protocol':'https','credential_helper':'gh auth git-credential','commit_email':'320211121+custos-1f916@users.noreply.github.com','token_in_global_environment':False}
Path('/var/lib/custos-qualification/continuation').mkdir(parents=True, exist_ok=True)
Path('/var/lib/custos-qualification/continuation/github-account.json').write_text(json.dumps(proof,indent=2));print(json.dumps(proof))
