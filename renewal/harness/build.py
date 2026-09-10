"""Build an exact committed source tree; promotion never rebuilds it."""
import json,os,pathlib,shutil,subprocess,tarfile,tempfile
from harness.common import digest,atomic_json,validate_archive,MAX_TREE
P=pathlib.Path;STATE=P('/var/lib/custos-harness')

def build(repo,commit):
 repo=P(repo).resolve()
 if not (repo==P('/opt/custos/repo') or P('/opt/custos/work') in repo.parents):raise ValueError('build from the Custos repository or its worktrees')
 sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','--verify',commit+'^{commit}'],text=True).strip()
 # Uncommitted changes never enter the artifact.
 with tempfile.TemporaryDirectory(prefix='build-',dir=STATE) as tmp:
  tmp=P(tmp);source=tmp/'source';source.mkdir();raw=tmp/'source.tar'
  with raw.open('wb') as f:subprocess.run(['git','-C',str(repo),'archive',sha],stdout=f,check=True)
  with tarfile.open(raw) as t:
   for e in t.getmembers():
    if e.issym() or e.islnk():raise ValueError('vendor/source links must be committed as regular files')
    if not e.name.startswith(('runtime/','renewal/')):continue
    if not e.isfile():continue
    p=source/e.name;p.parent.mkdir(parents=True,exist_ok=True)
    with t.extractfile(e) as f,p.open('wb') as o:shutil.copyfileobj(f,o)
    p.chmod(e.mode & 0o777)
  config=json.loads((source/'runtime/upstream.json').read_text())
  if config.get('state_version')!=1:raise ValueError('state migration requires a compatible controller contract; current state version is 1')
  deps=STATE/'dependencies';depmeta=json.loads((deps/'manifest.json').read_text())
  for name,expected in depmeta['inputs'].items():
   if digest(source/name)!=expected:raise ValueError('dependency input changed: '+name+'; prepare and qualify a new pinned dependency bundle first')
  shutil.copytree(deps/'renewal-node',source/'renewal/node_modules')
  shutil.copytree(deps/'web',source/'runtime/headlong/web/.runtime')
  # Build viewer from this commit, using pinned offline dependencies inside the seal.
  shutil.copytree(deps/'viewer-node',source/'runtime/headlong/web/viewer/node_modules')
  from harness.seal import run
  result=run(source,['bun','run','build'],seconds=240,subdir='runtime/headlong/web/viewer',keep=False)
  if result['exit_code']!=0:raise RuntimeError('sealed viewer build failed: '+result['job'])
  shutil.rmtree(source/'runtime/headlong/web/viewer/node_modules')
  with tarfile.open(P(result['job'])/'artifacts.tar.gz') as t:
   prefix='work/runtime/headlong/web/viewer/build/'
   files=[e for e in t.getmembers() if e.isfile() and e.name.startswith(prefix) and '..' not in P(e.name).parts]
   if not files:raise RuntimeError('sealed build produced no viewer assets')
   for e in files:
    p=source/e.name.removeprefix('work/');p.parent.mkdir(parents=True,exist_ok=True)
    with t.extractfile(e) as f,p.open('wb') as o:shutil.copyfileobj(f,o)
    p.chmod(0o644)
  # Host-controller sources ship for review but never replace host code.
  files={};total=0
  for p in sorted(source.rglob('*')):
   if not p.is_file():continue
   if p.is_symlink():raise ValueError('dependency bundle contains a link')
   total+=p.stat().st_size
   if total>MAX_TREE:raise ValueError('release tree bound')
   files[str(p.relative_to(source))]={'sha256':digest(p),'mode':p.stat().st_mode & 0o777}
  manifest={'format':1,'state_version':1,'commit':sha,'upstream':config,'dependency_bundle':depmeta['id'],'files':files}
  atomic_json(source/'release.json',manifest)
  archive=tmp/'release.tar.gz'
  with archive.open('wb') as rawout:
   import gzip
   with gzip.GzipFile(filename='',mode='wb',fileobj=rawout,mtime=0) as zipped,tarfile.open(fileobj=zipped,mode='w') as t:
    for p in sorted(source.rglob('*')):
     if not p.is_file():continue
     e=t.gettarinfo(str(p),arcname=str(p.relative_to(source)));e.uid=e.gid=0;e.uname=e.gname='';e.mtime=0
     with p.open('rb') as f:t.addfile(e,f)
  artifact,_=validate_archive(archive);dest=STATE/'artifacts';dest.mkdir(exist_ok=True)
  target=dest/(artifact+'.tar.gz');os.replace(archive,target)
  return {'ok':True,'artifact':artifact,'commit':sha,'path':str(target),'build_job':result['job']}
