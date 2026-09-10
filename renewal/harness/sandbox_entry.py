"""Trusted child entrypoint. Only runs inside the disposable VM."""
import base64,hashlib,json,os,pathlib,selectors,shutil,signal,subprocess,sys,tarfile,time,threading,socket
P=pathlib.Path
MAX_LOG=8*1024*1024;MAX_ARTIFACT=512*1024*1024

def emit(kind,**fields):
    print(json.dumps({'kind':kind,**fields},ensure_ascii=True),flush=True)

def fingerprint(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink() and p.stat().st_size<16*1024*1024}

def bridge():
    # The sole network path is a parent-controlled Unix socket. The parent
    # implements either a bounded model route or a dependency-host allowlist.
    def handle(client):
        try:
            remote=socket.create_connection(('10.0.2.100',18080),timeout=650)
            with client,remote,selectors.DefaultSelector() as sel:
                sel.register(client,selectors.EVENT_READ,remote);sel.register(remote,selectors.EVENT_READ,client)
                while True:
                    ready=sel.select(650)
                    if not ready:return
                    for k,_ in ready:
                        b=k.fileobj.recv(65536)
                        if not b:return
                        k.data.sendall(b)
        except OSError:pass
    server=socket.socket();server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);server.bind(('127.0.0.1',18080));server.listen(16)
    while True:
        c,_=server.accept();threading.Thread(target=handle,args=(c,),daemon=True).start()

def main():
    spec=json.loads(P('/spec.json').read_text());os.umask(0o077)
    shutil.copytree('/input','/work',dirs_exist_ok=True,symlinks=True)
    before=fingerprint(P('/work'))
    if spec['profile']!='offline':threading.Thread(target=bridge,daemon=True).start()
    # Private Git metadata preserves a useful HEAD for runtime/version probes,
    # never points a worktree back into the real source repository.
    if spec.get('git_head'):
        subprocess.run(['git','init','-q','/work'],check=True)
        subprocess.run(['git','-C','/work','config','user.email','fixture@localhost'],check=True)
        subprocess.run(['git','-C','/work','config','user.name','Sealed fixture'],check=True)
        subprocess.run(['git','-C','/work','add','.'],check=True)
        subprocess.run(['git','-C','/work','commit','-qm','Sealed source '+spec['git_head']],check=True)
    os.chdir('/work')
    env={'HOME':'/home/test','XDG_CONFIG_HOME':'/home/test/.config','XDG_DATA_HOME':'/home/test/.local/share','XDG_CACHE_HOME':'/home/test/.cache','PATH':'/work/renewal/bin:/work/runtime/headlong/bin:/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','HEADLONG_APP_DIR':'/home/test/app'}
    if spec['profile']=='fetch':env.update(HTTPS_PROXY='http://127.0.0.1:18080',https_proxy='http://127.0.0.1:18080')
    if spec['profile']=='model':
        env.update(OPENAI_BASE_URL='http://127.0.0.1:18080/v1',OPENAI_API_KEY='sealed-fixture',
                   LLM_BASE_URL='http://127.0.0.1:18080/v1',SHELLM_MODEL='qwen3.8-27b',SHELLM_EFFORT='medium')
    env.update(spec.get('test_env',{}))
    start=time.monotonic();size=0;reason='exit';rc=125
    proc=subprocess.Popen(spec['argv'],cwd='/work/'+spec.get('subdir',''),env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    with selectors.DefaultSelector() as sel:
        sel.register(proc.stdout,selectors.EVENT_READ)
        while sel.get_map():
            if time.monotonic()-start>spec['seconds']:
                os.killpg(proc.pid,signal.SIGKILL);reason='timeout';break
            for key,_ in sel.select(1):
                chunk=os.read(key.fileobj.fileno(),32768)
                if not chunk:sel.unregister(key.fileobj);continue
                size+=len(chunk)
                if size>MAX_LOG:
                    os.killpg(proc.pid,signal.SIGKILL);reason='output-limit';break
                emit('log',data=base64.b64encode(chunk).decode())
            if reason!='exit':break
    try:rc=proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait();reason='descendant-timeout'
    # A command's exit does not entitle its background descendants to survive.
    try:os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    changed=[];total=0;omitted=0
    for root,label in [(P('/work'),'work'),(P('/home/test'),'home')]:
        for p in sorted(root.rglob('*')):
            if not p.is_file() or p.is_symlink() or '.git' in p.relative_to(root).parts:continue
            rel=str(p.relative_to(root));length=p.stat().st_size
            if length>16*1024*1024:omitted+=1;continue
            if label=='work' and before.get(rel)==hashlib.sha256(p.read_bytes()).hexdigest():continue
            if total+length>MAX_ARTIFACT:omitted+=1;continue
            changed.append((p,label+'/'+rel));total+=length
    bundle=P('/tmp/artifacts.tar.gz')
    with tarfile.open(bundle,'w:gz') as tar:
        for p,n in changed:tar.add(p,arcname=n,recursive=False)
    with bundle.open('rb') as f:
        while chunk:=f.read(49152):emit('artifact',data=base64.b64encode(chunk).decode())
    emit('result',exit_code=rc if reason=='exit' else 124,reason=reason,seconds=round(time.monotonic()-start,3),artifact_files=len(changed),artifact_omitted=omitted,source_commit=spec.get('git_head'))
    return 0
if __name__=='__main__':raise SystemExit(main())
