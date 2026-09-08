#!/usr/bin/env python3
"""Operator-installed deploy runner, executes ONLY inside Automata LXC126.

No network credentials. Archives contain only the particle-life app. Species
persist outside releases. Candidate checks precede an atomic application switch;
a failed post-switch health check restores the prior release and service.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request

BASE=Path('/srv/custos-automata')
APP=Path('/srv/terrarium-pl')
DATA=Path('/var/lib/automata/species')
STATE=BASE/'state.json'
ARCHIVE=Path('/var/lib/custos-automata-incoming/source.tar')


def atomic(path, value):
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,indent=2)+'\n'); os.replace(tmp,path)


def run(args, timeout=60):
    # Deployment/test logs stay inside the target container, out of model replies.
    with (BASE/'deploy.log').open('ab') as log:
        subprocess.run(args,stdout=log,stderr=log,check=True,timeout=timeout)


def extract(archive, destination):
    """Reject links, special files, traversal, persisted data and tar bombs."""
    with tarfile.open(archive,mode='r:') as source:
        members=source.getmembers()
        if len(members)>2000 or sum(m.size for m in members)>32*1024*1024:
            raise ValueError('archive limit')
        seen=set()
        for m in members:
            parts=PurePosixPath(m.name).parts
            if (not parts or m.name.startswith('/') or '..' in parts or '\\' in m.name or
                parts[0] in ('species','.git','.env') or m.name in seen or
                not (m.isdir() or m.isfile())):
                raise ValueError('unsafe archive entry')
            seen.add(m.name)
        for m in members:
            target=destination.joinpath(*PurePosixPath(m.name).parts)
            if m.isdir(): target.mkdir(parents=True,exist_ok=True); continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with source.extractfile(m) as src, target.open('xb') as dst:
                shutil.copyfileobj(src,dst)
            target.chmod(0o755 if m.mode & 0o111 else 0o644)
    for name in ('server.js','index.html','sim.js','sim.test.js'):
        if not (destination/name).is_file(): raise ValueError('missing particle-life entrypoint/test')


def health(port=8080):
    for route in ('/','/sim.js','/api/species'):
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{route}',timeout=3) as response:
            body=response.read(1048577)
            if response.status!=200 or not body or len(body)>1048576:
                raise RuntimeError('health failed')
            if route=='/api/species' and not isinstance(json.loads(body).get('species'),list):
                raise RuntimeError('species health failed')
    return True


def wait_healthy(port=8080):
    for _ in range(10):
        try:
            if health(port): return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError('HTTP health failed')


def switch(target):
    tmp=APP.with_name(APP.name+'.next')
    if tmp.is_symlink(): tmp.unlink()
    tmp.symlink_to(target); os.replace(tmp,APP)


def initialize():
    BASE.mkdir(parents=True,exist_ok=True)
    if STATE.exists(): return
    if APP.is_symlink() or not APP.is_dir(): raise RuntimeError('unexpected legacy application path')
    # Preserve legacy bytes and state, including the service's exact original unit.
    if DATA.exists(): raise RuntimeError('unexpected existing species data; reconcile migration')
    run(['systemctl','stop','terrarium.service'])
    try:
        DATA.parent.mkdir(parents=True,exist_ok=True)
        if (APP/'species').exists(): shutil.copytree(APP/'species',DATA)
        else: DATA.mkdir()
        legacy=BASE/'legacy-20260908'
        shutil.copytree(APP,legacy)
        shutil.copy2('/etc/systemd/system/terrarium.service',BASE/'terrarium.original.service')
        # Original tree is retained as an additional untouched rollback archive.
        old=BASE/'original-20260908'
        APP.rename(old)
        shutil.rmtree(legacy/'species')
        (legacy/'species').symlink_to(DATA)
        switch(legacy)
        atomic(STATE,{'current':str(legacy),'previous':None,'commit':None,'previous_commit':None})
    finally:
        run(['systemctl','start','terrarium.service'])
    wait_healthy()


def status():
    state=json.loads(STATE.read_text()) if STATE.exists() else {'current':str(APP),'commit':None,'previous':None}
    actual=str(APP.resolve())
    consistent=actual==state['current']
    try: healthy=health()
    except Exception: healthy=False
    return {**state,'recorded_commit':state['commit'], 'commit':state['commit'] if consistent else None,
            'actual_path':actual,'state_consistent':consistent,'healthy':healthy,'service_active':subprocess.run(
        ['systemctl','is-active','--quiet','terrarium.service']).returncode==0}


def activate(target, commit):
    old=json.loads(STATE.read_text())
    try:
        switch(target)
        run(['systemctl','restart','terrarium.service'])
        wait_healthy()
    except Exception:
        switch(Path(old['current']))
        run(['systemctl','restart','terrarium.service'])
        wait_healthy()
        return {'ok':False,'rolled_back':True,**status()}
    atomic(STATE,{'current':str(target),'previous':old['current'],'commit':commit,'previous_commit':old['commit']})
    return {'ok':True,**status()}


def deploy(commit, digest):
    if not re.fullmatch(r'[0-9a-f]{40}',commit) or not re.fullmatch(r'[0-9a-f]{64}',digest):
        raise ValueError('invalid commit/digest')
    if ARCHIVE.stat().st_size>32*1024*1024 or hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()!=digest:
        raise ValueError('source archive mismatch')
    state=json.loads(STATE.read_text())
    if str(APP.resolve())!=state['current']:
        raise RuntimeError('interrupted switch; reconcile current path and deployment state first')
    if state['commit']==commit and health(): return {'ok':True,'unchanged':True,**status()}
    release=BASE/'releases'/commit
    release.parent.mkdir(parents=True,exist_ok=True)
    if release.exists():
        if not (release/'.source-sha256').is_file() or (release/'.source-sha256').read_text()!=digest:
            raise RuntimeError('release exists with different or incomplete source; inspect it')
    else:
        release.mkdir(); extract(ARCHIVE,release)
        (release/'species').symlink_to(DATA)
        (release/'.source-sha256').write_text(digest)
    run(['/usr/bin/node','--check',str(release/'server.js')])
    run(['/usr/bin/node',str(release/'sim.test.js')])
    # A separate systemd cgroup prevents a canary child leaking after a check.
    try:
        run(['systemd-run','--unit=custos-automata-canary','--collect',
             '--property=WorkingDirectory='+str(release),'--setenv=PORT=18083',
             '/usr/bin/node',str(release/'server.js')])
        wait_healthy(18083)
    finally:
        run(['systemctl','stop','custos-automata-canary.service'])
    return activate(release,commit)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['initialize','status','deploy','rollback'])
    parser.add_argument('commit',nargs='?');parser.add_argument('digest',nargs='?');args=parser.parse_args()
    if subprocess.check_output(['hostname'],text=True).strip()!='automata': raise RuntimeError('wrong container')
    BASE.mkdir(parents=True,exist_ok=True)
    with (BASE/'deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            if args.action=='initialize': initialize(); result={'ok':True,**status()}
            elif args.action=='status': result=status()
            elif args.action=='deploy': result=deploy(args.commit or '',args.digest or '')
            else:
                state=json.loads(STATE.read_text())
                if not state['previous']: raise ValueError('no previous release')
                result=activate(Path(state['previous']),state['previous_commit'])
        except Exception as error:
            result={'ok':False,'error':type(error).__name__,'detail':str(error)[:200],**status()}
        print(json.dumps(result))

if __name__=='__main__': main()
