"""A maintenance request during a tool finishes that tool and prevents another model call."""
import json,os,pathlib,shutil,subprocess,tempfile,unittest

class Handoff(unittest.TestCase):
 def test_yield_after_atomic_step(self):
  root=pathlib.Path(os.environ['HEADLONG_ROOT'])
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);b=p/'bin';shutil.copytree(root/'bin',b)
   marker=p/'maintenance';calls=p/'calls';completed=p/'completed'
   # Redirect only the fixed sentinel in the fixture; never touch live maintenance.
   shell=b/'shellm';shell.write_text(shell.read_text().replace('/var/lib/custos/harness-maintenance',str(marker)))
   (b/'llm').write_text('''#!/usr/bin/env bash
if [[ " $* " != *" --thinking "* ]]; then printf '{}\\n'; exit 0; fi
printf 'call\\n' >> "$HANDOFF_CALLS"
printf '```bash\\ntouch "%s"\\nsleep 0.1\\nprintf completed > "%s"\\n```\\n' "$HANDOFF_MARKER" "$HANDOFF_COMPLETED"
''');(b/'llm').chmod(0o755)
   home=p/'home';home.mkdir();wd=p/'wd';wd.mkdir()
   env=dict(os.environ,PATH=str(b)+':'+os.environ['PATH'],HOME=str(home),HEADLONG_HOME=str(home/'.headlong'),IDENTITY_NAME='custos',SHELLM_MODEL='fixture',SHELLM_ENV='local',ANTHROPIC_API_KEY='fixture',HANDOFF_CALLS=str(calls),HANDOFF_MARKER=str(marker),HANDOFF_COMPLETED=str(completed))
   for name in ['IDENTITY_DIR','TRAJ_DIR','TRAJ_ID','MEM_DIR','THINKERS_DIR']:env.pop(name,None)
   r=subprocess.run([str(shell),'--workdir',str(wd),'--max-iterations','3','Exercise one atomic step then yield.'],env=env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=45)
   self.assertEqual(r.returncode,0,r.stderr[-2500:]);self.assertEqual(completed.read_text(),'completed');self.assertEqual(calls.read_text(),'call\n');self.assertIn('unfinished work remains active',r.stdout)
   rows=[]
   for f in p.rglob('trajectory.jsonl'):rows.extend(json.loads(x) for x in f.read_text().splitlines() if x)
   handoffs=[x for x in rows if x.get('type')=='harness-handoff'];self.assertEqual(len(handoffs),1)
   self.assertTrue(any(x.get('type')=='shell-output' and x.get('exit')==0 for x in rows))
