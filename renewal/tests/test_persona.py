"""Personal revisions use temporary identities; no model or live transports."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

RENEWAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RENEWAL))
import custos_dream as d
import custos_memory as cm
from custos_persona import Persona, render, MAX_WORDS


class PersonaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.charter = self.root / 'core_identity_prompt.md'
        self.original = 'I pursue questions. I prefer careful explanations.'
        self.boundary = '## Hard lines\n\nRespect consent, privacy and the operator pause.\n'
        self.charter.write_text(self.original+'\n\n'+self.boundary)
        self.store = cm.Store(self.root/'memories')
        self.now = dt.datetime(2026,9,16,9,30,tzinfo=dt.timezone.utc)
        self.dream = d.Dream(self.store, config=d.DEFAULTS, clock=lambda:self.now)
        self.persona = Persona(self.dream)
        env = mock.patch.dict(os.environ, {'TRAJ_ID':'', 'IDENTITY_DIR':str(self.root),
                                          'MEM_DIR':str(self.store.directory)})
        env.start(); self.addCleanup(env.stop)

    def ready(self, verdict='revise'):
        self.dream.begin()
        current = self.persona.show()
        self.persona.reflect(current['expected'], verdict, 'Trajectory event X; note Y supplies counterevidence.')
        return current

    def change(self, current, body='I pursue questions. I start with concise explanations.'):
        return self.persona.revise(current['expected'], body, 'Conversation X and experiment Y.',
                                   'Replace preference for careful explanations with concise first answers.',
                                   'Did concise first answers preserve the relevant caveats?')

    def test_founding_render_is_read_only_and_preserves_boundary(self):
        before = {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.persona.show()['body'], self.original)
        self.assertTrue(render(self.root).endswith(self.boundary))
        self.assertEqual(before, {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        # The actual founding charter must fit too.
        self.assertIn('## Hard lines', render(RENEWAL/'identity'))

    def test_revision_requires_current_reflection_and_active_window(self):
        current = self.persona.show()
        with self.assertRaises(ValueError): self.change(current)
        self.dream.begin()
        with self.assertRaises(ValueError): self.change(current)
        self.persona.reflect(current['expected'], 'keep', 'No counterevidence yet.')
        with self.assertRaises(ValueError): self.change(current)
        self.persona.reflect(current['expected'], 'revise', 'Evidence X.')
        self.now += dt.timedelta(minutes=20)
        with self.assertRaises(ValueError): self.change(current)
        self.assertFalse(self.persona.path.exists())

    def test_cas_covers_operator_charter_and_persona_state(self):
        current = self.ready()
        self.charter.write_text(self.charter.read_text()+'An additional operator boundary.\n')
        with self.assertRaises(ValueError): self.change(current)
        with self.assertRaises(ValueError): self.persona.reflect(current['expected'],'revise','X')
        fresh = self.persona.show()
        self.persona.reflect(fresh['expected'],'revise','Reconsidered with new boundary.')
        self.change(fresh)
        with self.assertRaises(ValueError): self.change(fresh)

    def test_bounds_no_additive_edit_and_no_operator_section(self):
        current = self.ready()
        for body in [self.original, self.original+' I like stories.',
                     'A new rule. '+self.original, 'word '*(MAX_WORDS+1),
                     'x'*10001, 'New prose\n'+self.boundary, '']:
            with self.subTest(body=body[:30]):
                with self.assertRaises(ValueError): self.change(current, body)
        self.assertFalse(self.persona.path.exists())
        self.assertEqual(self.dream.active()['edits'], [])

    def test_change_receipts_prompt_and_daily_limit(self):
        current = self.ready(); old = self.charter.read_bytes()
        entry = self.change(current)
        self.assertEqual(entry['status'], 'applied')
        self.assertEqual(Path(entry['backup']).read_text().strip(),self.original)
        self.assertEqual(d.sha(Path(entry['candidate']).read_bytes()),entry['after_sha256'])
        self.assertIn('-I pursue questions.',entry['diff'])
        self.assertEqual(len(self.dream.active()['edits']),1)
        self.assertEqual(self.charter.read_bytes(),old)
        text = render(self.root)
        self.assertIn('concise explanations', text)
        self.assertNotIn('careful explanations',text)
        self.assertTrue(text.endswith(self.boundary))
        fresh = self.persona.show()
        self.persona.reflect(fresh['expected'],'revise','Another event.')
        with self.assertRaises(ValueError): self.change(fresh, 'A second revision.')
        self.dream.finish('Changed a habit, preserving boundaries.')
        report = (self.dream.root/self.dream.day()/'report.md').read_text()
        self.assertIn('Replaced/retired:',report)
        self.assertIn('2026-09-19',report)

    def test_followup_carries_forward_and_reversal_has_its_own_history(self):
        entry = self.change(self.ready())
        self.dream.finish('Review later.')
        self.now += dt.timedelta(days=3)
        current = self.dream.begin()['persona']
        self.assertEqual(current['follow_up']['review_on_or_after'],self.dream.day())
        self.assertIsNone(current['last_reflection'])
        self.persona.reflect(current['expected'],'revert','Three conversations lost crucial caveats.')
        undone = self.change(current, self.original)
        self.assertNotEqual(entry['id'],undone['id'])
        self.assertEqual(self.persona.show()['body'], self.original)
        self.assertTrue(Path(entry['candidate']).exists())

    def test_crash_reconciles_and_persona_counts_against_memory_edit_budget(self):
        self.dream.config['max_edits'] = 1
        current = self.ready(); real = d.write_json
        def crash(path, value):
            if path.parent.name=='changes' and value.get('status')=='applied':
                raise OSError('simulated interruption after atomic write')
            return real(path,value)
        with mock.patch.object(d,'write_json',side_effect=crash):
            with self.assertRaises(OSError): self.change(current)
        active = self.dream.active()
        self.assertEqual(len(active['edits']),1)
        self.assertEqual(d.read_json(Path(active['edits'][0]))['status'],'applied')
        fresh = self.persona.show()
        self.persona.reflect(fresh['expected'],'revise','Further evidence.')
        with self.assertRaisesRegex(ValueError,'budget'): self.change(fresh,'Another habit.')

    def test_interruption_before_write_keeps_persona_and_retains_prepared_receipt(self):
        current = self.ready(); real = d.atomic
        def fail(path, raw):
            if path == self.persona.path: raise OSError('disk full')
            return real(path,raw)
        with mock.patch.object(d,'atomic',side_effect=fail):
            with self.assertRaises(OSError): self.change(current)
        self.assertEqual(self.persona.show()['body'],self.original)
        active = self.dream.active()
        self.assertEqual(d.read_json(Path(active['edits'][0]))['status'],'prepared')
        with self.assertRaises(ValueError): self.change(current)

    def test_keep_is_complete_and_missing_reflection_is_honest(self):
        self.ready('keep'); self.dream.finish('No change warranted.')
        self.assertFalse(self.persona.path.exists())
        self.assertEqual(self.persona.show()['last_reflection']['verdict'],'keep')
        self.now += dt.timedelta(days=1)
        self.dream.begin(); self.dream.finish('Urgent work preempted reflection.')
        self.assertIn('Not reviewed', (self.dream.root/self.dream.day()/'report.md').read_text())

    def test_real_identity_prompt_cli_uses_current_description_and_rejects_corruption(self):
        self.change(self.ready())
        (self.root/'info.txt').write_text('name=custos\n')
        env = dict(os.environ, IDENTITY_NAME='custos', PATH=str(RENEWAL/'bin')+os.pathsep+os.environ['PATH'])
        tool = RENEWAL.parent/'runtime/headlong/tools/identity'
        args = ['bash',str(tool),'prompt','--identity-dir',str(self.root)]
        result = subprocess.run(args,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,render(self.root))
        self.persona.path.write_text('{broken json')
        result = subprocess.run(args,env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,'')

    def test_common_prompt_does_not_continue_without_custos_identity(self):
        common = RENEWAL.parent/'runtime/headlong/thinkers/_lib/common.sh'
        script = 'source "$1"; _prompt_section() { return 1; }; IDENTITY_NAME=custos; _build_system_prompt'
        result = subprocess.run(['bash','-c',script,'test',str(common)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,'')


if __name__=='__main__': unittest.main()
