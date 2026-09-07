#!/usr/bin/env python3
"""Quiesce the retired persona and archive its complete runtime privately."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
root = Path('/var/backups/custos-renewal') / stamp
root.mkdir(parents=True, mode=0o700)
os.chmod(root.parent, 0o700)
units = subprocess.check_output(['systemctl', 'list-unit-files', 'custos*', 'headlong*', '--no-legend', '--no-pager'], text=True)
(root / 'units.before.txt').write_text(units)
names = [line.split()[0] for line in units.splitlines() if line.split() and line.split()[0].endswith(('.service', '.timer'))]
if names:
    subprocess.run(['systemctl', 'stop', *[n for n in names if n.endswith('.timer')]], check=True)
    subprocess.run(['systemctl', 'stop', *[n for n in names if n.endswith('.service')]], check=True)
    subprocess.run(['systemctl', 'disable', *names], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
for name in names:
    state = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True).stdout.strip()
    if state in ('active', 'activating', 'deactivating', 'reloading'):
        raise RuntimeError('Old actor has not stopped: ' + name)
keys = ['/etc/custos.env', '/etc/custos-systemd.env', '/opt/custos/ed25519.key', '/opt/custos/ed25519-openssh.key']
manifest = {}
for name in keys:
    p = Path(name)
    if not p.is_file():
        raise RuntimeError('Required identity material missing: ' + name)
    manifest[name] = {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'mode': oct(p.stat().st_mode & 0o777)}
(root / 'identity-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
paths = ['/root/.headlong', '/root/.local/bin', '/root/.ssh', '/root/.pi', '/root/.skills', '/opt/custos', '/etc/cron.d', '/var/spool/cron/crontabs/root']
paths += [str(p) for p in Path('/etc').glob('custos*')]
paths += [str(p) for p in Path('/etc/systemd/system').iterdir() if p.name.startswith(('custos', 'headlong'))]
archive = root / 'retired-persona.tar.gz'
with tarfile.open(archive, 'w:gz', compresslevel=1, dereference=False) as out:
    for name in sorted(set(paths)):
        p = Path(name)
        if p.exists() or p.is_symlink():
            out.add(p, arcname=name.lstrip('/'), recursive=True)
os.chmod(archive, 0o600)
with tarfile.open(archive, 'r:gz') as inp:
    members = {m.name for m in inp.getmembers()}
    for name in keys:
        if name.lstrip('/') not in members:
            raise RuntimeError('Archive missing identity material: ' + name)
sha = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
(root / 'retired-persona.tar.gz.sha256').write_text(sha + '  retired-persona.tar.gz\n')
for name in names:
    p = Path('/etc/systemd/system') / name
    if p.exists() or p.is_symlink():
        p.unlink()
    p.symlink_to('/dev/null')
subprocess.run(['systemctl', 'daemon-reload'], check=True)
cron = Path('/var/spool/cron/crontabs/root')
if cron.exists():
    lines = cron.read_text().splitlines()
    kept = [line for line in lines if not any(marker in line for marker in ['/opt/custos', '.headlong', 'persona custos'])]
    if kept != lines:
        subprocess.run(['crontab', '-'], input='\n'.join(kept) + '\n', text=True, check=True)
print(json.dumps({'archive': str(archive), 'sha256': sha, 'bytes': archive.stat().st_size, 'identity_files': len(manifest), 'retired_units': names}), flush=True)
