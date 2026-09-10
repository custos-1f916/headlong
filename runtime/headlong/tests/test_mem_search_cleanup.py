#!/usr/bin/env python3
"""A failed/cancelled search must not keep a result pipeline open via heartbeat."""
import os,signal,subprocess,tempfile,shlex
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);(root/'bin').mkdir();(root/'mem').mkdir()
    (root/'mem/2026-09-08-00-00-00_abcd_capture.md').write_text('---\nsummary: capture race\ntype: fact\n---\ncapture race\n')
    env=dict(os.environ,PATH=str(root/'bin')+':'+os.environ['PATH'],MEM_DIR=str(root/'mem'),MEM_SEARCH_HEARTBEAT_S='0.05')
    for mode,body,expected in [('failed','exit 42',42),('cancelled','kill -TERM "$PPID"; exit 0',143)]:
        stub=root/'bin/llm';stub.write_text('#!/bin/bash\ncat >/dev/null\nsleep 0.15\n'+body+'\n');stub.chmod(0o755)
        p=subprocess.Popen(['bash','-o','pipefail','-c',shlex.quote(str(ROOT/'bin/mem'))+' search "capture race" | head -5'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:out,err=p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.communicate();raise AssertionError(mode+': orphan heartbeat kept pipeline open')
        assert p.returncode==expected,(mode,p.returncode,err.decode())
        assert b'waiting for the model' not in out
        print('PASS',mode,'search closes its pipeline and preserves exit',expected)
    # Unicode and JSON escaping must fit the actual wire budget, not only raw bytes.
    (root/'mem/2026-09-08-00-00-00_abcd_capture.md').write_text('capture race\n'+('証人 "capture" \\ race\n'*20000))
    capture=root/'captured.txt'
    stub.write_text('#!/bin/bash\ncat > '+shlex.quote(str(capture))+'\nprintf "bounded reply\\n"\n')
    r=subprocess.run([str(ROOT/'bin/mem'),'search','capture race'],env=dict(env,MEM_SEARCH_MAX_BYTES='8192'),text=True,capture_output=True,timeout=3)
    assert r.returncode==0,r.stderr
    import json
    assert len(json.dumps(capture.read_text(),ensure_ascii=False).encode())<8500
    assert 'EXCERPT' in capture.read_text() and 'not an exhaustive search' in r.stderr
    print('PASS oversized Unicode memory is bounded with explicit incomplete-coverage notice')
