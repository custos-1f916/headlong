"""Audit regressions; all commands/models/receipts use disposable sealed fixtures."""
import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import custos_brain_client as client
import custos_memory as memory
import custos_receipt_text as receipt
from test_audit_20260924 import ShellRuntime
from test_memory import MemoryFixture

ROOT=Path(__file__).resolve().parents[2]/'runtime/headlong'
policy=importlib.machinery.SourceFileLoader('command_policy',str(ROOT/'bin/shellm-command-policy')).load_module()


class LiteralAndBudget(ShellRuntime):
    def test_literal_final_preserves_currency_quotes_multiline(self):
        self.env['SHELLM_EXEC_TIMEOUT']='30'
        literal='$91.30, "$12.00" and it\'s literal.\nSecond line: $(touch NEVER), `id`.\n'
        self.stub('```bash\nshellm-final <<\'FINAL_TEXT\'\n'+literal+'FINAL_TEXT\n```\n')
        result=subprocess.run([str(self.b/'shellm'),'-q','--workdir',str(self.wd),'literal'],env=self.env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr[-2500:])
        self.assertEqual(result.stdout.strip(),literal.strip())
        self.assertFalse((self.wd/'NEVER').exists())
        self.assertEqual(next(x for x in self.rows() if x.get('type')=='final')['content'],literal.rstrip('\n'))

    def test_unsafe_final_block_is_rejected_before_earlier_side_effect(self):
        self.env['SHELLM_EXEC_TIMEOUT']='30'
        self.stub('```bash\ntouch SHOULD_NOT_EXIST\nFINAL="Total $91.30"\n```\n')
        result=subprocess.run([str(self.b/'shellm'),'-q','--workdir',str(self.wd),'currency'],env=self.env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=40)
        self.assertFalse((self.wd/'SHOULD_NOT_EXIST').exists())
        row=next(x for x in self.rows() if x.get('type')=='reasoning')
        self.assertFalse(row['execution_started']);self.assertIn('dollar-number',row['execution_rejected'])
        self.assertIn('block was not executed',next(x for x in self.rows() if x.get('type')=='shell-output')['stdout'])

    def test_search_deadline_preserves_partial_output(self):
        self.env.update(SHELLM_EXEC_TIMEOUT='30',SHELLM_SEARCH_TIMEOUT='2')
        (self.b/'rg').write_text('#!/bin/sh\necho partial-match\nsleep 20\n');(self.b/'rg').chmod(0o755)
        self.stub('```bash\nrg needle named-repo\n```\n')
        result=subprocess.run([str(self.b/'shellm'),'-q','--workdir',str(self.wd),'search'],env=self.env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20)
        rows=self.rows();row=next(x for x in rows if x.get('execution_started'))
        self.assertEqual(row['execution_timeout'],2);self.assertEqual(row['execution_budget'],'search')
        output=next(x for x in rows if x.get('type')=='shell-output')
        self.assertTrue(output['timed_out']);self.assertIn('partial-match',output['stdout'])
        self.assertLess(output['exec_s'],12)


class Policies(unittest.TestCase):
    def test_audited_search_with_headings_cd_and_redirection_gets_short_budget(self):
        script = '''echo "heading" >&2
cd /tmp/asban
grep -oiE '"datePublished"[^,]*|<time[^>]*datetime="[^"]*"' marcus.html | head
grep -rilE '10\\^?25|1025 ?ops|develop, deploy|acquire, possess, fund' /tmp 2>/dev/null | head
grep -rilE 'ban artificial superintelligence act' /root/.headlong/app/.identities/custos/workdir 2>/dev/null | grep -viE 'node_modules|uv.lock' | head
find /tmp -iname '*.pdf' -o -iname '*bill*' 2>/dev/null | head
rg nothing named-file 2>/dev/null || true
'''
        self.assertEqual(policy.inspect(script)['timeout'],60)
        self.assertEqual(policy.inspect(script+'make build\n')['timeout'],2400)
    def test_search_and_build_budgets(self):
        for script in ['rg needle repo | head -20', 'grep -R needle /tmp', 'find repo -name file']:
            self.assertEqual(policy.inspect(script)['timeout'],60,script)
        for script in ['make build', 'rg x repo; make test', 'python3 script.py', 'find repo -exec make {} ;']:
            self.assertEqual(policy.inspect(script)['timeout'],2400,script)
        self.assertEqual(policy.inspect('# shellm: timeout=120\nrg x repo')['timeout'],120)
        self.assertEqual(policy.inspect('# shellm: timeout=99999\nmake')['timeout'],2400)
    def test_literal_and_escaped_final_are_allowed(self):
        for text in ["FINAL='Total $91.30'",r'FINAL="Total \$91.30"',"shellm-final <<'TEXT'\nFINAL=\"$91.30\"\nTEXT\n"]:
            self.assertIsNone(policy.inspect(text)['error'],text)
    def test_receipt_fields_copy_exactly(self):
        doc={'estimated_total':'91.30','state':'COMMENTED','body':'He said "yes".\n$12.00'}
        result=receipt.render(doc,'Total {{total}}; {{state}}.\n{{body}}',{'total':'/estimated_total','state':'/state','body':'/body'},['total'])
        self.assertEqual(result,'Total $91.30; COMMENTED.\nHe said "yes".\n$12.00')
        with self.assertRaises(ValueError):receipt.render(doc,'{{missing}}',{'total':'/estimated_total'})
        with self.assertRaises(ValueError):receipt.render({'total':'91.301'},'{{t}}',{'t':'/total'},['t'])
    def test_receipt_cli_retains_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);raw=b'{"total":91.30}';(d/'receipt').write_bytes(raw);(d/'template').write_text('Total {{t}}\n')
            rc=receipt.main(['--receipt',str(d/'receipt'),'--template-file',str(d/'template'),'--field','t=/total','--usd','t','--output',str(d/'final')])
            self.assertEqual(rc,0);self.assertEqual((d/'final').read_text(),'Total $91.30\n')
            self.assertEqual(json.loads((d/'final.receipt.json').read_text())['sha256'],hashlib.sha256(raw).hexdigest())


class RetryWire(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.calls=[];self.responses=[]
        parent=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                parent.calls.append(body)
                status,content=parent.responses[min(len(parent.calls)-1,len(parent.responses)-1)]
                self.send_response(status);self.send_header('Content-Type','application/json' if status>=400 or not body['stream'] else 'text/event-stream')
                self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.env={'HOME':str(self.home),'HEADLONG_HOME':str(self.home), 'PATH':'/usr/local/bin:/usr/bin:/bin',
                  'LLM_PROVIDER':'openai-compatible','LLM_API_URL':f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
                  'LLM_RETRIES':'2','LLM_RETRY_BACKOFF':'0','LLM_ADMISSION_RETRIES':'0'}
    def call(self,stream):
        return subprocess.run([str(ROOT/'bin/llm'),'-m','tycho/qwen3.8-27b','--stream' if stream else '--no-stream','--effort','medium','probe'],env=self.env,capture_output=True,text=True,timeout=15)
    def test_http400_is_terminal_stream_and_nonstream(self):
        self.responses=[(400,b'{"error":{"message":"unsupported_model"}}')]
        for stream in [True,False]:
            self.calls.clear();r=self.call(stream)
            self.assertNotEqual(r.returncode,0);self.assertEqual(len(self.calls),1,r.stderr)
            error=memory.CommandFailure('llm',r.returncode,r.stderr)
            self.assertEqual(error.details['http_status'],400);self.assertTrue(error.deterministic)
            self.assertEqual(error.code,'unsupported_model')
    def test_5xx_and_429_retry_then_succeed(self):
        for status in [503,429]:
            for stream in [True,False]:
                self.calls.clear();body=(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n' if stream else b'{"choices":[{"message":{"content":"ok"}}]}')
                self.responses=[(status,b'{"error":{"message":"busy"}}'),(200,body)]
                r=self.call(stream);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(len(self.calls),2);self.assertIn('ok',r.stdout)
    def test_diagnostics_drop_arbitrary_stderr(self):
        error=memory.CommandFailure('llm',1,'API error (HTTP 400): unsupported_model SECRET request content')
        self.assertNotIn('SECRET',str(error));self.assertEqual(memory.failure_code(error),'unsupported_model')


class Recovery(MemoryFixture):
    def test_configuration_error_preserves_goal_and_rearms_only_on_change(self):
        goal=self.store.capture(self.payload,'trigger',deferred=True)['goal_id']
        incoming={'request_id':self.payload['request_id']}
        with patch.object(memory,'inference_signature',return_value='before'),patch.object(memory,'record_invalid_response',return_value='private-diagnostic'),patch.object(memory,'append_step'):
            with self.assertRaises(memory.ResponseFailure):
                with memory.response_attempt(self.store,goal,incoming,'trigger',{}) as attempt:
                    attempt['stage']='inference';raise memory.CommandFailure('llm',1,'API error (HTTP 400): unsupported_model')
            record=self.store.find(goal)[4]
            self.assertEqual(record['status'],'active');self.assertFalse(record['responder_attempt']['retryable'])
            with memory.response_attempt(self.store,goal,incoming,'trigger',{}) as attempt:self.assertTrue(attempt['blocked'])
        with patch.object(memory,'inference_signature',return_value='after'):
            with memory.response_attempt(self.store,goal,incoming,'trigger',{}) as attempt:
                self.assertFalse(attempt['blocked']);self.assertEqual(attempt['count'],1)
        self.assertEqual(self.store.find(goal)[4]['origin'],record['origin'])


if __name__=='__main__':unittest.main()
