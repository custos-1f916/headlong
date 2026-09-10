"""Bounded experiment runner; clean environment plus a disposable KVM machine."""
import argparse,base64,contextlib,hashlib,http.client,http.server,ipaddress,json,os,pathlib,re,selectors,shutil,signal,socket,socketserver,subprocess,sys,tempfile,threading,time,uuid
P=pathlib.Path
STATE=P('/var/lib/custos-harness/jobs')
FETCH_HOSTS={'github.com','codeload.github.com','objects.githubusercontent.com','raw.githubusercontent.com','registry.npmjs.org','pypi.org','files.pythonhosted.org'}

class Proxy(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True
class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def setup(self):super().setup();self.connection.settimeout(650)
    def do_CONNECT(self):
        if self.server.profile!='fetch':self.send_error(403);return
        parts=self.path.rsplit(':',1)
        if len(parts)!=2 or parts[0] not in FETCH_HOSTS or parts[1]!='443':self.send_error(403);return
        try:
            addresses=socket.getaddrinfo(parts[0],443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('nonpublic address')
            with socket.create_connection(addresses[0][4][:2],timeout=20) as remote:
                self.send_response(200);self.end_headers()
                with selectors.DefaultSelector() as sel:
                    sel.register(self.connection,selectors.EVENT_READ,remote);sel.register(remote,selectors.EVENT_READ,self.connection)
                    deadline=time.monotonic()+600;size=0
                    while time.monotonic()<deadline:
                        for k,_ in sel.select(1):
                            b=k.fileobj.recv(65536)
                            if not b:return
                            size+=len(b)
                            if size>256*1024*1024:return
                            k.data.sendall(b)
        except (OSError,ValueError):return
    def do_POST(self):
        if self.server.profile!='model' or self.path!='/v1/chat/completions':self.send_error(403);return
        try:
            if self.headers.get('Transfer-Encoding'):raise ValueError('chunked body')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=262144:raise ValueError('size')
            p=json.loads(self.rfile.read(length));p['model']='qwen3.8-27b';p['reasoning_effort']='medium'
            p['max_tokens']=min(int(p.get('max_tokens',4096)),8192);p['stream']=False
            # One bounded call at a time from this disposable run, through the
            # existing host admission. Never accept an arbitrary destination.
            with self.server.model_lock:
                connection=http.client.HTTPConnection('192.168.86.44',18080,timeout=630)
                connection.request('POST','/v1/chat/completions',body=json.dumps(p).encode(),headers={'Content-Type':'application/json'})
                result=connection.getresponse();body=result.read(2*1024*1024+1);status=result.status;connection.close()
            if len(body)>2*1024*1024:raise ValueError('response size')
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except Exception:self.send_error(502)

def snapshot(source,dest,profile='offline'):
    source=source.resolve();allowed=[P('/opt/custos/work'),P('/opt/custos/repo'),P('/tmp'),P('/var/lib/custos-harness')]
    if not any(source==x or x in source.parents for x in allowed) or source in allowed and source!=P('/opt/custos/repo'):raise ValueError('choose a project subdirectory under work, repo, tmp or candidates')
    # The canonical repo itself is an explicitly supported source.
    total=count=0
    for root,dirs,files in os.walk(source,followlinks=False):
        rel=P(root).relative_to(source);dirs[:]=[n for n in dirs if n not in {'.git','.identities','.state','__pycache__'}]
        target=dest/rel;target.mkdir(parents=True,exist_ok=True)
        for name in list(dirs):
            p=P(root)/name
            if p.is_symlink():(target/name).symlink_to(os.readlink(p));dirs.remove(name)
        for name in files:
            if profile=='fetch' and (rel!=P('.') or name not in {'package.json','package-lock.json','bun.lock','bun.lockb','pyproject.toml','uv.lock','requirements.txt'}):continue
            if name in {'.git','.env','.netrc','.git-credentials'} or name.startswith('._'):continue
            p=P(root)/name;q=target/name;count+=1
            if p.is_symlink():q.symlink_to(os.readlink(p));continue
            if not p.is_file():raise ValueError('nonregular input')
            total+=p.stat().st_size
            if total>768*1024*1024 or count>60000:raise ValueError('input bound exceeded')
            shutil.copy2(p,q)
    return {'files':count,'bytes':total}

def run(source,argv,profile='offline',seconds=900,keep=True,subdir='',test_env=None,job_root=STATE):
    if profile not in {'offline','fetch','model'} or not 1<=seconds<=1800:raise ValueError('invalid profile or time bound')
    if not argv or not all(isinstance(x,str) and '\x00' not in x for x in argv):raise ValueError('command required')
    if subdir and (P(subdir).is_absolute() or '..' in P(subdir).parts):raise ValueError('invalid relative cwd')
    permitted={'HEADLONG_ROOT','CUSTOS_RENEWAL_DIR','PYTHONPATH','CUSTOS_HARNESS_FIXTURE'}
    if test_env and any(k not in permitted or not v.startswith('/work/') or '..' in P(v).parts for k,v in test_env.items()):raise ValueError('test environment must refer to candidate paths')
    if not shutil.which('qemu-system-x86_64') or not shutil.which('systemd-run'):raise RuntimeError('required isolation tools unavailable')
    meminfo=dict((l.split(':',1)[0],int(l.split()[1])) for l in P('/proc/meminfo').read_text().splitlines())
    if meminfo['MemAvailable']<1536*1024:raise RuntimeError('insufficient memory headroom for sealed experiment')
    job_root.mkdir(parents=True,exist_ok=True,mode=0o700);job=job_root/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:12]);job.mkdir(mode=0o700)
    inputs=job/'input';inputs.mkdir();info=snapshot(P(source),inputs,profile)
    head=subprocess.run(['git','-C',str(source),'rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()
    spec={'argv':argv,'profile':profile,'seconds':seconds,'subdir':subdir,'test_env':test_env or {},'git_head':head if re.fullmatch('[a-f0-9]{40}',head) else None}
    (job/'spec.json').write_text(json.dumps(spec));unit='custos-sealed-'+uuid.uuid4().hex[:16]
    proxy=None
    if profile!='offline':
        proxy=Proxy(str(job/'proxy.sock'),ProxyHandler);proxy.profile=profile;proxy.model_lock=threading.Lock();threading.Thread(target=proxy.serve_forever,daemon=True).start()
    toolchain=P('/var/lib/custos-harness/toolchain')
    if not all((toolchain/n).is_file() for n in ['root.ext4','vmlinuz','initrd']):raise RuntimeError('sealed VM toolchain is not installed')
    # Read-only block images expose only copied source and the curated toolchain.
    # No virtiofs/9p mounts, host directory shares, production sockets or credentials.
    volume=job/'volume';volume.mkdir();inputs.rename(volume/'input');inputs=volume/'input'
    shutil.copy2(job/'spec.json',volume/'spec.json')
    shutil.copy2(P(__file__).with_name('sandbox_entry.py'),volume/'entry.py')
    disk=job/'input.ext4';size=max(64*1024*1024,info['bytes']+info['files']*8192+32*1024*1024)
    with disk.open('wb') as f:f.truncate(size)
    subprocess.run(['mkfs.ext4','-q','-F','-N',str(max(1024,info['files']+1000)),'-d',str(volume),str(disk)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=120)
    args=['qemu-system-x86_64','-machine','q35,accel=kvm','-cpu','host','-smp','1','-m','1536',
          '-nodefaults','-no-reboot','-display','none','-monitor','none','-serial','stdio',
          '-kernel',str(toolchain/'vmlinuz'),'-initrd',str(toolchain/'initrd'),
          '-append','root=/dev/vda ro init=/sbin/custos-sealed-init console=ttyS0 quiet loglevel=0 panic=1',
          '-drive','file='+str(toolchain/'root.ext4')+',format=raw,if=virtio,readonly=on',
          '-drive','file='+str(disk)+',format=raw,if=virtio,readonly=on']
    if profile=='offline':args+=['-nic','none']
    else:
        # SLIRP restrict=on denies all normal guest egress/host access. Its only
        # guestfwd starts a fixed socat bridge to this job's allowlisted proxy.
        args+=['-netdev','user,id=sealed,restrict=on,guestfwd=tcp:10.0.2.100:18080-cmd:/usr/bin/socat STDIO UNIX-CONNECT:'+str(job/'proxy.sock'),'-device','virtio-net-pci,netdev=sealed']
    cmd=['systemd-run','--quiet','--pipe','--wait','--collect','--unit',unit,'-p','MemoryMax=2048M','-p','MemorySwapMax=128M','-p','CPUQuota=100%','-p','TasksMax=256','-p','RuntimeMaxSec='+str(seconds+60),'-p','KillMode=control-group','-p','TimeoutStopSec=5',*args]
    clean={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':'/nonexistent'}
    result=None;artifact_bytes=log_bytes=0;started=time.monotonic();last_notice=started
    proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=clean,close_fds=True)
    try:
        with (job/'output.log').open('wb') as log,(job/'artifacts.tar.gz').open('wb') as artifacts,(job/'runner.log').open('wb') as diagnostics,selectors.DefaultSelector() as sel:
            sel.register(proc.stdout,selectors.EVENT_READ,'out');sel.register(proc.stderr,selectors.EVENT_READ,'err');buf=b''
            while sel.get_map():
                if time.monotonic()-started>seconds+75:raise TimeoutError('outer job deadline')
                if time.monotonic()-last_notice>30:
                    print('hermetic: running '+job.name,flush=True,file=sys.stderr);last_notice=time.monotonic()
                for k,_ in sel.select(1):
                    chunk=os.read(k.fileobj.fileno(),65536)
                    if not chunk:sel.unregister(k.fileobj);continue
                    if k.data=='err':
                        if diagnostics.tell()+len(chunk)>1024*1024:raise ValueError('runner output bound')
                        diagnostics.write(chunk);continue
                    buf+=chunk
                    if len(buf)>256*1024:raise ValueError('invalid child frame')
                    while b'\n' in buf:
                        line,buf=buf.split(b'\n',1)
                        if not line.lstrip().startswith(b'{'):
                            if diagnostics.tell()+len(line)<1024*1024:diagnostics.write(line+b'\n')
                            continue
                        row=json.loads(line)
                        if row['kind']=='log':
                            data=base64.b64decode(row['data'],validate=True);log_bytes+=len(data)
                            if log_bytes>8*1024*1024:raise ValueError('log bound')
                            log.write(data);log.flush();sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
                        elif row['kind']=='artifact':
                            data=base64.b64decode(row['data'],validate=True);artifact_bytes+=len(data)
                            if artifact_bytes>140*1024*1024:raise ValueError('artifact bound')
                            artifacts.write(data)
                        elif row['kind']=='result':result=row
                        else:raise ValueError('invalid child protocol')
            rc=proc.wait(timeout=10)
            if rc or result is None:raise RuntimeError('sandbox failed; inspect '+str(job/'runner.log'))
    except BaseException as e:
        result={'exit_code':125,'reason':type(e).__name__,'error':str(e)}
    finally:
        subprocess.run(['systemctl','stop',unit+'.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=clean,timeout=15)
        if proc.poll() is None:proc.kill();proc.wait()
        if proxy:proxy.shutdown();proxy.server_close()
    result.update(job=str(job),profile=profile,source=str(P(source).resolve()),input=info,unit=unit)
    (job/'result.json').write_text(json.dumps(result,indent=2)+'\n');print('hermetic: report '+str(job/'result.json'),file=sys.stderr,flush=True)
    disk.unlink(missing_ok=True)
    if not keep:shutil.rmtree(volume)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cwd',default=os.getcwd());p.add_argument('--profile',choices=['offline','fetch','model'],default='offline');p.add_argument('--timeout',type=int,default=900);p.add_argument('--keep',action='store_true');p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    try:return int(run(a.cwd,command,a.profile,a.timeout,a.keep)['exit_code'])
    except (ValueError,RuntimeError,OSError) as e:print('hermetic: '+str(e),file=sys.stderr);return 125
if __name__=='__main__':raise SystemExit(main())
