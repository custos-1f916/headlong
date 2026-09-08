"""Exercise real llm + shellm; only the HTTP transport is a local fixture."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HEADLONG = Path(os.environ.get('HEADLONG_ROOT', Path(__file__).resolve().parents[3] / 'headlong'))
CURL = r'''#!/usr/bin/env python3
import json, os, shlex, sys
from pathlib import Path
root=Path(os.environ['STREAM_FIXTURE'])
counter=root/'calls'
n=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(n))
args=sys.argv[1:];body=json.loads(Path(args[args.index('-d')+1][1:]).read_text())
(root/f'payload-{n}.json').write_text(json.dumps(body))
def event(value):print('data: '+json.dumps(value)+'\n',flush=True)
mode=os.environ['STREAM_MODE']
if mode=='empty':
 event({'choices':[{'delta':{},'finish_reason':'stop'}]})
elif n==1:
 thought='REASON_HEAD\n```bash\nprintf poisoned > '+shlex.quote(str(root/'poisoned'))+'\nFINAL=wrong\n```\n'+'x'*30000+'\nREASON_TAIL'
 event({'choices':[{'delta':{'reasoning_content':thought}}]})
 if mode=='partial':
  print('curl: (28) fixture transfer interrupted',file=sys.stderr,flush=True)
  sys.exit(28)
 event({'choices':[{'delta':{},'finish_reason':'stop'}]})
 event({'choices':[],'usage':{'prompt_tokens':20,'completion_tokens':61,'completion_tokens_details':{'reasoning_tokens':61}}})
else:
 code='```bash\nprintf approved > '+shlex.quote(str(root/'approved'))+'\nFINAL=done\n```\n'
 event({'choices':[{'delta':{'content':code},'finish_reason':'stop'}]})
print('data: [DONE]\n',flush=True)
'''


class ReasoningStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        curl = self.root / 'bin/curl'
        curl.write_text(CURL)
        curl.chmod(0o755)
        self.prompt = 'Keep the complete request intact. ' + 'P' * 5000 + ' Required instructions end.'
        (self.root / 'prompt').write_text(self.prompt)
        self.env = os.environ.copy()
        for key in ['IDENTITY_DIR', 'IDENTITY_NAME', 'TRAJ_ID', 'ROOT_TRAJ_ID', 'TRAJ_DIR',
                    'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'LLM_API_KEY',
                    'SHELLM_CONF_DIR', 'SHELLM_SYSTEM_PROMPT']:
            self.env.pop(key, None)
        self.env.update({'HOME': str(self.root), 'HEADLONG_HOME': str(self.root / 'home'),
                         'PATH': str(self.root / 'bin') + ':' + str(HEADLONG / 'bin') + ':' + str(HEADLONG / 'tools') + ':' + os.environ['PATH'],
                         'STREAM_FIXTURE': str(self.root), 'LLM_PROVIDER': 'openai-compatible',
                         'LLM_API_URL': 'http://127.0.0.1:9/v1/chat/completions',
                         'SHELLM_API_URL': 'http://127.0.0.1:9/v1/chat/completions',
                         'SHELLM_MODEL': 'qwen3.8-27b', 'SHELLM_ENV': 'local', 'SHELLM_EFFORT': 'medium',
                         'SHELLM_RUN_SUMMARY': '0', 'SHELLM_CONTEXT_SCOPE': 'run',
                         'SHELLM_CONTEXT_MAX_BYTES': '7000', 'SHELLM_EMPTY_RESPONSE_RETRIES': '2',
                         'LLM_RETRIES': '0', 'SHELLM_STOP_AFTER_CODE_BLOCK': '0'})

    def run_mode(self, mode):
        return subprocess.run([str(HEADLONG / 'bin/shellm'), '--workdir', str(self.root),
                               '--max-iterations', '1', '--prompt-file', str(self.root / 'prompt')],
                              env=self.env | {'STREAM_MODE': mode}, cwd=self.root,
                              capture_output=True, text=True, timeout=40)

    def test_reasoning_only_stop_continues_without_executing_thought_and_within_budget(self):
        result = self.run_mode('continue')
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertFalse((self.root / 'poisoned').exists())
        self.assertEqual((self.root / 'approved').read_text(), 'approved')
        self.assertEqual((self.root / 'calls').read_text(), '2')
        messages = json.loads((self.root / 'payload-2.json').read_text())['messages']
        conversation = [message for message in messages if message['role'] != 'system']
        self.assertEqual(conversation[0]['content'], self.prompt)
        self.assertLessEqual(len(json.dumps(conversation, ensure_ascii=False, separators=(',', ':')).encode()), 7000)
        thoughts = '\n'.join(message['content'] for message in conversation if message['role'] == 'assistant')
        self.assertIn('REASON_HEAD', thoughts)
        self.assertIn('REASON_TAIL', thoughts)

    def test_truly_empty_stream_remains_failure_without_executing_code(self):
        result = self.run_mode('empty')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'calls').read_text(), '1')
        self.assertFalse((self.root / 'approved').exists())
        self.assertFalse((self.root / 'poisoned').exists())

    def test_interrupted_reasoning_is_not_retried_or_executed(self):
        result = self.run_mode('partial')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'calls').read_text(), '1')
        self.assertFalse((self.root / 'approved').exists())
        self.assertFalse((self.root / 'poisoned').exists())


if __name__ == '__main__':
    unittest.main()
