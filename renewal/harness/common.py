"""Release data contracts shared by the builder and fixed deployment controller."""
import hashlib,json,os,pathlib,re,tarfile,tempfile
P=pathlib.Path
HEX=re.compile(r'[0-9a-f]{64}'); COMMIT=re.compile(r'[0-9a-f]{40}')
MAX_ARCHIVE=384*1024*1024; MAX_TREE=1536*1024*1024

def digest(p):
 h=hashlib.sha256()
 with P(p).open('rb') as f:
  while b:=f.read(1024*1024):h.update(b)
 return h.hexdigest()

def atomic_json(p,v):
 p=P(p);p.parent.mkdir(parents=True,exist_ok=True)
 fd,n=tempfile.mkstemp(dir=p.parent,prefix='.'+p.name)
 try:
  with os.fdopen(fd,'w') as f:json.dump(v,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
  os.replace(n,p);d=os.open(p.parent,os.O_DIRECTORY);os.fsync(d);os.close(d)
 finally:
  if os.path.exists(n):os.unlink(n)

def safe_name(n):
 p=P(n)
 if not n or p.is_absolute() or '..' in p.parts or n.startswith('./') or any(x in p.parts for x in ['.git','.identities','.env']):raise ValueError('unsafe archive path')
 return p

def validate_archive(path,expected=None):
 if P(path).stat().st_size>MAX_ARCHIVE:raise ValueError('archive too large')
 actual=digest(path)
 if expected is not None and actual!=expected:raise ValueError('artifact hash mismatch')
 with tarfile.open(path,'r:gz') as t:
  entries=t.getmembers();seen=set();total=0
  if len(entries)>60000:raise ValueError('too many archive entries')
  for e in entries:
   safe_name(e.name)
   if e.name in seen:raise ValueError('duplicate archive entry')
   seen.add(e.name)
   # No symlink traversal, hardlinks, devices, fifos, ownership or capabilities.
   if not e.isfile() and not e.isdir():raise ValueError('release archives contain regular files/directories only')
   if e.mode & 0o7000:raise ValueError('special mode')
   total+=e.size
   if total>MAX_TREE:raise ValueError('expanded artifact too large')
  m=t.extractfile('release.json')
  if m is None or t.getmember('release.json').size>2*1024*1024:raise ValueError('manifest missing or too large')
  manifest=json.load(m)
  if manifest.get('format')!=1 or manifest.get('state_version')!=1:raise ValueError('unsupported state version/migration')
  if not COMMIT.fullmatch(manifest.get('commit','')):raise ValueError('invalid source commit')
  files=manifest.get('files',{})
  if not isinstance(files,dict) or set(files)!=seen-{'release.json'}:raise ValueError('manifest/file mismatch')
  for e in entries:
   if not e.isfile() or e.name=='release.json':continue
   row=files[e.name]
   if not isinstance(row,dict) or row.get('sha256')!=hashlib.sha256(t.extractfile(e).read()).hexdigest() or row.get('mode')!=e.mode:raise ValueError('file hash/mode mismatch')
  for n in ['runtime/headlong/bin/thinkers','runtime/headlong/bin/llm','runtime/headlong/bin/shellm','renewal/bin/custos-memory']:
   if n not in files:raise ValueError('missing core executable')
 return actual,manifest

def extract_archive(path,dest,expected=None):
 actual,m=validate_archive(path,expected);dest=P(dest)
 if dest.exists():raise ValueError('destination must be new')
 dest.mkdir(parents=True,mode=0o700)
 with tarfile.open(path,'r:gz') as t:
  for e in t.getmembers():
   p=dest/e.name;p.parent.mkdir(parents=True,exist_ok=True)
   if e.isdir():p.mkdir(exist_ok=True);continue
   with t.extractfile(e) as src,p.open('xb') as out:
    import shutil;shutil.copyfileobj(src,out)
   p.chmod(e.mode)
 return actual,m

def overlay_action(previous,current,candidate):
 """Keep local edits; stop on a concurrent release edit to the same file."""
 if current==candidate:return 'keep'
 if current==previous:return 'replace'
 if candidate==previous:return 'keep'
 raise ValueError('managed overlay conflict')
