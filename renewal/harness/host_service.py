"""LXC122-only supervisor. Invoked by systemd on blink1, outside Custos."""
import fcntl,hashlib,json,os,pathlib,shutil,socketserver,subprocess,sys,threading,time,tarfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from harness.controller import Controller
from harness.common import digest,validate_archive,atomic_json,MAX_ARCHIVE
P=pathlib.Path;STATE=P('/var/lib/custos-harness');CODE=P('/opt/custos-harness');GUEST='/usr/local/libexec/custos-harness'

def command(args,timeout=60,**kw):return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=True,**kw)
class Adapter:
 def __init__(self):
  self.version=digest(CODE/'actuator.tar.gz')+':'+digest(P(__file__).with_name('controller.py'))+':'+digest(CODE/'toolchain-manifest.json')
  (STATE/'artifacts').mkdir(parents=True,exist_ok=True)
 def paused(self):return P('/etc/custos-gateway/paused').exists() or self.act('inspect').get('paused',False)
 def install(self):
  command(['pct','push','122',str(CODE/'actuator.tar.gz'),'/tmp/custos-harness-actuator.tar.gz'])
  command(['pct','exec','122','--','mkdir','-p',GUEST])
  command(['pct','exec','122','--','tar','-xzf','/tmp/custos-harness-actuator.tar.gz','-C',GUEST])
 def act(self,action,**fields):
  p=subprocess.run(['pct','exec','122','--','python3',GUEST+'/harness/guest_agent.py'],input=json.dumps({'action':action,**fields}).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=1500)
  try:result=json.loads(p.stdout)
  except Exception:raise RuntimeError('guest actuator returned invalid data')
  if 'error' in result:raise RuntimeError(result['error'])
  if p.returncode:raise RuntimeError('guest actuator failed')
  return result
 def receive(self,a):
  path=STATE/'artifacts'/(a+'.tar.gz')
  if path.exists():validate_archive(path,a);return path
  remote='/var/lib/custos-harness/artifacts/'+a+'.tar.gz'
  # Stat is only a preliminary bound; stream copying enforces it on the host.
  size=int(command(['pct','exec','122','--','stat','-c','%s',remote]).stdout)
  if not 0<size<=MAX_ARCHIVE or shutil.disk_usage(STATE).free<3*MAX_ARCHIVE:raise ValueError('artifact size/disk bound')
  temp=path.with_suffix('.partial');proc=subprocess.Popen(['pct','exec','122','--','cat',remote],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
  total=0;timer=threading.Timer(120,proc.kill);timer.start()
  try:
   with temp.open('wb') as f:
    while b:=proc.stdout.read(1024*1024):
     total+=len(b)
     if total>MAX_ARCHIVE:raise ValueError('artifact stream too large')
     f.write(b)
    f.flush();os.fsync(f.fileno())
   if proc.wait(timeout=30):raise RuntimeError('artifact transfer failed')
   validate_archive(temp,a);os.replace(temp,path)
  finally:
   timer.cancel()
   if proc.poll() is None:proc.kill();proc.wait()
   temp.unlink(missing_ok=True)
  return path
 def supply(self,a):
  p=STATE/'artifacts'/(a+'.tar.gz');validate_archive(p,a)
  command(['pct','push','122',str(p),'/var/lib/custos-harness/artifacts/'+a+'.tar.gz'],timeout=120)
 def qualify(self,a):
  self.receive(a);self.install();self.supply(a)
  return self.act('qualify',artifact=a)
 def preflight(self,a,current,evidence):
  if evidence.get('artifact')!=a:raise ValueError('qualification mismatch')
  self.install();self.supply(a);self.supply(current['artifact'])
  return self.act('preflight',artifact=a,expected_current=current['artifact'])
 def drain(self,r):
  atomic_json(STATE/'maintenance',{'request_id':r['request_id']})
  # Wait for existing host-to-guest native writes. The flag prevents new ones.
  with open('/run/custos-harness-intake.lock','a') as lock:
   deadline=time.monotonic()+150
   while True:
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
    except BlockingIOError:
     if time.monotonic()>deadline:raise TimeoutError('host intake did not drain')
     time.sleep(1)
  return self.act('drain',request_id=r['request_id'])
 def abort_drain(self,r):
  result=self.act('abort-drain',request_id=r['request_id']);(STATE/'maintenance').unlink(missing_ok=True);return result
 def stop(self,r):return self.act('stop',request_id=r['request_id'])
 def switch(self,a,r):
  if self.paused():raise RuntimeError('operator pause before switch')
  return self.act('switch',artifact=a,request_id=r['request_id'])
 def verify(self,a,r):
  if self.paused():raise RuntimeError('operator pause before startup')
  result=self.act('verify',artifact=a,request_id=r['request_id']);(STATE/'maintenance').unlink(missing_ok=True);return result
 def restore(self,a,r):
  self.install();self.supply(a)
  # The guest's mirrored pause normally arrives through admission; explicitly
  # retain it if the host pause is already active. Never remove either pause.
  if P('/etc/custos-gateway/paused').exists():command(['pct','exec','122','--','touch','/var/lib/custos/operator-paused'])
  result=self.act('restore',artifact=a,request_id=r['request_id']);(STATE/'maintenance').unlink(missing_ok=True);return result
class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):daemon_threads=True
class Handler(socketserver.StreamRequestHandler):
 def handle(self):
  self.connection.settimeout(15)
  try:
   raw=self.rfile.readline(32769)
   if len(raw)>32768 or not raw.endswith(b'\n'):raise ValueError('bounded JSON line required')
   result=self.server.controller.handle(json.loads(raw))
  except Exception as e:result={'ok':False,'error':str(e)[:300]}
  self.wfile.write(json.dumps(result).encode()+b'\n')
def main():
 os.umask(0o077);adapter=Adapter();controller=Controller(STATE,adapter)
 def worker():
  while True:
   try:
    if not controller.process_one():time.sleep(2)
   except Exception as e:print('harness recovery pending: '+str(e)[:300],flush=True);time.sleep(10)
 threading.Thread(target=worker,daemon=True).start()
 path='/run/custos-harness.sock';P(path).unlink(missing_ok=True)
 with Server(path,Handler) as s:s.controller=controller;s.serve_forever()
if __name__=='__main__':main()
