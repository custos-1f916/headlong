"""One-time code-layout migration; retains exact old tree and service backups."""
from pathlib import Path
import os,json,shutil,subprocess,sys,time
sys.path.insert(0,'/usr/local/libexec/custos-harness')
from harness import guest_agent as g
from harness.common import atomic_json,digest
P=Path;S=P('/var/lib/custos-harness');base=json.loads((S/'bootstrap-baseline.json').read_text())['artifact'];rid='layout-bootstrap-20260910-v3'
if P('/opt/custos/current').exists():raise RuntimeError('layout already migrated; inspect instead of rerunning')
# Stage and verify all code before touching live services.
g.stage(base)
# Resolve managed skill overlays against the original repository defaults before drain.
from harness.common import overlay_action
changes=[]
for n,row in g.manifest(base)['files'].items():
 if n.startswith('renewal/identity/skills/') and n.endswith('/SKILL.md'):
  src=P('/opt/custos/repo')/n;dst=g.identity()/n.removeprefix('renewal/identity/')
  if overlay_action(digest(src) if src.exists() else None,digest(dst) if dst.exists() else None,row['sha256'])=='replace':changes.append((n,dst))
backup=S/'bootstrap-backup-v3';backup.mkdir(mode=0o700)
units=['headlong-thinkers@custos.service','custos-observe.service','custos-relay-bridge.service','headlong-web.service']
for n in units:shutil.copy2(P('/etc/systemd/system')/n,backup/n)
links={}
for p in P('/usr/local/bin').iterdir():
 if p.is_symlink() and '/opt/custos/repo/renewal/bin/' in str(p.readlink()):links[str(p)]=str(p.readlink())
atomic_json(backup/'helper-links.json',links)
# May time out safely before any code move. Existing step children finish.
try:g.drain(rid)
except Exception:g.abort_drain(rid);raise
g.stop(rid)
app=P('/root/.headlong/app');old=backup/'app';identities=S/'identities'
try:
 app.rename(old);(old/'.identities').rename(identities);(old/'.identities').symlink_to(identities)
 (P('/opt/custos/current')).symlink_to(g.release(base));app.symlink_to('/opt/custos/current/runtime/headlong')
 (g.release(base)/'runtime/headlong/.identities').unlink(missing_ok=True)
 (g.release(base)/'runtime/headlong/.identities').symlink_to(identities)
 config=P('/var/lib/custos/config');config.mkdir(exist_ok=True)
 for name in ['observations.json','dream.json']:
  q=config/name
  if not q.exists():shutil.copy2('/opt/custos/repo/renewal/'+name,q)
 for path,target in links.items():
  p=P(path);p.unlink();p.symlink_to('/opt/custos/current/renewal/bin/'+p.name)
 # Use the source-defined guest units and an explicit frozen web interpreter.
 for n in units[:3]:shutil.copy2(g.release(base)/'renewal/systemd'/n,P('/etc/systemd/system')/n)
 web='''[Unit]
Description=Custos Headlong dashboard (selected release)
After=network-online.target
[Service]
Type=simple
WorkingDirectory=/opt/custos/current/runtime/headlong/web
Environment=PYTHONPATH=/opt/custos/current/runtime/headlong/web/src:/opt/custos/current/runtime/headlong/web/.runtime/site-packages
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/custos/current/runtime/headlong/web/.runtime/python/bin/python3.14 /usr/local/libexec/custos-dashboard-current.py
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
 (P('/etc/systemd/system')/'headlong-web.service').write_text(web)
 subprocess.run(['systemctl','daemon-reload'],check=True)
 # stop() can clear pending; restore the checkpoint before starting under gate.
 q=g.checkpoint_dir(rid)/'pending'
 if q.exists():shutil.copytree(q,g.identity()/'run/pending',dirs_exist_ok=True)
 g.health(base,rid)
 for n,dst in changes:
  dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(g.release(base)/n,dst)
 helper=P('/usr/local/bin/custos-harness');helper.symlink_to('/opt/custos/current/renewal/bin/custos-harness')
 atomic_json(S/'layout.json',{'baseline':base,'previous_tree':str(old),'identity':str(identities),'at':time.time()})
 print(json.dumps({'ok':True,'baseline':base,'identity':str(identities)}))
except BaseException:
 # Restore old code/layout only. Identity has continued forward and is retained.
 try:
  g.stop(rid)
  if app.is_symlink():app.unlink()
  if (old/'.identities').is_symlink():(old/'.identities').unlink()
  if identities.exists():identities.rename(old/'.identities')
  if old.exists():old.rename(app)
  P('/opt/custos/current').unlink(missing_ok=True)
  for path,target in links.items():
   p=P(path);p.unlink(missing_ok=True);p.symlink_to(target)
  for n in units:shutil.copy2(backup/n,P('/etc/systemd/system')/n)
  subprocess.run(['systemctl','daemon-reload'],check=True)
  g.MAINT.unlink(missing_ok=True)
  if not g.paused():subprocess.run(['systemctl','start',*g.WRITERS,g.TIMER],check=True)
 finally:raise
