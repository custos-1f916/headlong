#!/usr/bin/env python3
"""Verify real traj CLI output against per-field grep, including blob contents."""
import json,os,subprocess,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as d:
    root=Path(d);folder=root/'abcdef12-root';folder.mkdir();path=folder/'trajectory.jsonl'
    rows=[{'step_id':'a','content':'Before\nCapture race\nafter\ngap\ngap\nCAPTURE tail','thought':'a+b literal\n尾 anchor','stdout':'stub','stdout_ref':'blob.txt'}, {'step_id':'b','cmd':'echo capture\nnext','stderr':'No matches'}]
    (folder/'blob.txt').write_text('blob start\ncapture full output\nblob end\n')
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    env=dict(os.environ,TRAJ_DIR=str(root),TRAJ_ID='abcdef12')
    cases=[('capture',[],['content','thought','cmd','stdout','stderr']),('capture',['-i','-C','1'],['content','thought','cmd','stdout','stderr']),('Capture|a[+]b',['-E','-C','1'],['content','thought','cmd','stdout','stderr']),('capture',['--field','stdout'],['stdout']),('尾',[],['content','thought','cmd','stdout','stderr']),('absent',[],['content','thought','cmd','stdout','stderr'])]
    for pattern,flags,fields in cases:
        expected=[]
        for row in rows:
            for field in fields:
                val=(folder/row[field+'_ref']).read_text() if row.get(field+'_ref') else row.get(field,'')
                if not val:continue
                opts=[x for x in flags]
                if '--field' in opts:opts=opts[:opts.index('--field')]+opts[opts.index('--field')+2:]
                if '-E' not in opts:opts+=['-F']
                r=subprocess.run(['grep','-n',*opts,'--',pattern],input=val.rstrip('\n')+'\n',text=True,capture_output=True)
                expected.extend(row['step_id']+':'+field+':'+l for l in r.stdout.splitlines())
        r=subprocess.run([str(ROOT/'bin/traj'),'search',pattern,*flags],env=env,text=True,capture_output=True,timeout=5)
        assert r.returncode==0,(r.returncode,r.stderr)
        assert r.stdout==''.join(l+'\n' for l in expected),(pattern,flags,r.stdout,expected)
    print('PASS literal, regex, case, context, blob, Unicode, field and no-match output matches grep')
    path.write_text(path.read_text()+'{malformed\n'+json.dumps({'step_id':'c','content':'capture after malformed'})+'\n')
    r=subprocess.run([str(ROOT/'bin/traj'),'search','capture'],env=env,text=True,capture_output=True,timeout=5)
    assert 'c:content:1:capture after malformed' in r.stdout and 'malformed JSON' in r.stderr
    print('PASS malformed records are reported and later valid records remain searchable')
    path.write_text(''.join(json.dumps({'step_id':str(i),'content':'needle '+str(i)})+'\n' for i in range(5000)))
    t=time.monotonic();r=subprocess.run([str(ROOT/'bin/traj'),'search','needle'],env=env,text=True,capture_output=True,timeout=5)
    assert r.returncode==0 and len(r.stdout.splitlines())==5000
    print('PASS 5000 matching records in %.3fs'%(time.monotonic()-t))
