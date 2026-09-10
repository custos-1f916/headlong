#!/usr/bin/env python3
import os,json,subprocess,tempfile,time,signal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as d:
    root=Path(d);(root/'bin').mkdir();capture=root/'payload.json'
    curl=root/'bin/curl';curl.write_text('''#!/usr/bin/env python3
import os,sys,json,time
from pathlib import Path
time.sleep(float(os.environ.get('FIXTURE_DELAY','0')))
a=sys.argv[1:];payload=Path(a[a.index('-d')+1][1:]).read_bytes();Path(os.environ['CAPTURE']).write_bytes(payload)
Path(a[a.index('-o')+1]).write_text(json.dumps({'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}],'usage':{'completion_tokens':1}}))
print('200',end='')
''');curl.chmod(0o755)
    env=dict(os.environ,PATH=str(root/'bin')+':'+os.environ['PATH'],HEADLONG_HOME=str(root/'home'),LLM_PROVIDER='openai-compatible',LLM_API_URL='http://localhost/v1/chat/completions',LLM_API_KEY='fixture',LLM_EFFORT='xhigh',LLM_MIN_OUTPUT_TOKENS='65536',LLM_RETRIES='0',CAPTURE=str(capture))
    cmd=[str(ROOT/'bin/llm'),'-m','qwen3.8-27b','-t','1024','--no-stream']
    r=subprocess.run(cmd,input='fixture',text=True,capture_output=True,env=env,timeout=5);assert r.returncode==0,r.stderr
    p=json.loads(capture.read_text());assert p['max_tokens']==65536 and p['reasoning_effort']=='xhigh',p
    print('PASS xhigh utility request gets full configured reasoning budget despite -t 1024')
    del env['LLM_MIN_OUTPUT_TOKENS'];r=subprocess.run(cmd,input='fixture',text=True,capture_output=True,env=env,timeout=5);assert r.returncode==0,r.stderr
    assert json.loads(capture.read_text())['max_tokens']==1024
    print('PASS identities without opt-in floor retain explicit token limits')

    beacon=root/'activity'
    env.update(SHELLM_ACTIVITY_FILE=str(beacon),FIXTURE_DELAY='3')
    process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,start_new_session=True)
    process.stdin.write('fixture');process.stdin.close();process.stdin=None
    deadline=time.monotonic()+2
    while not beacon.exists() and time.monotonic()<deadline:time.sleep(.02)
    assert beacon.exists(), 'nonstream llm must keep the parent execution active'
    first=beacon.read_text();time.sleep(1.2)
    assert beacon.read_text()!=first,'activity must progress during silent model work'
    out,err=process.communicate(timeout=5);assert process.returncode==0,err
    ended=beacon.stat().st_mtime_ns;time.sleep(1.2);assert beacon.stat().st_mtime_ns==ended,'beacon must stop after success'
    print('PASS silent model work refreshes shellm activity and closes result pipes on success')
    env['FIXTURE_DELAY']='10'
    process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,start_new_session=True)
    process.stdin.write('fixture');process.stdin.close();process.stdin=None
    time.sleep(.5);os.killpg(process.pid,signal.SIGTERM)
    out,err=process.communicate(timeout=3);assert process.returncode in (143,-signal.SIGTERM),(process.returncode,err)
    ended=beacon.stat().st_mtime_ns;time.sleep(1.2);assert beacon.stat().st_mtime_ns==ended,'beacon must stop after cancellation'
    print('PASS cancelled nonstream call closes pipes and stops activity')
