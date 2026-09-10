import hashlib,io,json,pathlib,tarfile,tempfile,unittest,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from harness.common import validate_archive,extract_archive,safe_name
class ArchiveTests(unittest.TestCase):
 def test_unsafe_names(self):
  for n in ['../escape','/etc/shadow','renewal/../escape','runtime/.identities/custos/.env','']:
   with self.subTest(n=n),self.assertRaises(ValueError):safe_name(n)
 def archive(self,extra=None,version=1):
  p=pathlib.Path(self.tmp.name)/'test.tar.gz';data={n:b'#!/bin/sh\n' for n in ['runtime/headlong/bin/thinkers','runtime/headlong/bin/llm','runtime/headlong/bin/shellm','renewal/bin/custos-memory']}
  manifest={'format':1,'state_version':version,'commit':'a'*40,'files':{n:{'sha256':hashlib.sha256(b).hexdigest(),'mode':0o755} for n,b in data.items()}}
  data['release.json']=json.dumps(manifest).encode()
  with tarfile.open(p,'w:gz') as t:
   for n,b in data.items():
    e=tarfile.TarInfo(n);e.size=len(b);e.mode=0o755;t.addfile(e,io.BytesIO(b))
   if extra:t.addfile(extra)
  return p
 def setUp(self):self.tmp=tempfile.TemporaryDirectory()
 def tearDown(self):self.tmp.cleanup()
 def test_valid_exact_extract(self):
  p=self.archive();h,m=validate_archive(p);out=pathlib.Path(self.tmp.name)/'out';extract_archive(p,out,h);self.assertTrue((out/'renewal/bin/custos-memory').exists())
 def test_hash_mismatch(self):
  with self.assertRaises(ValueError):validate_archive(self.archive(),'f'*64)
 def test_symlink(self):
  e=tarfile.TarInfo('renewal/link');e.type=tarfile.SYMTYPE;e.linkname='/etc'
  with self.assertRaises(ValueError):validate_archive(self.archive(e))
 def test_traversal(self):
  e=tarfile.TarInfo('../escape')
  with self.assertRaises(ValueError):validate_archive(self.archive(e))
 def test_future_migration_rejected(self):
  with self.assertRaises(ValueError):validate_archive(self.archive(version=2))
if __name__=='__main__':unittest.main()
