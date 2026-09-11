import argparse
import json
from unittest import mock
import test_memory as fixtures
import custos_memory as cm
import custos_transport as ct
from test_signal_targeting import group
import custos_signal_targeting as st


class RoutingMemoryTests(fixtures.ResponderTests):
    def routed(self, body='@Friend, please build this'):
        item = group(body)
        self.envelope.update(ambient=not item['directed'], signal_routing=st.routing([(None, item)]))
        if not self.envelope['ambient']:
            del self.envelope['ambient']
        self.log.write_text(cm.encode(self.envelope) + '\n')
        return self.envelope['signal_routing']

    def test_context_only_settles_without_inference_task_or_replay(self):
        self.routed()
        with mock.patch.object(cm, 'run', side_effect=lambda argv, *a, **kw: self.fail('unexpected inference') if argv[0] == 'llm' else self.real_run(argv, *a, **kw)):
            first = cm.response(self.store, self.request)
            second = cm.response(self.store, self.request)
        self.assertEqual(first['decision'], 'no-reply')
        self.assertTrue(second['replayed'])
        record = self.store.request('operator:42')[4]
        self.assertEqual(record['status'], 'completed')
        self.assertFalse(record['response']['inference'])
        self.assertFalse(cm.is_task(record))
        self.assertEqual(self.outgoing(), [])
        self.assertEqual(cm.replay_unanswered(self.store, older_than=0)['queued'], [])
        self.assertFalse(any(s.get('type') == 'action' for s in self.steps()))

    def test_transport_metadata_is_durable_and_conflicts_cannot_retarget(self):
        route = self.routed()
        args = argparse.Namespace(request_id='routing-test', sender='signal-group', authority='external',
                                  source_url='', ambient=True, signal_routing=route)
        def native(argv, payload=None):
            if argv == ['custos-memory', 'capture']:
                return cm.encode(self.store.capture(json.loads(payload)))
            return self.real_run(argv, payload)
        with mock.patch.object(ct, 'native', side_effect=native):
            first = ct.send(args, 'Synthetic group context')
            self.assertEqual(first, ct.send(args, 'Synthetic group context'))
            event = next(s for s in self.steps() if s.get('request_id') == 'routing-test')
            self.assertEqual(event['signal_routing'], route)
            incoming, trigger = cm.envelope_payload(event)
            self.assertFalse(self.store.capture(incoming, trigger)['created'])
            args.signal_routing = st.routing([(None, group('@Custos, build this'))])
            with self.assertRaisesRegex(ValueError, 'different content'):
                ct.send(args, 'Synthetic group context')

    def test_routed_reply_and_recovery_keep_eligible_ids(self):
        route = self.routed('@Custos, explain this')
        self.plan = {'reply': 'Here is an explanation.', 'decision': 'reply', 'goal': None,
                     'memories': [], 'person': None, 'reply_to_items': route['eligible']}
        with mock.patch.object(cm, 'run', side_effect=self.model):
            cm.response(self.store, self.request)
            cm.response(self.store, self.request)
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(len(self.outgoing()), 1)
        self.assertEqual(self.store.request('operator:42')[4]['response']['plan']['reply_to_items'], route['eligible'])

    def test_unprocessed_context_only_intake_recovers_to_settled_receipt(self):
        self.routed()
        self.store.capture(*cm.envelope_payload(self.envelope))
        recovered = cm.replay_unanswered(self.store, older_than=0)
        self.assertEqual(len(recovered['queued']), 1)
        with mock.patch.object(cm, 'run', side_effect=lambda argv, *a, **kw: self.fail('unexpected inference') if argv[0] == 'llm' else self.real_run(argv, *a, **kw)):
            cm.response(self.store, self.request)
        self.assertEqual(cm.replay_unanswered(self.store, older_than=0)['queued'], [])
        self.assertEqual(self.outgoing(), [])

for name in dir(fixtures.ResponderTests):
    if name.startswith('test_'):
        setattr(RoutingMemoryTests, name, None)
