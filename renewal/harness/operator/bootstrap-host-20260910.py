from pathlib import Path
import subprocess,json,sys,time,fcntl
sys.path.insert(0,'/opt/custos-harness')
from harness.common import atomic_json
from harness.host_service import Adapter
P=Path;S=P('/var/lib/custos-harness');rid='layout-bootstrap-20260910-v3'
q=json.loads((S/'requests/merged-release-v3.json').read_text())
assert q['phase']=='qualified'
a=q['payload']['artifact'];adapter=Adapter();assert q['controller']==adapter.version
assert not (S/'current.json').exists()
assert not P('/etc/custos-gateway/paused').exists()
record=S/'bootstrap'/ (rid+'.json')
atomic_json(record,{'request_id':rid,'artifact':a,'phase':'prepared','created':time.time()})
adapter.install()
atomic_json(S/'maintenance',{'request_id':rid,'record':str(record)})
try:
 with open('/run/custos-harness-intake.lock','a') as gate:fcntl.flock(gate,fcntl.LOCK_EX)
 atomic_json(record,{'request_id':rid,'artifact':a,'phase':'transitioning','updated':time.time()})
 subprocess.run(['pct','exec','122','--','python3','-c','from pathlib import Path;Path("/var/lib/custos-harness/bootstrap-baseline.json").write_text('+repr(json.dumps({'artifact':a}))+')'],check=True)
 subprocess.run(['pct','push','122','/tmp/custos-harness-layout-bootstrap.py','/tmp/custos-harness-layout-bootstrap.py'],check=True)
 p=subprocess.run(['pct','exec','122','--','systemd-run','--quiet','--wait','--pipe','--collect','--unit','custos-harness-layout-bootstrap-v3','-p','RuntimeMaxSec=1500','python3','/tmp/custos-harness-layout-bootstrap.py'],timeout=1530)
 if p.returncode:raise RuntimeError('guest bootstrap failed')
 atomic_json(S/'current.json',{'artifact':a,'commit':q['evidence']['commit'],'installed':time.time(),'request_id':rid})
 atomic_json(record,{'request_id':rid,'artifact':a,'phase':'committed','updated':time.time()})
 (S/'maintenance').unlink()
 print('Selected release healthy; host intake resumed; bootstrap receipt retained')
except BaseException:
 check=subprocess.run(['pct','exec','122','--','python3','-c','from pathlib import Path;import subprocess;assert not Path("/opt/custos/current").exists();subprocess.run(["systemctl","is-active","--quiet","headlong-thinkers@custos.service","headlong-web.service","custos-relay-bridge.service"],check=True)'])
 if check.returncode==0:
  (S/'maintenance').unlink(missing_ok=True)
  atomic_json(record,{'request_id':rid,'artifact':a,'phase':'old-layout-restored','updated':time.time()})
 else:atomic_json(record,{'request_id':rid,'artifact':a,'phase':'recovery-required','updated':time.time()})
 raise
