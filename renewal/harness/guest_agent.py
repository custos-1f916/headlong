"""Fixed guest-side actuator, copied by the host before each operation.

No candidate installer, migrations or arbitrary shell is accepted. Durable identity
is never extracted from an artifact or restored from a checkpoint.
"""
import hashlib,json,os,pathlib,shutil,signal,subprocess,sys,tarfile,time,urllib.request
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from harness.common import HEX,digest,atomic_json,extract_archive,validate_archive,overlay_action
P=pathlib.Path;S=P('/var/lib/custos-harness');CURRENT=P('/opt/custos/current')
IDENTITY=P('/var/lib/custos-harness/identities/custos');MAINT=P('/var/lib/custos/harness-maintenance')
MIND='headlong-thinkers@custos.service';WRITERS=[MIND,'custos-observe.service','custos-relay-bridge.service','headlong-web.service'];TIMER='custos-observe.timer'

def call(argv,timeout=60,check=True):return subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout,check=check)
def paused():return P('/var/lib/custos/operator-paused').exists()
def identity():return IDENTITY if IDENTITY.exists() else P('/root/.headlong/app/.identities/custos')
def release(a):
 if not isinstance(a,str) or not HEX.fullmatch(a):raise ValueError('invalid artifact')
 return S/'releases'/a

def manifest(a):return json.loads((release(a)/'release.json').read_text())
def installed():
 if not CURRENT.is_symlink():raise RuntimeError('release layout has not been bootstrapped')
 target=CURRENT.resolve();a=target.name
 if target!=release(a):raise RuntimeError('unexpected release pointer')
 return a

def checkpoint_dir(rid):
 import re
 if not isinstance(rid,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',rid):raise ValueError('invalid request id')
 p=S/'checkpoints'/rid;p.mkdir(parents=True,exist_ok=True,mode=0o700);return p

def overlays(a,apply=False,force_previous=None):
 old=manifest(installed());new=manifest(a);live=identity();actions=[]
 managed=set(n for n in set(old['files'])|set(new['files']) if n.startswith('renewal/identity/skills/') and n.endswith('/SKILL.md'))
 for n in sorted(managed):
  rel=n.removeprefix('renewal/identity/');p=live/rel
  previous=old['files'].get(n,{}).get('sha256');candidate=new['files'].get(n,{}).get('sha256');current=digest(p) if p.exists() else None
  action=overlay_action(previous,current,candidate)
  if action=='replace':
   actions.append((n,p,candidate))
 if apply:
  for n,p,candidate in actions:
   if candidate is None:p.unlink(missing_ok=True)
   else:p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(release(a)/n,p)
 return [n for n,p,candidate in actions]

def stage(a,repair=False):
 path=S/'artifacts'/(a+'.tar.gz');actual,m=validate_archive(path,a);r=release(a)
 if r.exists():
  # Do not trust a directory left from a killed extraction or guest edits.
  for n,row in m['files'].items():
   p=r/n
   if not p.is_file() or p.is_symlink() or digest(p)!=row['sha256']:
    if not repair:raise ValueError('staged release differs from artifact: '+n)
    quarantine=S/'quarantine';quarantine.mkdir(exist_ok=True);r.rename(quarantine/(a+'-'+str(time.time_ns())));extract_archive(path,r,a);break
 else:extract_archive(path,r,a)
 return m

def qualification(a):
 r=release(a);m=stage(a)
 expected=P(__file__).resolve().parents[1]/'toolchain-manifest.json'
 if expected.exists():
  meta=json.loads(expected.read_text())
  for name in ['root.ext4','vmlinuz','initrd']:
   if digest(S/'toolchain'/name)!=meta[name]:raise ValueError('test toolchain differs from host qualification baseline')
 # The trusted contract suite is not supplied by the candidate.
 canary=r/'.controller'
 if canary.exists():shutil.rmtree(canary)
 shutil.copytree(P(__file__).with_name('contracts'),canary)
 from harness.seal import run
 result=run(r,['python3','-m','unittest','discover','-s','.controller','-p','test_*.py'],seconds=600,keep=False,test_env={'HEADLONG_ROOT':'/work/runtime/headlong','CUSTOS_RENEWAL_DIR':'/work/renewal','PYTHONPATH':'/work/renewal'})
 if result['exit_code']!=0:raise RuntimeError('fixed contract suite failed: '+result['job'])
 tests=run(r,['python3','-m','unittest','discover','-s','renewal/tests','-p','test_*.py'],seconds=600,keep=False,test_env={'HEADLONG_ROOT':'/work/runtime/headlong','CUSTOS_RENEWAL_DIR':'/work/renewal','PYTHONPATH':'/work/renewal'})
 if tests['exit_code']!=0:raise RuntimeError('candidate regression suite failed: '+tests['job'])
 shutil.rmtree(canary)
 return {'artifact':a,'commit':m['commit'],'contracts':result['job'],'candidate_tests':tests['job'],'state_version':m['state_version']}

def preflight(a,expected):
 if installed()!=expected:raise ValueError('guest current release differs from expected')
 stage(a);overlays(a)
 if paused():raise ValueError('operator pause active')
 if shutil.disk_usage(S).free<2*1024**3:raise ValueError('less than 2GiB disk headroom')
 return {'ok':True}

def alive(pid):
 try:return P('/proc') .joinpath(str(pid),'stat').read_text().rsplit(')',1)[1].split()[0]!='Z'
 except (OSError,ValueError,IndexError):return False

def drain(rid):
 c=checkpoint_dir(rid);atomic_json(MAINT,{'request_id':rid,'since':time.time()})
 call(['systemctl','stop',TIMER])
 call(['systemctl','stop','headlong-web.service'],timeout=100)
 # Intake is durable at the upstream relays; stop pulling new work, but let
 # current observer writes finish. Observer is bounded; never kill it mid-write.
 deadline=time.monotonic()+660
 while time.monotonic()<deadline:
  if call(['systemctl','show','-p','ActiveState','--value','custos-observe.service']).stdout.strip() in {'inactive','failed'}:break
  time.sleep(1)
 else:raise TimeoutError('observer did not drain')
 call(['systemctl','stop','custos-relay-bridge.service'])
 run=identity()/'run';pid=int((run/'dispatcher.pid').read_text())
 # Freeze only the dispatcher. Its active step children can finish normally.
 if alive(pid):os.kill(pid,signal.SIGSTOP)
 atomic_json(c/'dispatcher.json',{'pid':pid})
 while time.monotonic()<deadline:
  entries=(run/'step_pids').read_text().splitlines() if (run/'step_pids').exists() else []
  if not any(alive(int(x.split()[0])) for x in entries if x.strip()):break
  time.sleep(1)
 else:raise TimeoutError('in-flight thinker did not drain')
 q=c/'pending'
 if not q.exists():shutil.copytree(run/'pending',q) if (run/'pending').exists() else q.mkdir()
 atomic_json(c/'schedules.json',{p.name:p.read_text() for p in run.glob('*.wake_at')})
 # Integrity evidence excludes mutable code overlays and run scratch files.
 protected={}
 for name in ['.env','info.txt','activate']:
  p=identity()/name
  if p.is_file():protected[name]=digest(p)
 atomic_json(c/'protected.json',protected)
 # Consistent checkpoint for operator recovery; controller never rewinds it.
 backup=c/'identity-state.tar.gz'
 if not backup.exists():
  with tarfile.open(backup,'w:gz') as t:
   for name in ['memories','.state','social-policy.json']:
    p=identity()/name
    if p.exists():t.add(p,arcname=name,recursive=True)
 return {'ok':True,'checkpoint':str(c)}

def abort_drain(rid):
 pid=int(call(['systemctl','show','-p','MainPID','--value',MIND]).stdout.strip() or '0')
 if pid>1 and alive(pid):os.kill(pid,signal.SIGCONT)
 MAINT.unlink(missing_ok=True)
 call(['systemctl','start','headlong-web.service'])
 if not paused():call(['systemctl','start','custos-relay-bridge.service',TIMER])
 return {'ok':True}

def stop(rid):
 c=checkpoint_dir(rid)
 # Dequeue nothing during maintenance. systemd sends TERM/KILL independently
 # of a stopped dispatcher; SIGCONT lets its shutdown trap finish promptly.
 call(['systemctl','stop','--no-block',*WRITERS])
 try:
  pid=int(call(['systemctl','show','-p','MainPID','--value',MIND]).stdout.strip() or '0')
  if pid>1 and alive(pid):os.kill(pid,signal.SIGCONT)
 except (FileNotFoundError,ProcessLookupError):pass
 deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  if all(call(['systemctl','show','-p','ActiveState','--value',n]).stdout.strip() in {'inactive','failed'} for n in WRITERS):break
  time.sleep(1)
 else:raise TimeoutError('guest services did not stop')
 return {'ok':True}

def switch(a,rid):
 stage(a);overlays(a,apply=True)
 link=S/'next';link.unlink(missing_ok=True);link.symlink_to(release(a));os.replace(link,CURRENT)
 r=release(a)/'runtime/headlong';p=r/'.identities'
 if not p.is_symlink():p.symlink_to(S/'identities')
 c=checkpoint_dir(rid);run=identity()/'run';q=c/'pending'
 if q.exists():shutil.copytree(q,run/'pending',dirs_exist_ok=True)
 for n,v in json.loads((c/'schedules.json').read_text()).items() if (c/'schedules.json').exists() else []:
  if '/' in n or not n.endswith('.wake_at'):raise ValueError('invalid saved schedule')
  (run/n).write_text(v)
 return manifest(a)

def start(rid):
 c=checkpoint_dir(rid)
 for n,v in json.loads((c/'protected.json').read_text()).items() if (c/'protected.json').exists() else []:
  if digest(identity()/n)!=v:raise RuntimeError('identity integrity changed: '+n)
 call(['systemctl','reset-failed',*WRITERS],check=False)
 call(['systemctl','start','headlong-web.service'])
 if paused():return {'ok':True,'paused':True}
 # Start under the dispatcher maintenance gate, restore pending triggers before
 # allowing any thinker to run, then restart durable intake.
 call(['systemctl','start',MIND])
 MAINT.unlink(missing_ok=True)
 call(['systemctl','start','custos-relay-bridge.service',TIMER])
 return {'ok':True}

def health(a,rid):
 if installed()!=a:raise RuntimeError('release pointer changed during probation')
 c=checkpoint_dir(rid);trajectory=identity()/'trajectories/7ed4c8d8-root/trajectory.jsonl'
 offset=trajectory.stat().st_size if trajectory.exists() else 0
 start(rid)
 if paused():raise RuntimeError('operator pause during startup')
 deadline=time.monotonic()+660;runs=set();reasoned=set();progress=None;buffer=b''
 while time.monotonic()<deadline:
  if paused():raise RuntimeError('operator pause during probation')
  if call(['systemctl','is-active',MIND],check=False).returncode:raise RuntimeError('mind service not active')
  if trajectory.exists():
   with trajectory.open('rb') as f:f.seek(offset);chunk=f.read(4*1024*1024);offset=f.tell()
   buffer+=chunk
   if len(buffer)>8*1024*1024:raise RuntimeError('trajectory probation record bound')
   lines=buffer.split(b'\n');buffer=lines.pop()
   for line in lines:
    try:event=json.loads(line)
    except ValueError:raise RuntimeError('invalid durable trajectory row')
    if event.get('type')=='shellm-run' and event.get('launched_by')=='monolith':runs.add(event.get('step_id'))
    run_id=event.get('run_id')
    if run_id in runs and event.get('type')=='reasoning':reasoned.add(run_id)
    if run_id in reasoned and event.get('type')=='shell-output' and event.get('exit')==0:
     progress={'run_id':run_id,'step_id':event.get('step_id')};break
  if progress:break
  time.sleep(2)
 if not progress:raise TimeoutError('no successful model/tool progress during probation')
 with urllib.request.urlopen('http://127.0.0.1:8080/',timeout=10) as r:
  if r.status!=200:raise RuntimeError('dashboard not healthy')
 for name in ['custos-relay-bridge.service',TIMER]:
  if call(['systemctl','is-active',name],check=False).returncode:raise RuntimeError(name+' not active')
 return {'ok':True,'wake_progress':progress,'dashboard':True}

def restore(a,rid):
 atomic_json(MAINT,{'request_id':rid,'recovery':True})
 stop(rid);stage(a,repair=True)
 # Before restoring overlays, reverse from the failed release to the retained
 # previous defaults; preserve edits that do not conflict. Conflicts keep the
 # gate closed for operator repair instead of silently overwriting local work.
 switch(a,rid);start(rid)
 return {'ok':True,'paused':paused()}

def main():
 p=json.load(sys.stdin);action=p['action'];a=p.get('artifact');rid=p.get('request_id')
 if action=='inspect':return {'current':installed(),'paused':paused()}
 if action=='qualify':
  import contextlib
  log=S/('qualification-'+a+'.log')
  with log.open('w') as f,contextlib.redirect_stdout(f),contextlib.redirect_stderr(f):return qualification(a)
 if action=='preflight':return preflight(a,p['expected_current'])
 if action=='drain':return drain(rid)
 if action=='abort-drain':return abort_drain(rid)
 if action=='stop':return stop(rid)
 if action=='switch':return switch(a,rid)
 if action=='verify':return health(a,rid)
 if action=='restore':return restore(a,rid)
 raise ValueError('unsupported actuator action')
if __name__=='__main__':
 try:print(json.dumps(main()))
 except Exception as e:print(json.dumps({'error':str(e)[:1000]}));sys.exit(1)
