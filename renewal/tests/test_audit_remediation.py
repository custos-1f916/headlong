import copy
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_admission as admission
import custos_memory as cm
import custos_signal as cs
import custos_actions as ca
from custos_square import Square, Store, APIError
from test_signal import POLICY, FRIEND, envelope
from test_memory import MemoryFixture


class ReceiptTests(unittest.TestCase):
    def test_newline_repair_checks_author_target_time_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            self.addCleanup(store.db.close)
            payload = {'post_id': 44, 'parent_id': 9, 'body': 'Exact result\n'}
            raw = json.dumps(payload)
            with store.db:
                store.db.execute('INSERT INTO outbound VALUES(?,?,?,?,?,?)',
                                 ('x', 'comment', raw, 'uncertain', json.dumps({'write_response': {'comment_id': 7}}), 2000))
            api = Square(store)
            valid = dict(payload, body='Exact result', author='custos', id=7, created_at=2000000)
            for wrong in ({'body': 'Exact  result'}, {'body': 'Exact result '}, {'author': 'other'},
                          {'id': 8}, {'post_id': 45}, {'parent_id': 10}, {'created_at': 1000}):
                with patch.object(api, 'get', return_value={'comment': {**valid, **wrong}}), patch.object(api, 'request') as send:
                    with self.assertRaises(APIError): api.receipt('x')
                    send.assert_not_called()
            with patch.object(api, 'get', return_value={'comment': valid}), patch.object(api, 'request') as send:
                self.assertEqual(api.receipt('x')['status'], 'delivered')
                send.assert_not_called()
            self.assertEqual(store.db.execute('SELECT payload FROM outbound').fetchone()[0], raw)
            with patch.object(api, 'request') as send:
                self.assertEqual(api.write('x', 'comment', {**payload, 'body': 'Exact result'})['status'], 'delivered')
                send.assert_not_called()
            with patch.object(api, 'request') as send, patch.object(api, 'validate'), patch.object(api, 'get', return_value={'handle': 'custos', 'today': {'comments_remaining': 20}}):
                self.assertEqual(api.write('new-id', 'comment', {**payload, 'body': 'Exact result'})['request_id'], 'x')
                send.assert_not_called()


class AdmissionTests(unittest.TestCase):
    def test_denials_coalesce_and_recovery_records_once(self):
        with tempfile.TemporaryDirectory() as root, patch.object(admission, 'probe', side_effect=[('all_backends_unavailable', 300), ('admitted', 5)]) as probe:
            with patch.object(admission.time, 'time', return_value=100):
                a = admission.check(root, 'http://fixture')
                for _ in range(20): self.assertEqual(admission.check(root, 'http://fixture'), a)
            self.assertEqual(probe.call_count, 1)
            with patch.object(admission.time, 'time', return_value=401):
                b = admission.check(root, 'http://fixture')
            self.assertEqual(b['state'], 'admitted')
            self.assertEqual(len(b['transitions']), 2)
    def test_connection_failure_is_not_operator_pause(self):
        with patch.object(admission.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = OSError('unreachable')
            self.assertEqual(admission.probe('http://fixture'), ('gateway_unreachable', 300))


class PeerCliTests(unittest.TestCase):
    def test_inspection_and_invalid_verbs_never_send_or_release(self):
        path = Path(__file__).resolve().parents[1] / 'bin/ask-agent'
        loader = importlib.machinery.SourceFileLoader('ask_fixture', str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec); loader.exec_module(module)
        for args, expected in ((['list'], {'action': 'signal-asks'}),
                               (['status', 'ask-1'], {'action': 'status', 'request_id': 'ask-1'})):
            with patch.object(sys, 'argv', ['ask-agent'] + args), patch.object(module, 'call', return_value=(200, {'ok': True})) as call, patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(module.main(), 0)
                call.assert_called_once_with(expected)
        for args in ([], ['status'], ['list', 'oops'], ['hello'], ['ask']):
            with patch.object(sys, 'argv', ['ask-agent'] + args), patch.object(module, 'call') as call, patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit): module.main()
                call.assert_not_called()


class SchedulingTests(MemoryFixture):
    def test_gate_excludes_stale_and_expiry_then_becomes_actionable(self):
        gid = self.store.capture(self.payload)['goal_id']
        item = self.store.find(gid); rec = item[4]
        rec['response'] = {'state': 'sent', 'plan': {'reply': 'on it', 'decision': 'defer', 'goal': rec['goal'], 'memories': []}}
        rec['received_at'] = '2020-01-01T00:00:00+00:00'
        self.store.save(item, rec)
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
        self.store.update({'goal_id': gid, 'not_before': future})
        result = self.store.context()
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['deferred'][0]['goal_id'], gid)
        self.assertEqual(cm.expire_asks(self.store), [])
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
        self.store.update({'goal_id': gid, 'not_before': past})
        result = self.store.context()
        self.assertEqual(result['total'], 1)
        self.assertFalse(result['goals'][0].get('stale'))
        self.assertEqual(cm.expire_asks(self.store), [])
        with self.assertRaises(cm.InvalidInput): self.store.update({'goal_id': gid, 'not_before': '2026-09-20T12:00:00'})

    def test_newer_superseded_person_cannot_win(self):
        people = cm.People(self.store)
        target = people.save('kim', 'Kim', {'notes': 'Detailed canonical facts.', 'aliases': []}, 'kim')
        meta = {'person_key': 'kim', 'display': 'Kim', 'updated': '2999-01-01', 'aliases': [], 'routes': []}
        body = 'Person: Kim\n\nSuperseded duplicate; canonical note ' + target + cm.PERSON_MARKER + json.dumps(meta)
        stub = self.store.commit(body, memory_type='person')
        self.assertNotEqual(stub, target)
        self.assertEqual(people.find('kim')[0][3]['id'], target)
        self.assertIn('Detailed canonical facts.', people.context())
        self.assertNotIn('Superseded duplicate', people.context())

    def test_merge_preserves_legacy_headingless_and_long_prose(self):
        people = cm.People(self.store)
        target = people.save('jack', 'Jack', {'notes': 'Current facts.', 'aliases': []}, 'jack')
        notes = 'First paragraph must survive.\n\n' + ('Detailed existing fact. ' * 100)
        meta = {'person_key': 'jack', 'display': 'Jack', 'updated': '2026-01-01', 'aliases': [], 'routes': []}
        source = self.store.commit(notes + '\nCustos person note v1: ' + json.dumps(meta), memory_type='person')
        result = people.merge(source, target)
        self.assertTrue(Path(result['archived']).exists())
        self.assertIn(notes, people.find('jack')[2])
        self.assertIn('Current facts.', people.find('jack')[2])


class ErrorBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = copy.deepcopy(POLICY); self.policy['people'][FRIEND]['bot'] = True
        self.path = self.root / 'policy.json'; self.path.write_text(json.dumps(self.policy))
        self.spool = cs.Spool(self.root / 'spool.sqlite'); self.addCleanup(self.spool.db.close)
        self.bridge = cs.Bridge(self.path, self.spool, str(self.root / 'actions.sqlite'))
        self.bridge.rpc = Mock(return_value={})
        self.clock = 2000000
    def incoming(self, text):
        self.clock += 1000
        item = cs.classify(envelope(sender=FRIEND, message=text, timestamp=self.clock), self.policy)
        self.spool.receive(item)
        return item
    def test_shared_claim_budget_changing_errors_restart_and_recovery(self):
        a = self.incoming('Internal error. Please try again. $0.01')
        conv = a['conversation']
        self.assertIsNone(self.bridge.claim_bot_error(conv, 'outbox:responder', a['request_id']))
        self.assertIn('claimed', self.bridge.claim_bot_error(conv, 'action:social'))
        b = self.incoming('Backend unavailable. Ask me to continue. $0.02')
        self.assertIsNone(self.bridge.claim_bot_error(conv, 'action:second', b['request_id']))
        c = self.incoming('Service unavailable. $0.03')
        restarted = cs.Bridge(self.path, self.spool, self.bridge.actions_state)
        self.assertIn('budget', restarted.claim_bot_error(conv, 'outbox:third', c['request_id']))
        self.incoming('Recovered; here is the substantive answer.')
        self.assertIsNone(restarted.claim_bot_error(conv, 'action:normal'))
        self.assertIn('stale', restarted.claim_bot_error(conv, 'outbox:late', a['request_id']))
        d = self.incoming('Internal error again')
        self.assertIsNone(restarted.claim_bot_error(conv, 'action:new-episode', d['request_id']))
    def test_preexisting_send_and_proactive_queue_share_claim(self):
        a = self.incoming('Internal error. Please try again.')
        with self.spool.db:
            self.spool.db.execute("INSERT INTO outbox(id,request_id,content,phase,created) VALUES(?,?,?,'submitted',?)",
                                  ('prior', a['request_id'], 'Please continue', 2002))
        channel = ca.Channel(self.bridge.actions_state, self.path)
        target = self.policy['people'][FRIEND]['label']
        channel.handle({'action': 'signal-send', 'request_id': 'social', 'target': target, 'message': 'Try again'})
        with patch.object(cs, 'paused', return_value=False): self.bridge.proactive()
        self.assertEqual(channel.handle({'action': 'status', 'request_id': 'social'})['phase'], 'blocked')
        self.bridge.rpc.call.assert_not_called()
