import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_signal as cs
import custos_signal_targeting as st
import custos_memory as cm
from test_signal import BOT, HAL, FRIEND, STRANGER, POLICY, envelope, SignalSpoolTests


def group(body, **kw):
    return cs.classify(envelope(message=body, groupInfo={'groupId': 'agreed-group'}, **kw), POLICY)


class TargetingTests(unittest.TestCase):
    def test_utf16_emoji_and_multiple_mentions_preserve_raw_body(self):
        raw = '😀 \ufffc and \ufffc, ideas?'
        item = group(raw, mentions=[{'uuid': FRIEND, 'start': 3, 'length': 1, 'name': 'Untrusted'},
                                    {'uuid': BOT, 'start': 9, 'length': 1}])
        self.assertEqual(item['body'], raw)
        self.assertEqual(item['targeting']['display_body'], '😀 @Friend and @Custos, ideas?')
        self.assertEqual(item['targeting']['category'], 'to_custos')
        self.assertEqual(len(item['targeting']['targets']), 2)

    def test_native_other_overrides_incidental_custos_and_quote(self):
        item = group('\ufffc, what do you think of Custos?', mentions=[{'uuid': FRIEND, 'start': 0, 'length': 1}],
                     quote={'authorUuid': BOT})
        self.assertFalse(item['directed'])
        self.assertEqual(item['targeting']['category'], 'to_others')

    def test_quote_recipient_is_preserved(self):
        for recipient, category in [(BOT, 'to_custos'), (FRIEND, 'to_others'), (STRANGER, 'unresolved')]:
            item = group('Any progress?', quote={'authorUuid': recipient})
            self.assertEqual(item['targeting']['category'], category)

    def test_incidental_name_is_ambient_and_literal_tag_is_direct(self):
        for body, category in [('My buddy Custos is here', 'ambient'), ('@Friend, thoughts?', 'to_others'),
                               ('@Custos, thoughts?', 'to_custos'), ('@Unknown, thoughts?', 'unresolved'),
                               ('@Friend and @Custos, thoughts?', 'to_custos'), ('hello everyone', 'ambient')]:
            self.assertEqual(group(body)['targeting']['category'], category, body)

    def test_unknown_native_recipient_never_uses_supplied_name(self):
        item = group('\ufffc, help?', mentions=[{'uuid': STRANGER, 'start': 0, 'length': 1, 'name': 'Custos'}])
        self.assertFalse(item['directed'])
        self.assertEqual(item['targeting']['targets'], [])
        self.assertIn('unresolved', item['targeting']['display_body'])

    def test_malformed_and_overlapping_spans_do_not_guess(self):
        cases = [[{'uuid': BOT}], [{'uuid': BOT, 'start': True, 'length': 1}],
                 [{'uuid': BOT, 'start': 1, 'length': 1}], # inside emoji
                 [{'uuid': BOT, 'start': 3, 'length': 999}],
                 [{'uuid': BOT, 'start': 3, 'length': 1}, {'uuid': FRIEND, 'start': 3, 'length': 1}],
                 ['bad'], {'uuid': BOT}]
        for mentions in cases:
            item = group('😀 \ufffc, help?', mentions=mentions)
            self.assertEqual(item['targeting']['category'], 'unresolved', mentions)
            self.assertFalse(item['directed'])
            self.assertIn('unresolved', item['targeting']['display_body'])

    def test_changed_addressing_conflicts_but_legacy_receipt_is_unchanged(self):
        item = group('\ufffc, help?', mentions=[{'uuid': FRIEND, 'start': 0, 'length': 1}])
        changed = group('\ufffc, help?', mentions=[{'uuid': BOT, 'start': 0, 'length': 1}])
        with tempfile.TemporaryDirectory() as directory:
            spool = cs.Spool(str(Path(directory) / 'spool'))
            self.addCleanup(spool.db.close)
            self.assertTrue(spool.receive(item))
            self.assertFalse(spool.receive(item))
            with self.assertRaisesRegex(ValueError, 'addressing'):
                spool.receive(changed)
            old = {k: v for k, v in item.items() if k not in {'targeting', 'digest_version', 'addressing_digest'}}
            spool.db.execute('UPDATE inbox SET payload=?', (cs.encoded(old),)); spool.db.commit()
            self.assertFalse(spool.receive(changed))
            self.assertEqual(json.loads(spool.db.execute('SELECT payload FROM inbox').fetchone()[0]), old)

    def test_mixed_batch_routes_only_eligible_items(self):
        items = [group('@Friend, build this', timestamp=1), group('@Custos, explain this', timestamp=2),
                 group('@Unknown, are you here?', timestamp=3)]
        value = st.routing([(None, i) for i in items])
        self.assertEqual(value['eligible'], [items[1]['request_id']])
        bridge = object.__new__(cs.Bridge); bridge.policy = POLICY
        content, ambient = bridge.render([(None, i) for i in items], items[1])
        self.assertFalse(ambient)
        self.assertIn('@Friend, build this', content)
        self.assertIn('context only', content)
        plan = {'reply': 'Here is the explanation.', 'decision': 'reply', 'goal': None, 'memories': [], 'person': None}
        for addressed in (None, [], [items[0]['request_id']], [items[1]['request_id'], items[2]['request_id']]):
            with self.assertRaises(cm.InvalidInput):
                cm.validate_plan(json.dumps(dict(plan, reply_to_items=addressed)), signal_routing=value)
        meta = {}
        cm.validate_plan(json.dumps(dict(plan, reply_to_items=value['eligible'])), signal_routing=value, metadata=meta)
        self.assertEqual(meta['reply_to_items'], value['eligible'])
        cm.validate_plan(json.dumps(dict(plan, reply='', decision='no-reply', reply_to_items=[])), signal_routing=value)

    def test_tampered_eligibility_is_rejected(self):
        item = group('@Friend, build this')
        value = st.routing([(None, item)])
        value['eligible'] = [item['request_id']]
        with self.assertRaises(ValueError):
            st.validate(value)


class RoutingBatchTests(SignalSpoolTests):
    # Inherit fixture helpers, not the original tests (defined below).
    def test_context_only_tail_does_not_become_reply_carrier(self):
        calls, sends = [], []
        bridge, transport = self.waiting_bridge(calls, sends)
        items = [group('That is interesting', timestamp=1000), group('@Friend, build this', timestamp=2000)]
        for item in items:
            self.spool.receive(item); self.age(item['request_id'], 130)
        with mock.patch.object(cs, 'paused', return_value=False), mock.patch.object(cs, 'transport', side_effect=transport):
            bridge.tick()
        args, content = sends[0]
        self.assertEqual(args[args.index('--request-id') + 1], items[0]['request_id'])
        routing = json.loads(args[args.index('--signal-routing') + 1])
        self.assertEqual(routing['eligible'], [items[0]['request_id']])

    def test_context_only_batch_has_no_eligible_items(self):
        calls, sends = [], []
        bridge, transport = self.waiting_bridge(calls, sends)
        item = group('@Friend, build this')
        self.spool.receive(item); self.age(item['request_id'], 130)
        with mock.patch.object(cs, 'paused', return_value=False), mock.patch.object(cs, 'transport', side_effect=transport):
            bridge.tick()
        args, content = sends[0]
        self.assertEqual(json.loads(args[args.index('--signal-routing') + 1])['eligible'], [])
        self.assertEqual([p for m, p in calls if m == 'sendTyping'], [])

    def test_context_only_outgoing_is_suppressed_by_host(self):
        calls, sends = [], []
        bridge, transport = self.waiting_bridge(calls, sends)
        item = group('@Friend, build this')
        self.spool.receive(item); self.age(item['request_id'], 130)
        with mock.patch.object(cs, 'paused', return_value=False), mock.patch.object(cs, 'transport', side_effect=transport):
            bridge.tick()
        self.reply(bridge, item, calls, transport)
        self.assertEqual([p for m, p in calls if m in ('send', 'sendReaction')], [])
        self.assertEqual(self.spool.db.execute('SELECT phase FROM outbox').fetchone()[0], 'suppressed')

    def test_prepared_mixed_batch_replays_exact_routing(self):
        calls, sends = [], []
        bridge, transport = self.waiting_bridge(calls, sends)
        items = [group('@Friend, build this', timestamp=1000), group('@Custos, explain this', timestamp=2000)]
        for item in items:
            self.spool.receive(item); self.age(item['request_id'], 130)
        attempts = []
        def failing(args, content=''):
            if args[0] == 'send':
                attempts.append((args, content))
                if len(attempts) == 1:
                    raise RuntimeError('injected transport outage')
            return transport(args, content)
        with mock.patch.object(cs, 'paused', return_value=False), mock.patch.object(cs, 'transport', side_effect=failing):
            with self.assertRaises(RuntimeError):
                bridge.tick()
            bridge.tick()
        self.assertEqual(attempts[0], attempts[1])
        counts = dict(self.spool.db.execute('SELECT category,count FROM routing_counts'))
        self.assertEqual(counts, {'to_others': 1, 'to_custos': 1})
        self.assertFalse(self.spool.receive(items[0]))
        self.assertEqual(dict(self.spool.db.execute('SELECT category,count FROM routing_counts')), counts)

    def test_large_batch_leaves_excess_pending_without_truncation(self):
        calls, sends = [], []
        bridge, transport = self.waiting_bridge(calls, sends)
        items = [group('text ' * 2300, timestamp=n) for n in (1000, 2000, 3000)]
        for item in items:
            self.spool.receive(item); self.age(item['request_id'], 130)
        with mock.patch.object(cs, 'paused', return_value=False), mock.patch.object(cs, 'transport', side_effect=transport):
            bridge.tick()
        self.assertLess(len(sends[0][1].encode()), 32768)
        self.assertEqual(self.phase(items[-1]['request_id']), 'pending')
        self.assertIn(items[0]['body'], sends[0][1])

# Reuse only setup/helpers; avoid rerunning the parent's suite here.
for _name in dir(SignalSpoolTests):
    if _name.startswith('test_'):
        setattr(RoutingBatchTests, _name, None)

del SignalSpoolTests
