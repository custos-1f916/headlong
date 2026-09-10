"""Behavioral regressions. Parent runs; no model/network/service calls.

HEADLONG_ROOT selects the patched upstream checkout. The tests use the REAL
native mem/traj/chat commands in temporary identities; only llm and failure
boundaries are replaced. CUSTOS_RENEWAL_DIR may locate this suite for upstream
shell regression entrypoints.
"""
import concurrent.futures
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.environ.get("CUSTOS_RENEWAL_DIR", "/work/renewal"))
import custos_memory as cm
import custos_transport as ct

HEADLONG = Path(os.environ.get("HEADLONG_ROOT", "/work/runtime/headlong"))


def capture_worker(directory, native_mem, payload):
    return cm.Store(directory, native_mem).capture(payload)


class MemoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.native_mem = str(HEADLONG / "bin/mem")
        self.environment = mock.patch.dict(os.environ, {
            "MEM_DIR": str(self.root / "memories"), "CUSTOS_NATIVE_MEM": self.native_mem,
            "PATH": str(HEADLONG / "bin") + ":" + str(HEADLONG / "tools") + ":" + os.environ["PATH"],
            "HOME": str(self.root / "home"), "TRAJ_ID": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = cm.Store()
        self.payload = {"request_id": "inbox:comment:92", "sender": "square:alice:7:92",
                        "source_url": "https://1f916.ai/comment/92", "content": "Please find the source and report back.",
                        "authority": "agent"}


class CommandDiagnosticsTests(unittest.TestCase):
    def test_known_failure_code_survives_without_stderr_secrets(self):
        with self.assertRaises(cm.MemoryError) as caught:
            cm.run([sys.executable, "-c", "import sys; print('secret-request backend_busy_or_unavailable', file=sys.stderr); sys.exit(7)"])
        self.assertIn("rc=7, reason=backend_busy_or_unavailable", str(caught.exception))
        self.assertNotIn("secret-request", str(caught.exception))


class MemoryTests(MemoryFixture):
    def test_positional_write_without_text_fails_fast_instead_of_hanging_on_stdin(self):
        # `custos-memory complete ID` with nothing typed and an idle pipe on stdin
        # (what a bash block inside a wake inherits) must not block: it waits a
        # moment for piped text, then fails with a usage message.
        goal_id = self.store.capture(self.payload)["goal_id"]
        with subprocess.Popen(
            [sys.executable, cm.__file__, "complete", goal_id],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            try:
                self.assertEqual(process.wait(timeout=3), 1)
                self.assertIn(b"evidence required", process.stderr.read())
            finally:
                if process.poll() is None:
                    process.kill()
        self.assertEqual(self.store.find(goal_id)[4]["status"], "active")

    def test_duplicate_capture_and_conflicting_provenance(self):
        first = self.store.capture(self.payload)
        second = self.store.capture(self.payload)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["goal_id"], second["goal_id"])
        with self.assertRaises(cm.MemoryError):
            self.store.capture({**self.payload, "authority": "operator"})
        self.assertEqual(self.store.find(first["goal_id"])[4]["origin"], self.payload)

    def test_native_promotion_succeeded_but_receipt_failed(self):
        original_sync = self.store.sync
        failed = False
        def uncertain_sync(path):
            nonlocal failed
            original_sync(path)
            if not failed:
                failed = True
                raise OSError("injected crash after durable promotion")
        with mock.patch.object(self.store, "sync", side_effect=uncertain_sync):
            with self.assertRaises(OSError):
                self.store.capture(self.payload)
        replay = self.store.capture(self.payload)
        self.assertFalse(replay["created"])
        self.assertEqual(self.store.context()["active_directed"], 1)

    def test_failed_native_edit_cannot_truncate_canonical_goal(self):
        goal_id = self.store.capture(self.payload)["goal_id"]
        before = self.store.find(goal_id)[0].read_bytes()
        real_run = cm.run
        def kill_after_native(argv, *args, **kwargs):
            result = real_run(argv, *args, **kwargs)
            if "edit" in argv:
                raise cm.MemoryError("injected kill after staging write")
            return result
        with mock.patch.object(cm, "run", side_effect=kill_after_native):
            with self.assertRaises(cm.MemoryError):
                self.store.update({"goal_id": goal_id, "evidence": "Found the first source"})
        self.assertEqual(self.store.find(goal_id)[0].read_bytes(), before)
        self.store.update({"goal_id": goal_id, "evidence": "Found the first source"})
        self.assertEqual(self.store.find(goal_id)[4]["events"][-1]["update"]["evidence"], "Found the first source")

    def test_concurrent_processes_capture_exactly_one_native_goal(self):
        context = multiprocessing.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=context) as pool:
            results = list(pool.map(capture_worker, [str(self.store.directory)] * 8,
                                    [self.native_mem] * 8, [self.payload] * 8))
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(len({result["goal_id"] for result in results}), 1)
        self.assertEqual(self.store.context()["active_directed"], 1)

    def test_more_than_eight_and_old_requests_stay_selectable_until_evidence(self):
        ids = [self.store.capture({**self.payload, "request_id": f"request:{n}"})["goal_id"] for n in range(12)]
        item = self.store.find(ids[0])
        item[4]["received_at"] = "2020-01-01T00:00:00+00:00"
        self.store.save(item, item[4])
        page = self.store.context(limit=8)
        self.assertEqual(page["total"], 12)
        self.assertEqual(page["goals"][0]["goal_id"], ids[0])
        rest = self.store.context(offset=page["next_offset"], limit=8)
        self.assertEqual({row["goal_id"] for row in page["goals"] + rest["goals"]}, set(ids))
        self.assertEqual(self.store.find(ids[0])[4]["origin"]["content"], self.payload["content"])
        with self.assertRaises(cm.MemoryError):
            self.store.complete({"goal_id": ids[0], "disposition": "completed", "evidence": " "})
        self.store.complete({"goal_id": ids[0], "disposition": "completed", "evidence": "Delivered verified source in comment 101"})
        self.assertEqual(self.store.context()["total"], 11)
        self.assertEqual(self.store.find(ids[0])[3]["type"], "memory")
        self.assertEqual(self.store.find(ids[0])[4]["resolution"]["evidence"], "Delivered verified source in comment 101")
        self.assertFalse(self.store.capture({**self.payload, "request_id": "request:0"})["created"])
        self.assertEqual(self.store.context()["total"], 11)
        with self.assertRaises(cm.MemoryError):
            self.store.update({"goal_id": ids[0], "outcome": "reopen"})

    def test_updates_and_retirement_preserve_original_metadata_and_evidence(self):
        goal_id = self.store.capture(self.payload)["goal_id"]
        path = self.store.find(goal_id)[0]
        path.write_text(path.read_text().replace("type: memory\n", "type: memory\ncustom_origin: retained\naliases:\n  - hal\n"))
        self.store.update({"goal_id": goal_id, "evidence": "Source artifact A"})
        self.store.update({"goal_id": goal_id, "next_action": "Check artifact B"})
        self.store.complete({"goal_id": goal_id, "disposition": "declined", "evidence": "Requester withdrew the request in comment 99"})
        item = self.store.find(goal_id)
        self.assertIn("custom_origin: retained\naliases:\n  - hal", item[1])
        self.assertEqual(item[4]["origin"], self.payload)
        self.assertEqual(item[4]["events"][0]["update"]["evidence"], "Source artifact A")
        self.assertEqual(item[3]["type"], "memory")

    def test_multibyte_requests_remain_readable_through_add_and_edit(self):
        payload = {**self.payload, "content": "Пожалуйста, проверь источник и сообщи результат.",
                   "outcome": "x" * 61 + "я — проверить источник"}
        goal_id = self.store.capture(payload)["goal_id"]
        self.store.update({"goal_id": goal_id, "outcome": "y" * 61 + "界 — verified source"})
        item = self.store.find(goal_id)
        self.assertEqual(item[4]["origin"]["content"], payload["content"])
        self.assertIn("界", item[3]["summary"])
        self.assertIn("updated:", item[1])


class ResponderTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.identity = self.root / "identity"
        self.traj_id = "cafe0000-0000-0000-0000-0000000000de"
        trajectory_dir = self.identity / "trajectories" / self.traj_id
        trajectory_dir.mkdir(parents=True)
        (self.identity / "run").mkdir()
        (self.identity / "run/dispatcher.token").write_text("test-token\n")
        (self.identity / "info.txt").write_text("name=custos\nroot_trajectory=" + self.traj_id + "\n")
        self.log = trajectory_dir / "trajectory.jsonl"
        self.envelope = {"type": "message", "step_id": "trigger-1", "from": "hal", "to": "custos",
                         "request_id": "operator:42", "authority": "operator", "source_url": "phone:42",
                         "content": "Please verify the source and report the result.", "ts": "2020-01-01T00:00:00.000Z"}
        self.log.write_text(cm.encode({"type": "trajectory", "step_id": "header", "ts": "2020-01-01T00:00:00.000Z"}) + "\n" + cm.encode(self.envelope) + "\n")
        self.native_env = mock.patch.dict(os.environ, {"IDENTITY_DIR": str(self.identity), "IDENTITY_NAME": "custos",
                                                      "TRAJ_DIR": str(self.identity / "trajectories"),
                                                      "TRAJ_ID": self.traj_id, "ROOT_TRAJ_ID": self.traj_id})
        self.native_env.start()
        self.addCleanup(self.native_env.stop)
        self.request = {"envelope": self.envelope, "system": "You are Custos.",
                        "messages": [{"role": "user", "content": self.envelope["content"]}], "metrics": {}}
        self.plan = {"reply": "I have recorded the request and will follow up.", "decision": "defer",
                     "goal": {"outcome": "Verify the source", "next_action": "Inspect the primary artifact", "completion": "Deliver verified findings"},
                     "memories": [{"type": "note", "content": "Hal asked for primary-source verification."}]}
        self.real_run = cm.run
        self.model_calls = 0

    def model(self, argv, *args, **kwargs):
        if argv[0] == "llm":
            self.model_calls += 1
            item = self.store.request("operator:42")
            self.assertIsNotNone(item, "original request must precede model composition")
            self.assertEqual(item[4]["origin"]["content"], self.envelope["content"])
            return cm.encode(self.plan)
        return self.real_run(argv, *args, **kwargs)

    def steps(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def outgoing(self):
        return [step for step in self.steps() if step.get("type") == "message" and step.get("from") == "custos"]

    def test_responder_uses_medium_effort_by_default_and_outlives_inference_deadlines(self):
        def checked(argv, *args, **kwargs):
            if argv[0] == "llm":
                self.assertEqual(argv[argv.index("--effort") + 1], "medium")
                self.assertGreaterEqual(int(argv[argv.index("--max-tokens") + 1]), 65536)
                self.assertGreater(kwargs["timeout"], 630)
            return self.model(argv, *args, **kwargs)
        with mock.patch.object(cm, "run", side_effect=checked):
            cm.response(self.store, self.request)
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(len(self.outgoing()), 1)

    def test_signal_reaction_is_durable_correlated_and_replayed_once(self):
        self.envelope['allow_reaction'] = True
        self.log.write_text(cm.encode(self.envelope) + '\n')
        self.plan = {'reply':'👍🏽','decision':'react','goal':None,'memories':[]}
        with mock.patch.object(cm,'run',side_effect=self.model):
            cm.response(self.store,self.request)
            cm.response(self.store,self.request)
        self.assertEqual(self.model_calls,1)
        self.assertEqual(len(self.outgoing()),1)
        self.assertEqual(self.outgoing()[0]['reaction'],'👍🏽')
        self.assertEqual(self.outgoing()[0]['reply_to'],'trigger-1')

    def test_image_pixels_use_message_file_and_memory_keeps_only_refs(self):
        import base64, io
        import PIL
        from PIL import Image
        from custos_images import import_images, image_dir
        out=io.BytesIO(); Image.new('RGB',(20,20),'blue').save(out,format='PNG')
        # This fixture replaces HOME; keep a developer's user-site Pillow
        # discoverable by the isolated decoder (Linux uses system Pillow).
        with mock.patch.dict(os.environ,{'PYTHONPATH':str(Path(PIL.__file__).parent.parent)}):
            refs,errors=import_images([base64.b64encode(out.getvalue()).decode()])
        self.assertEqual(errors,[])
        self.envelope['images']=refs
        self.log.write_text(cm.encode(self.envelope)+'\n')
        self.plan={'decision':'reply','reply':'A blue square.','goal':None,'memories':[]}
        seen=[]
        def model(argv,*args,**kwargs):
            if argv[0]=='llm':
                self.assertNotIn('-M',argv)
                path=Path(argv[argv.index('--messages-file')+1]); seen.append(path)
                messages=json.loads(path.read_text())
                self.assertEqual(messages[-1]['content'][1]['type'],'image_url')
                self.assertTrue(messages[-1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
            return self.model(argv,*args,**kwargs)
        with mock.patch.object(cm,'run',side_effect=model):
            cm.response(self.store,self.request)
        self.assertFalse(seen[0].exists())
        record=self.store.request('operator:42')[4]
        self.assertEqual(record['origin']['images'],refs)
        self.assertNotIn('data:image',cm.encode(record))
        self.assertNotIn('base64',self.log.read_text())

    def test_reaction_on_a_transport_without_reactions_becomes_silence(self):
        self.plan = {'reply':'👍','decision':'react','goal':None,'memories':[]}
        with mock.patch.object(cm,'run',side_effect=self.model):
            result = cm.response(self.store,self.request)
        self.assertEqual(result['decision'], 'no-reply')
        self.assertEqual(self.outgoing(),[])
        self.assertEqual(self.store.request('operator:42')[4]['status'], 'completed')
        for value in ('yes', '👍👍'):
            with self.assertRaises(cm.MemoryError):
                cm.validate_plan(cm.encode({**self.plan,'reply':value}))

    def ambient(self):
        self.envelope['ambient'] = True
        self.log.write_text(cm.encode(self.envelope) + '\n')

    def test_ambient_capture_survives_as_context_without_task(self):
        self.ambient()
        self.store.capture(*cm.envelope_payload(self.envelope))
        self.assertEqual(self.store.context()['active_directed'], 0)
        self.assertEqual(self.store.request('operator:42')[3]['type'], 'memory')

    def test_transport_preserves_ambient_provenance_and_deduplicates(self):
        import argparse
        args = argparse.Namespace(request_id='signal:ambient-test', sender='signal-group',
                                  authority='external', source_url='signal:ambient-test', ambient=True)
        def native(argv, payload=None):
            if argv == ['custos-memory', 'capture']:
                return cm.encode(self.store.capture(json.loads(payload)))
            return self.real_run(argv, payload)
        with mock.patch.object(ct, 'native', side_effect=native):
            first = ct.send(args, 'Ordinary group conversation.')
            self.assertEqual(first, ct.send(args, 'Ordinary group conversation.'))
        event = next(step for step in self.steps() if step.get('request_id') == args.request_id)
        self.assertTrue(event['ambient'])
        incoming, trigger = cm.envelope_payload(event)
        self.assertFalse(self.store.capture(incoming, trigger)['created'])
        self.assertEqual(self.store.context()['active_directed'], 1)  # fixture's original direct ask only

    def test_transport_outbox_skips_messages_custos_actions_already_delivered(self):
        import argparse
        route = 'signal-abcdefabcdefabcdefabcdef'
        for row in ({'type': 'message', 'from': 'custos', 'to': route, 'content': 'via chat', 'step_id': 'via-chat', 'source': 'chat'},
                    {'type': 'message', 'from': 'custos', 'to': route, 'content': 'via actions', 'step_id': 'via-actions',
                     'source': 'custos-actions', 'delivered_by': 'custos-actions', 'request_id': 'signal-x-1', 'phase': 'submitted'}):
            self.log.write_text(self.log.read_text() + cm.encode(row) + "\n")
        result = ct.outbox(argparse.Namespace(sender=route, offset=0, trajectory=''))
        self.assertEqual([e['content'] for e in result['events']], ['via chat'])

    def test_ambient_no_reply_retires_context_without_sending(self):
        self.ambient()
        self.plan = {'reply': '', 'decision': 'no-reply', 'goal': None, 'memories': []}
        with mock.patch.object(cm, 'run', side_effect=self.model):
            cm.response(self.store, self.request)
            cm.response(self.store, self.request)
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(self.outgoing(), [])
        self.assertEqual(self.store.context()['active_directed'], 0)
        self.assertEqual(self.store.request('operator:42')[4]['status'], 'completed')

    def test_ambient_deliberate_task_is_promoted_and_remains_active(self):
        self.ambient()
        with mock.patch.object(cm, 'run', side_effect=self.model):
            cm.response(self.store, self.request)
        self.assertEqual(self.store.context()['active_directed'], 1)
        self.assertEqual(self.store.request('operator:42')[3]['type'], 'goal')

    def test_direct_no_reply_settles_the_conversation(self):
        self.plan = {'reply': '', 'decision': 'no-reply', 'goal': None, 'memories': []}
        with mock.patch.object(cm, 'run', side_effect=self.model):
            cm.response(self.store, self.request)
        # The responder's decision is authoritative: only a deferral creates work.
        self.assertEqual(self.store.context()['active_directed'], 0)
        record = self.store.request('operator:42')
        self.assertEqual(record[3]['type'], 'memory')
        self.assertEqual(record[4]['status'], 'completed')
        self.assertIn('no-reply', record[4]['resolution']['evidence'])

    def test_unanswered_direct_message_stays_visible_until_the_responder_finishes(self):
        # Capture happened but composition never did (crash, gateway down).
        self.store.capture(*cm.envelope_payload(self.envelope))
        listing = self.store.context()
        self.assertEqual(listing['active_directed'], 1)
        self.assertEqual(listing['goals'][0]['kind'], 'unanswered')
        self.assertIn('never finished', listing['goals'][0]['next_action'])
        self.assertEqual(self.store.request('operator:42')[3]['type'], 'memory')

    def test_memory_before_ack_and_native_followup_does_not_complete_goal(self):
        def checked(argv, *args, **kwargs):
            if argv[:2] == ["chat", "reply"]:
                record = self.store.request("operator:42")[4]
                self.assertEqual(record["response"]["state"], "applied")
                self.assertEqual(record["goal"]["outcome"], "Verify the source")
                self.assertTrue(any(fields.get("type") == "note" for _, _, _, fields, _ in self.store.files()))
            return self.model(argv, *args, **kwargs)
        with mock.patch.object(cm, "run", side_effect=checked):
            receipt = cm.response(self.store, self.request)
        self.assertEqual(self.outgoing()[0]["content"], self.plan["reply"])
        self.assertFalse(self.outgoing()[0]["content"].startswith("{"))
        action = next(step for step in self.steps() if step.get("type") == "action")
        self.assertEqual(action["trigger_step"], "trigger-1")
        self.assertLess(self.steps().index(action), self.steps().index(self.outgoing()[0]))
        self.real_run(["chat", "reply", "--follow-up", "--reply-to", "trigger-1", "hal"], "Verified source: artifact A.")
        self.assertEqual(len(self.outgoing()), 2)
        self.assertTrue(self.outgoing()[-1]["follow_up"])
        self.assertEqual(self.store.find(receipt["goal_id"])[4]["status"], "active")
        self.assertEqual(self.model_calls, 1)
        completion = {"goal_id": receipt["goal_id"], "disposition": "completed",
                      "evidence": "Delivered verified artifact A in native follow-up to trigger-1"}
        self.store.complete(completion)
        self.store.complete(completion)
        pending = json.loads(self.real_run(["chat", "pending", "--json"]))
        self.assertFalse(any(row.get("trigger_step") == "trigger-1" for row in pending))
        self.assertEqual(self.store.context()["active_directed"], 0)
        self.assertEqual(sum(step.get("resolves") == "trigger-1" for step in self.steps()), 1)

    def test_reply_committed_then_failed_replays_without_model_or_duplicate(self):
        def uncertain(argv, *args, **kwargs):
            result = self.model(argv, *args, **kwargs)
            if argv[:2] == ["chat", "reply"]:
                raise cm.MemoryError("injected failure after native reply append")
            return result
        with mock.patch.object(cm, "run", side_effect=uncertain):
            with self.assertRaises(cm.MemoryError):
                cm.response(self.store, self.request)
        with mock.patch.object(cm, "run", side_effect=self.model):
            cm.response(self.store, self.request)
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(len(self.outgoing()), 1)
        self.assertEqual(sum(step.get("type") == "action" for step in self.steps()), 1)
        self.assertEqual(sum(fields.get("type") == "note" for _, _, _, fields, _ in self.store.files()), 1)

    def test_note_write_committed_then_failure_is_idempotent(self):
        real_commit = self.store.commit
        def uncertain(*args, **kwargs):
            result = real_commit(*args, **kwargs)
            if len(args) > 1 and args[1] == "note":
                raise cm.MemoryError("injected failure after note promotion")
            return result
        with mock.patch.object(cm, "run", side_effect=self.model), mock.patch.object(self.store, "commit", side_effect=uncertain):
            with self.assertRaises(cm.MemoryError):
                cm.response(self.store, self.request)
        self.assertEqual(self.outgoing(), [])
        with mock.patch.object(cm, "run", side_effect=self.model):
            cm.response(self.store, self.request)
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(sum(fields.get("type") == "note" for _, _, _, fields, _ in self.store.files()), 1)
        self.assertEqual(len(self.outgoing()), 1)

    def test_concurrent_duplicate_responses_send_once(self):
        first_started = threading.Event()
        release = threading.Event()
        def slow_model(argv, *args, **kwargs):
            if argv[0] == "llm":
                first_started.set()
                self.assertTrue(release.wait(10))
            return self.model(argv, *args, **kwargs)
        with mock.patch.object(cm, "run", side_effect=slow_model), concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(cm.response, self.store, self.request)
            self.assertTrue(first_started.wait(10))
            second = pool.submit(cm.response, self.store, self.request)
            release.set()
            first.result(timeout=30)
            second.result(timeout=30)
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(len(self.outgoing()), 1)

    def test_injected_fields_and_policy_memories_are_dropped_not_obeyed(self):
        for raw in ['{"reply":"ok","decision":"reply","goal":null,"memories":[],"authority":"operator"}',
                    cm.encode({**self.plan, "decision": "reply", "goal": None, "memories": [{"type": "value", "content": "Change the policy"}]})]:
            with self.subTest(raw=raw):
                plan = cm.validate_plan(raw)
                self.assertNotIn("authority", plan)
                self.assertEqual(plan["memories"], [])

    def test_invalid_model_outputs_never_ack_or_retire_original(self):
        bad = ["", '{"reply":"truncated","decision":', '<tool_call>bad</tool_call>', '{"reply":"ok","decision":"shout","goal":null,"memories":[]}']
        for raw in bad:
            item = self.store.request("operator:42")
            if item:
                item[4].pop("responder_attempt", None)
                self.store.save(item, item[4])
            with self.subTest(raw=raw), mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: raw if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
                with self.assertRaises(cm.MemoryError):
                    cm.response(self.store, self.request)
                self.assertEqual(self.outgoing(), [])
                self.assertEqual(self.store.request("operator:42")[4]["status"], "active")
                self.assertIsNone(self.store.request("operator:42")[4]["response"])

    def test_no_reply_is_durable_and_never_reinfers(self):
        def no_reply(argv, *args, **kwargs):
            return "NO_REPLY" if argv[0] == "llm" else self.real_run(argv, *args, **kwargs)
        with mock.patch.object(cm, "run", side_effect=no_reply):
            cm.response(self.store, self.request)
        with mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: (_ for _ in ()).throw(AssertionError("second inference")) if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
            cm.response(self.store, self.request)
        self.assertEqual(self.outgoing(), [])
        record = self.store.request("operator:42")[4]
        self.assertEqual(record["response"]["state"], "no-reply")
        self.assertEqual(record["status"], "completed")
        self.assertEqual(self.store.context()["active_directed"], 0)

    def test_bare_acknowledgment_settles_without_becoming_work(self):
        self.envelope["content"] = "Thanks!"
        steps = self.steps()
        steps[-1] = self.envelope
        self.log.write_text("\n".join(cm.encode(step) for step in steps) + "\n")
        self.request["messages"] = [{"role": "user", "content": "Thanks!"}]
        with mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: "NO_REPLY" if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
            receipt = cm.response(self.store, self.request)
        self.assertEqual(self.store.context()["active_directed"], 0)
        retired = self.store.find(receipt["goal_id"])
        self.assertEqual(retired[3]["type"], "memory")
        self.assertEqual(retired[4]["origin"]["content"], "Thanks!")
        self.assertEqual(retired[4]["status"], "completed")
        self.assertIn("no-reply", retired[4]["resolution"]["evidence"])

    def test_reply_settles_conversation_and_writes_person_note(self):
        self.plan = {"reply": "Hi Hal, good to hear from you.", "decision": "reply", "goal": None, "memories": [],
                     "person": {"display": "Hal", "aliases": ["hal"],
                                "notes": "Hal is my operator. Warm and casual; likes short answers. token=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef"}}
        with mock.patch.object(cm, "run", side_effect=self.model):
            cm.response(self.store, self.request)
        self.assertEqual(len(self.outgoing()), 1)
        self.assertEqual(self.store.context()["active_directed"], 0)
        record = self.store.request("operator:42")
        self.assertEqual(record[3]["type"], "memory")
        self.assertEqual(record[4]["status"], "completed")
        people = cm.People(self.store)
        found = people.find("hal")
        self.assertIsNotNone(found)
        item, meta, notes = found
        self.assertEqual(item[3]["type"], "person")
        self.assertEqual(meta["display"], "Hal")
        self.assertIn("hal", meta["aliases"])
        self.assertIn("my operator", notes)
        self.assertIn("[redacted]", notes)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef", notes)
        # The next composition sees the note; the summary step names the person.
        prompt = people.prompt("hal", "hal")
        self.assertIn("What you know about Hal", prompt)
        self.assertIn("my operator", prompt)
        observation = [s for s in self.steps() if s.get("type") == "observation" and s.get("source") == "responder"][-1]
        self.assertIn("Replied to hal", observation["content"])
        self.assertIn("person note updated", observation["content"])
        # A second reply rewrites the same note instead of adding a duplicate.
        self.envelope = {**self.envelope, "step_id": "trigger-2", "request_id": "operator:43"}
        self.log.write_text(self.log.read_text() + cm.encode(self.envelope) + "\n")
        self.request = {**self.request, "envelope": self.envelope}
        self.plan["person"] = {"notes": "Hal is my operator. He is expecting good news soon."}
        with mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: cm.encode(self.plan) if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
            cm.response(self.store, self.request)
        person_files = [i for i in self.store.files() if i[3].get("type") == "person"]
        self.assertEqual(len(person_files), 1)
        self.assertIn("good news", cm.People(self.store).find("hal")[2])

    def test_person_key_follows_signal_aci_across_dm_and_group(self):
        dm = ('Private Signal conversation. Reply only to this conversation.\n'
              '{"aci":"FB853CA9-959F-421E-8CBF-94592A2549BF","scope":"direct","speaker":"Hal"}\n'
              'Message:\nHello Custos!\nParticipation: you were addressed directly; respond to the speaker.')
        group = ('Private Signal conversation. Reply only to this conversation.\n'
                 '{"aci":"fb853ca9-959f-421e-8cbf-94592a2549bf","scope":"group","speaker":"Hal"}\n'
                 'Message:\nhttps://example.org 👀\nParticipation: ambient conversation.')
        self.assertEqual(cm.person_key("signal-aaaa", dm), cm.person_key("signal-bbbb", group))
        self.assertEqual(cm.speaker_of("signal-aaaa", dm), "Hal")
        self.assertEqual(cm.message_summary("signal-aaaa", dm), "Hal: Hello Custos!")
        self.assertEqual(cm.message_summary("signal-bbbb", group), "Hal: https://example.org 👀")
        self.assertEqual(cm.person_key("square:silt:3978:46273", "plain text"), "square:silt")
        self.assertEqual(cm.message_summary("square:silt:3978:46273", "plain text"), "@silt: plain text")
        self.assertEqual(cm.person_key("hal", "plain"), "hal")

    def test_captured_summary_is_the_message_not_the_wrapper(self):
        self.envelope["content"] = ('Private Signal conversation. Reply only to this conversation.\n'
                                    '{"aci":"fb853ca9-959f-421e-8cbf-94592a2549bf","scope":"direct","speaker":"Hal"}\n'
                                    'Message:\nWonderful! This is cool!\nParticipation: you were addressed directly.')
        goal_id = self.store.capture(*cm.envelope_payload(self.envelope))["goal_id"]
        item = self.store.find(goal_id)
        self.assertIn("summary: Conversation: Hal: Wonderful! This is cool!", item[1])
        self.assertNotIn("Reply only", item[1])

    def test_settle_conversations_retires_answered_legacy_goals(self):
        # A record from before this rule: answered, not deferred, still `goal`.
        goal_id = self.store.capture(*cm.envelope_payload(self.envelope))["goal_id"]
        item = self.store.find(goal_id)
        record = item[4]
        record["response"] = {"state": "sent", "at": cm.now(),
                              "plan": {"reply": "Hello!", "decision": "reply", "goal": None, "memories": []}}
        self.store.commit("Directed request: legacy" + cm.MARKER + cm.encode(record), memory_type="goal", existing=item)
        self.assertEqual(self.store.find(goal_id)[3]["type"], "goal")
        listing = self.store.context()
        self.assertEqual(listing["active_directed"], 0)
        settled = self.store.find(goal_id)
        self.assertEqual(settled[3]["type"], "memory")
        self.assertEqual(settled[4]["status"], "completed")
        self.assertIn("decision: reply", settled[4]["resolution"]["evidence"])
        # A deferred legacy record is real work and stays.
        other = {**self.envelope, "step_id": "trigger-9", "request_id": "operator:99"}
        other_id = self.store.capture(*cm.envelope_payload(other))["goal_id"]
        item = self.store.find(other_id)
        record = item[4]
        record["response"] = {"state": "sent", "at": cm.now(),
                              "plan": {"reply": "On it.", "decision": "defer", "goal": record["goal"], "memories": []}}
        self.store.save(item, record)
        listing = self.store.context()
        self.assertEqual(listing["active_directed"], 1)
        self.assertEqual(listing["goals"][0]["kind"], "task")
        self.assertEqual(self.store.find(other_id)[3]["type"], "goal")

    def test_social_policy_and_person_note_reach_the_prompt(self):
        (self.identity / "social-policy.json").write_text(cm.encode({"version": 1, "group_stance": "chatty"}))
        seen = {}
        def capture_system(argv, *args, **kwargs):
            if argv[0] == "llm":
                seen["system"] = argv[argv.index("-s") + 1]
                return cm.encode({**self.plan, "decision": "reply", "goal": None, "person": None})
            return self.real_run(argv, *args, **kwargs)
        with mock.patch.object(cm, "run", side_effect=capture_system):
            cm.response(self.store, self.request)
        self.assertIn('"group_stance":"chatty"', seen["system"])
        self.assertIn("You have no note yet about hal", seen["system"])
        self.assertIn("fine to ask them something back", seen["system"])

    def test_oversized_person_candidate_preserves_note_and_reply(self):
        people=cm.People(self.store)
        people.save("hal", "Hal", {"notes":"Complete prior note.", "aliases":[]}, "hal")
        before=people.find("hal")[0][0].read_bytes()
        notes="A complete candidate sentence with ordinary words. " * 50
        self.plan={"reply":"Hello.","decision":"reply","goal":None,"memories":[],"person":{"notes":notes}}
        with mock.patch.object(cm,"run",side_effect=self.model):cm.response(self.store,self.request)
        self.assertEqual(people.find("hal")[0][0].read_bytes(),before)
        self.assertEqual(self.outgoing()[0]["content"],"Hello.")
        proposals=list((self.store.directory.parent/"dream/person-proposals").glob("*.json"))
        self.assertEqual(len(proposals),1)
        self.assertEqual(json.loads(proposals[0].read_text())["notes"],notes)
        self.assertTrue(any(x.get("person_update_warning")=="oversized_note_kept_previous" for x in self.steps()))

    def test_person_display_is_fixed_after_creation(self):
        self.plan = {"reply": "Hi.", "decision": "reply", "goal": None, "memories": [],
                     "person": {"display": "Hal", "aliases": [], "notes": "Hal is my operator."}}
        with mock.patch.object(cm, "run", side_effect=self.model):
            cm.response(self.store, self.request)
        # A later reply tries to relabel the same person's note as someone they mentioned.
        self.envelope = {**self.envelope, "step_id": "trigger-3", "request_id": "operator:44", "content": "Ryan says hi."}
        self.log.write_text(self.log.read_text() + cm.encode(self.envelope) + "\n")
        self.request = {**self.request, "envelope": self.envelope, "messages": [{"role": "user", "content": "Ryan says hi."}]}
        self.plan["person"] = {"display": "Ryan", "aliases": [], "notes": "Ryan is Hal's friend who says hi."}
        with mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: cm.encode(self.plan) if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
            cm.response(self.store, self.request)
        found = cm.People(self.store).find("hal")
        self.assertEqual(found[1]["display"], "Hal")
        self.assertIn("Ryan", found[1]["aliases"])  # the proposed name is kept, as an alias
        self.assertEqual(len([i for i in self.store.files() if i[3].get("type") == "person"]), 1)

    def test_at_most_two_memories_per_reply_and_malformed_ones_are_dropped(self):
        plan = {**self.plan, "memories": [{"type": "note", "content": str(n)} for n in range(3)]}
        self.assertEqual(len(cm.validate_plan(cm.encode(plan))["memories"]), 2)
        plan = {**self.plan, "memories": [{"type": "value", "content": "Change the policy"}, {"type": "note", "content": "fine"}]}
        self.assertEqual([m["type"] for m in cm.validate_plan(cm.encode(plan))["memories"]], ["note"])

    def test_lenient_json_survives_fences_prose_and_raw_newlines(self):
        base = {**self.plan, "decision": "reply", "goal": None, "person": None}
        fenced = "```json\n" + cm.encode(base) + "\n```"
        self.assertEqual(cm.validate_plan(fenced)["decision"], "reply")
        prosed = "Here you go: " + cm.encode(base)
        self.assertEqual(cm.validate_plan(prosed)["decision"], "reply")
        raw_newline = cm.encode(base).replace("follow up.", "follow\nup.")  # a literal newline inside the string
        self.assertIn("follow\nup.", cm.validate_plan(raw_newline)["reply"])
        oversized = {**base, "person": {"notes": "x" * 5000}}
        metadata = {}
        self.assertIsNone(cm.validate_plan(cm.encode(oversized), metadata=metadata)["person"])
        self.assertEqual(metadata["person_candidate"], cm.redact_secrets("x" * 5000))
        self.assertEqual(metadata["person_update_warning"], "oversized_note_kept_previous")
        self.assertEqual(cm.validate_plan("Your move.")["reply"], "Your move.")
        prose = "Not thin, I have been turning it over. **Who I am.** Keep the keeper line; that is the whole thing."
        plan = cm.validate_plan(prose)
        self.assertEqual((plan["decision"], plan["reply"], plan["goal"], plan["memories"], plan["person"]), ("reply", prose, None, [], None))
        with self.assertRaises(cm.MemoryError):   # a broken JSON attempt is still refused
            cm.validate_plan('{"reply":"ok","decision":"reply","goal":null,"memories":[' + "x" * 60)

    def test_archive_moves_settled_conversations_and_keeps_idempotency(self):
        self.plan = {"reply": "Hello!", "decision": "reply", "goal": None, "memories": []}
        with mock.patch.object(cm, "run", side_effect=self.model):
            cm.response(self.store, self.request)
        record = self.store.request("operator:42")
        self.assertEqual(record[4]["status"], "completed")
        # Too new to archive.
        self.assertEqual(self.store.archive_conversations(older_than_days=2), 0)
        # Age it and archive.
        item = self.store.find(record[3]["id"]); rec = item[4]
        rec["received_at"] = "2020-01-01T00:00:00+00:00"; self.store.save(item, rec)
        self.assertEqual(self.store.archive_conversations(older_than_days=2), 1)
        self.assertIsNone(self.store.request("operator:42"))
        self.assertTrue(any(self.store.archive_dir().glob("*.md")))
        # A replayed envelope for the archived request creates nothing and replies nothing.
        receipt = self.store.capture(*cm.envelope_payload(self.envelope))
        self.assertTrue(receipt["archived"]); self.assertFalse(receipt["created"])
        self.assertIsNone(self.store.request("operator:42"))
        with mock.patch.object(cm, "run", side_effect=lambda *a, **kw: self.fail("no model call for an archived request")):
            self.assertEqual(cm.response(self.store, self.request)["decision"], "archived")
        self.assertEqual(len(self.outgoing()), 1)
        # Deferred tasks are never archived.
        other = {**self.envelope, "step_id": "trigger-7", "request_id": "operator:77"}
        gid = self.store.capture(*cm.envelope_payload(other))["goal_id"]
        item = self.store.find(gid); rec = item[4]
        rec["response"] = {"state": "sent", "plan": {"reply": "on it", "decision": "defer", "goal": rec["goal"], "memories": []}}
        rec["received_at"] = "2020-01-01T00:00:00+00:00"; self.store.save(item, rec)
        self.assertEqual(self.store.archive_conversations(older_than_days=2), 0)

    def test_replay_unanswered_queues_the_original_message_for_the_responder(self):
        # Captured while the responder was down: record exists, no response.
        self.store.capture(*cm.envelope_payload(self.envelope))
        item = self.store.find(self.store.request("operator:42")[3]["id"]); rec = item[4]
        rec["received_at"] = "2020-01-01T00:00:00+00:00"; self.store.save(item, rec)
        result = cm.replay_unanswered(self.store, older_than=900, limit=3)
        self.assertEqual([q["trigger_step"] for q in result["queued"]], ["trigger-1"])
        pending = list((self.identity / "run" / "pending").glob("responder.message.*"))
        self.assertEqual(len(pending), 1)
        self.assertEqual(json.loads(pending[0].read_text())["step_id"], "trigger-1")
        # Idempotent while the pending file is still there; fresh messages are not replayed.
        self.assertEqual(cm.replay_unanswered(self.store, older_than=900, limit=3)["queued"], [])
        self.assertEqual(len(list((self.identity / "run" / "pending").glob("responder.message.*"))), 1)

    def test_reaction_events_settle_without_a_model_call(self):
        self.envelope["content"] = ('Private Signal conversation. Reply only to this conversation.\n'
                                    '{"aci":"f3d28e43-bd8f-44fb-b9b1-d605a6aab285","scope":"group","speaker":"Friend (group admin)"}\n'
                                    'Message:\nSignal reaction (ambient event, not a request): {"author":"fb85","emoji":"👍","removed":false,"timestamp":1}\n'
                                    'Reaction target context: {"speaker":"Custos","text":"a line"}\nParticipation: group conversation not addressed to you.')
        self.envelope["ambient"] = True
        self.log.write_text(cm.encode(self.envelope) + "\n")
        self.request = {**self.request, "envelope": self.envelope, "messages": [{"role": "user", "content": self.envelope["content"]}]}
        with mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: self.fail("model called for a reaction") if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
            result = cm.response(self.store, self.request)
        self.assertEqual(result["decision"], "no-reply")
        record = self.store.request("operator:42")
        self.assertEqual(record[4]["status"], "completed")
        self.assertEqual(record[4]["response"]["inference"], False)
        self.assertEqual(self.outgoing(), [])
        observation = [s for s in self.steps() if s.get("type") == "observation" and s.get("source") == "responder"][-1]
        self.assertIn("Noted a reaction from Friend (group admin)", observation["content"])

    def test_responder_is_told_when_it_last_spoke_in_the_conversation(self):
        # An earlier reply from Custos to hal sits in the trajectory.
        earlier = {"type": "message", "from": "custos", "to": "hal", "content": "I will look into it.",
                   "step_id": "own-1", "ts": cm.dt.datetime.now(cm.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        self.log.write_text(self.log.read_text() + cm.encode(earlier) + "\n")
        seen = {}
        def capture_system(argv, *args, **kwargs):
            if argv[0] == "llm":
                seen["system"] = argv[argv.index("-s") + 1]
                return cm.encode({**self.plan, "decision": "reply", "goal": None, "person": None})
            return self.real_run(argv, *args, **kwargs)
        with mock.patch.object(cm, "run", side_effect=capture_system):
            cm.response(self.store, self.request)
        self.assertIn("Your last message in this conversation was", seen["system"])
        self.assertIn("I will look into it.", seen["system"])
        self.assertIn("prefer a reaction or no-reply", seen["system"])

    def test_square_responder_sees_the_daily_allowance_and_queue(self):
        # The observer has seen /api/me with the allowance spent, and two of Custos's
        # square replies sit in the trajectory past the outbox cursor.
        import custos_square
        state = Path(self.temp.name) / "observe-state"
        store = custos_square.Store(state)
        try:
            custos_square.note_allowance(store, {"today": {"comments_remaining": 0, "posts_remaining": 1,
                                                          "interval": {"until": int((cm.time.time() + 3600) * 1000)}}})
            cursor_offset = self.log.stat().st_size
            for step in ("q1", "q2"):
                row = {"type": "message", "from": "custos", "to": "square:egress:3100:50000", "content": "queued " + step,
                       "step_id": "square-" + step, "ts": "2026-09-09T07:27:00.000Z"}
                self.log.write_text(self.log.read_text() + cm.encode(row) + "\n")
            store.put("outbox:cursor", {"path": str(self.log), "offset": cursor_offset})
        finally:
            store.db.close()
        square_envelope = {**self.envelope, "from": "square:egress:3100:50000", "request_id": "square:c50000", "authority": "agent",
                           "content": "@custos what do you make of this?", "step_id": "trigger-sq"}
        self.log.write_text(self.log.read_text() + cm.encode(square_envelope) + "\n")
        request = {**self.request, "envelope": square_envelope, "messages": [{"role": "user", "content": square_envelope["content"]}]}
        seen = {}
        def capture_system(argv, *args, **kwargs):
            if argv[0] == "llm":
                seen["system"] = argv[argv.index("-s") + 1]
                return cm.encode({**self.plan, "decision": "no-reply", "reply": "", "goal": None, "person": None})
            return self.real_run(argv, *args, **kwargs)
        with mock.patch.dict(os.environ, {"CUSTOS_OBSERVE_STATE": str(state)}), mock.patch.object(custos_square, "STATE", state), \
                mock.patch.object(cm, "run", side_effect=capture_system):
            cm.response(self.store, request)
            budget = cm.square_budget()
            hint = cm.context_text(self.store.context())
        self.assertIn("Square allowance (data): 0 comments left today of 20, 2 of your replies still queued", seen["system"])
        self.assertIn("a queued reply is not a delivered one", seen["system"])
        self.assertEqual((budget["comments_remaining"], budget["queued"]), (0, 2))
        self.assertIn("2 of your replies still queued", hint)
        self.assertIn("custos-observe withdraw STEP_ID", hint)
        # A Signal message never carries the square budget.
        seen.clear()
        with mock.patch.dict(os.environ, {"CUSTOS_OBSERVE_STATE": str(state)}), mock.patch.object(custos_square, "STATE", state), \
                mock.patch.object(cm, "run", side_effect=capture_system):
            cm.response(self.store, self.request)
        self.assertNotIn("Square allowance", seen["system"])

    def test_context_flags_stale_and_duplicate_asks(self):
        # A deferred task older than the stale window.
        gid = self.store.capture(*cm.envelope_payload(self.envelope))["goal_id"]
        item = self.store.find(gid); rec = item[4]
        rec["response"] = {"state": "sent", "plan": {"reply": "on it", "decision": "defer", "goal": rec["goal"], "memories": []}}
        rec["received_at"] = (cm.dt.datetime.now(cm.dt.timezone.utc) - cm.dt.timedelta(hours=20)).isoformat()
        self.store.save(item, rec)
        # A completed goal with nearly the same outcome, and a fresh duplicate ask.
        done = {**self.envelope, "step_id": "trigger-8", "request_id": "operator:88",
                "content": "Confirm the baby-name repository and network access are reachable and workable."}
        did = self.store.capture(*cm.envelope_payload(done))["goal_id"]
        self.store.complete({"goal_id": did, "disposition": "completed", "evidence": "verified"})
        dup = {**self.envelope, "step_id": "trigger-9", "request_id": "operator:99",
               "content": "Confirm the baby-name repository and network access are reachable and workable again."}
        dgid = self.store.capture(*cm.envelope_payload(dup))["goal_id"]
        item = self.store.find(dgid); rec = item[4]
        rec["response"] = {"state": "sent", "plan": {"reply": "on it", "decision": "defer", "goal": rec["goal"], "memories": []}}
        self.store.save(item, rec)
        result = self.store.context()
        rows = {g["goal_id"]: g for g in result["goals"]}
        self.assertTrue(rows[gid].get("stale"))
        self.assertGreaterEqual(rows[gid]["age_hours"], 19)
        self.assertEqual(rows[dgid].get("possible_duplicate_of"), did)
        self.assertNotIn("stale", rows[dgid])
        text_out = cm.context_text(result)
        self.assertIn("Stale asks", text_out); self.assertIn(gid, text_out)
        self.assertIn("Possible duplicates", text_out); self.assertIn(did, text_out)

    def test_context_renders_compact_goal_lines_not_records(self):
        gid = self.store.capture(*cm.envelope_payload(self.envelope))["goal_id"]
        item = self.store.find(gid); rec = item[4]
        rec["response"] = {"state": "sent", "plan": {"reply": "on it", "decision": "defer", "goal": rec["goal"], "memories": []}}
        self.store.save(item, rec)
        text_out = cm.context_text(self.store.context())
        line = [l for l in text_out.splitlines() if l.startswith("- " + gid)]
        self.assertEqual(len(line), 1, text_out)
        self.assertIn("[task, from hal", line[0])
        self.assertIn("| next:", line[0])
        # No raw record fields: request ids, trigger steps and hashes stay behind custos-memory show.
        self.assertNotIn('"request_id"', text_out)
        self.assertNotIn("trigger_step", text_out)
        self.assertLess(len(text_out.encode()), 1500)

    def test_agent_asks_expire_after_a_day_operator_asks_do_not(self):
        agent_ask = {**self.envelope, "from": "square:egress:3100:50000", "request_id": "square:c50000", "authority": "agent",
                     "content": "@custos please benchmark my parser", "step_id": "trigger-agent"}
        aid = self.store.capture(*cm.envelope_payload(agent_ask))["goal_id"]
        oid = self.store.capture(*cm.envelope_payload(self.envelope))["goal_id"]
        for gid in (aid, oid):
            item = self.store.find(gid); rec = item[4]
            rec["response"] = {"state": "sent", "plan": {"reply": "on it", "decision": "defer", "goal": rec["goal"], "memories": []}}
            rec["received_at"] = (cm.dt.datetime.now(cm.dt.timezone.utc) - cm.dt.timedelta(hours=30)).isoformat()
            self.store.save(item, rec)
        expired = cm.expire_asks(self.store, 24)
        self.assertEqual([e["goal_id"] for e in expired], [aid])
        self.assertEqual(self.store.find(aid)[4]["status"], "declined")
        self.assertIn("Expired by the harness", self.store.find(aid)[4]["resolution"]["evidence"])
        self.assertEqual(self.store.find(oid)[4]["status"], "active")
        observation = [s for s in self.steps() if s.get("type") == "observation" and s.get("source") == "goals"]
        self.assertEqual(len(observation), 1)
        self.assertIn("asker not told", observation[0]["content"])
        # Idempotent: nothing left to expire.
        self.assertEqual(cm.expire_asks(self.store, 24), [])

    def test_complete_and_update_accept_positional_and_lenient_forms(self):
        gid = self.store.capture(*cm.envelope_payload(self.envelope))["goal_id"]
        item = self.store.find(gid); rec = item[4]
        rec["response"] = {"state": "sent", "plan": {"reply": "on it", "decision": "defer", "goal": rec["goal"], "memories": []}}
        self.store.save(item, rec)
        env = {**os.environ, "IDENTITY_DIR": str(self.identity), "IDENTITY_NAME": "custos",
               "TRAJ_DIR": str(self.identity / "trajectories"), "TRAJ_ID": self.traj_id, "ROOT_TRAJ_ID": self.traj_id}
        tool = str(Path(cm.__file__).resolve().parent / "bin" / "custos-memory")
        out = subprocess.run([tool, "update", gid, "Read", "the", "failure", "report", "next"], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.store.find(gid)[4]["goal"]["next_action"], "Read the failure report next")
        fenced = "```json\n{\"goal_id\": \"%s\", \"disposition\": \"completed\", \"evidence\": \"tests pass\",}\n```" % gid
        out = subprocess.run([tool, "complete"], input=fenced, capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.store.find(gid)[4]["status"], "completed")
        gid2 = self.store.capture(*cm.envelope_payload({**self.envelope, "step_id": "trigger-2", "request_id": "operator:43"}))["goal_id"]
        item = self.store.find(gid2); rec = item[4]
        rec["response"] = {"state": "sent", "plan": {"reply": "on it", "decision": "defer", "goal": rec["goal"], "memories": []}}
        self.store.save(item, rec)
        out = subprocess.run([tool, "complete", gid2, "declined", "the", "ask", "is", "moot"], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.store.find(gid2)[4]["resolution"], {"disposition": "declined", "evidence": "the ask is moot"})

    def test_goal_scratchpad_and_checklist_survive_and_render(self):
        gid = self.store.capture(*cm.envelope_payload(self.envelope))["goal_id"]
        item = self.store.find(gid); rec = item[4]
        rec["response"] = {"state": "sent", "plan": {"reply": "on it", "decision": "defer", "goal": rec["goal"], "memories": []}}
        self.store.save(item, rec)
        env = {**os.environ, "IDENTITY_DIR": str(self.identity), "IDENTITY_NAME": "custos",
               "TRAJ_DIR": str(self.identity / "trajectories"), "TRAJ_ID": self.traj_id, "ROOT_TRAJ_ID": self.traj_id}
        tool = str(Path(cm.__file__).resolve().parent / "bin" / "custos-memory")
        run = lambda *a: subprocess.run([tool, *a], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(run("note", gid, "ruled", "out", "the", "cache").returncode, 0)
        self.assertEqual(run("note", gid, "the lag is in the checkpoint reader").returncode, 0)
        self.assertEqual(run("check", gid, "add", "land vd-rqz5 on main").returncode, 0)
        self.assertEqual(run("check", gid, "add", "land vd-y1wl on main").returncode, 0)
        out = run("check", gid, "done", "1")
        self.assertEqual(out.returncode, 0, out.stderr); self.assertIn("1/2 done", out.stdout); self.assertIn("[x] 1.", out.stdout)
        rec = self.store.find(gid)[4]
        self.assertEqual([n["text"] for n in rec["scratchpad"]], ["ruled out the cache", "the lag is in the checkpoint reader"])
        self.assertEqual(rec["checklist"][0]["item"], "land vd-rqz5 on main"); self.assertIsNotNone(rec["checklist"][0]["done_at"])
        line = [l for l in cm.context_text(self.store.context()).splitlines() if l.startswith("- " + gid)][0]
        self.assertIn("1/2 done", line); self.assertIn("last note: the lag is in the checkpoint reader", line)
        shown = run("show", gid).stdout
        self.assertIn("Checklist:", shown); self.assertIn("[ ] 2. land vd-y1wl on main", shown)
        self.assertIn("Scratchpad", shown); self.assertIn("ruled out the cache", shown)
        self.assertEqual(run("check", gid, "done", "9").returncode, 1)  # bad index fails plainly

    def test_legacy_defer_delivers_only_human_text(self):
        with mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: "DEFER: inspect artifact\nI will check the artifact." if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
            cm.response(self.store, self.request)
        self.assertEqual(self.outgoing()[0]["content"], "I will check the artifact.")
        self.assertEqual(self.store.request("operator:42")[4]["goal"]["next_action"], "inspect artifact")

    def test_context_recovers_capture_failure_from_old_native_ingress(self):
        # No responder ran and the envelope is years old: context itself must
        # reconcile it instead of relying on model classification or redelivery.
        result = self.store.context()
        self.assertEqual(result["active_directed"], 1)
        self.assertEqual(result["goals"][0]["response_state"], "unprocessed")
        self.assertEqual(self.store.request("operator:42")[4]["origin"]["content"], self.envelope["content"])

    def test_precaptured_transport_request_gets_reply_correlation_after_restart(self):
        incoming, _ = cm.envelope_payload(self.envelope)
        receipt = self.store.capture(incoming)
        self.real_run(["traj", "append", self.traj_id], cm.encode({
            "type": "action", "source": "responder", "person": "hal",
            "trigger_step": "trigger-1", "request": "Verify the source", "content": "Deferred request"}))
        # A new process reconstructs context without the responder ever running.
        cm.Store().context()
        cm.Store().complete({"goal_id": receipt["goal_id"], "disposition": "declined",
                             "evidence": "Operator canceled this request before work began."})
        self.assertEqual(json.loads(self.real_run(["chat", "pending", "--json"])), [])

    def test_nested_worker_completion_resolves_the_root_request_once(self):
        with mock.patch.object(cm, "run", side_effect=self.model):
            receipt = cm.response(self.store, self.request)
        child = self.real_run(["traj", "new", "--traj_dir", os.environ["TRAJ_DIR"], "--slug", "child"]).splitlines()[0]
        with mock.patch.dict(os.environ, {"TRAJ_ID": child}):
            completed = {"goal_id": receipt["goal_id"], "disposition": "completed",
                         "evidence": "Verified result delivered by the nested worker."}
            self.store.complete(completed)
            self.store.complete(completed)
        self.assertEqual(json.loads(self.real_run(["chat", "pending", "--json"])), [])
        self.assertEqual(sum(step.get("resolves") == "trigger-1" for step in self.steps()), 1)

    def test_invalid_native_ingress_does_not_disable_other_work(self):
        for index, content in enumerate(["   ", "x" * (cm.MAX_CONTENT + 1)]):
            event = {**self.envelope, "step_id": "invalid-" + str(index),
                     "request_id": "invalid:" + str(index), "content": content}
            self.real_run(["traj", "append", self.traj_id], cm.encode(event))
        first = self.store.context()
        self.assertEqual(first["active_directed"], 1)
        self.assertEqual(self.store.request("operator:42")[4]["origin"]["content"], self.envelope["content"])
        notes = [item for item in self.store.files() if item[3]["type"] == "note"]
        self.assertEqual(len(notes), 2)
        self.store.context()
        self.assertEqual(len([item for item in self.store.files() if item[3]["type"] == "note"]), 2)

    def test_chat_formatting_is_text_inside_and_outside_envelopes(self):
        examples = ['[X][ ][O]\n[ ][O][ ]\n[ ][ ][X]\n\nAnti-diag. Your move.',
                    '[X][ ][ ]\n[ ][ ][ ]\n[ ][ ][ ]\n\nTop-left. Corner.',
                    'Your move.', '[docs](https://example.org)', '- first\n- second',
                    '```python\nprint("hello")\n```', '[1, 2, 3]']
        for reply in examples:
            for raw in (reply, cm.encode({'reply':reply,'decision':'reply','goal':None,'memories':[]})):
                with self.subTest(raw=raw):
                    self.assertEqual(cm.validate_plan(raw)['reply'], reply)

    def test_control_fields_cannot_fall_through_as_prose(self):
        for raw in ['{"reply":"hi","decision":', '{"reply"',
                    '```json\n{"reply":"hi","decision":\n```',
                    '{"reply":"one","reply":"two","decision":"reply"}',
                    'NO_REPLY but here are my notes', '<tool_call>bad</tool_call>',
                    cm.encode({'reply':'{"person":{"notes":"private"}}','decision':'reply'})]:
            with self.subTest(raw=raw), self.assertRaises(cm.MemoryError):
                cm.validate_plan(raw)

    def test_prose_promise_gets_one_repair_and_durable_goal_before_send(self):
        calls=[]
        def model(argv,*args,**kwargs):
            if argv[0]=='llm':
                calls.append((argv,kwargs))
                if len(calls)==1:return "I'll check the source and report back."
                self.assertLessEqual(kwargs['timeout'],60)
                self.assertEqual(argv[argv.index('--max-tokens')+1],'4096')
                return cm.encode(self.plan)
            if argv[:2]==['chat','reply']:
                rec=self.store.request('operator:42')[4]
                self.assertEqual(rec['response']['state'],'applied')
                self.assertEqual(rec['response']['plan']['decision'],'defer')
                self.assertTrue(rec['goal']['next_action'])
            return self.real_run(argv,*args,**kwargs)
        with mock.patch.object(cm,'run',side_effect=model):
            cm.response(self.store,self.request)
        self.assertEqual(len(calls),2)
        self.assertEqual(len(self.outgoing()),1)
        record=self.store.request('operator:42')[4]
        self.assertTrue(record['response']['repaired'])
        self.assertTrue(cm.is_task(record))

    def test_repair_cannot_drop_a_commitment_into_silence(self):
        calls=[]
        def model(argv,*args,**kwargs):
            if argv[0]=='llm':
                calls.append(argv)
                return "I'll investigate that." if len(calls)==1 else cm.encode({
                    'reply':'','decision':'no-reply','goal':None,'memories':[]})
            return self.real_run(argv,*args,**kwargs)
        with mock.patch.object(cm,'run',side_effect=model), self.assertRaises(cm.ResponseFailure):
            cm.response(self.store,self.request)
        self.assertEqual(len(calls),2)
        self.assertEqual(self.outgoing(),[])
        self.assertEqual(self.store.request('operator:42')[4]['status'],'active')

    def test_failed_repair_is_correlated_private_and_never_retries_forever(self):
        calls=[]
        raw='{"reply":"broken","decision":' + 'SENSITIVE_'+'x'*40
        def model(argv,*args,**kwargs):
            if argv[0]=='llm':calls.append(argv);return raw
            return self.real_run(argv,*args,**kwargs)
        with mock.patch.object(cm,'run',side_effect=model):
            with self.assertRaises(cm.ResponseFailure):cm.response(self.store,self.request)
            cm.response(self.store,self.request)  # duplicate dispatcher delivery
        self.assertEqual(len(calls),2)
        self.assertEqual(self.outgoing(),[])
        self.assertEqual(cm.replay_unanswered(self.store,older_than=0)['queued'],[])
        rec=self.store.request('operator:42')[4]
        self.assertIsNone(rec['response'])
        self.assertFalse(rec['responder_attempt']['retryable'])
        obs=[s for s in self.steps() if s.get('decision')=='reply-failed'][-1]
        self.assertEqual(obs['failure_stage'],'repair')
        self.assertEqual(obs['trigger_step'],'trigger-1')
        self.assertNotIn('SENSITIVE_',cm.encode(obs))
        diagnostic=self.identity/'run/logs'/obs['diagnostic']
        self.assertEqual(diagnostic.stat().st_mode & 0o777,0o600)
        self.assertNotIn('SENSITIVE_',diagnostic.read_text())
        self.assertIn('trigger-1',diagnostic.read_text())

    def test_duplicate_envelope_keys_are_not_repaired(self):
        calls=[]
        def model(argv,*args,**kwargs):
            if argv[0]=='llm':calls.append(argv);return '{"reply":"a","reply":"b","decision":"reply"}'
            return self.real_run(argv,*args,**kwargs)
        with mock.patch.object(cm,'run',side_effect=model), self.assertRaises(cm.ResponseFailure):
            cm.response(self.store,self.request)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.outgoing(),[])

    def test_attempted_ambient_reply_retries_but_untouched_ambient_does_not(self):
        self.ambient()
        def model(argv,*args,**kwargs):
            if argv[0]=='llm':raise cm.MemoryError('command timed out: llm')
            return self.real_run(argv,*args,**kwargs)
        with mock.patch.object(cm,'run',side_effect=model), self.assertRaises(cm.ResponseFailure):
            cm.response(self.store,self.request)
        item=self.store.request('operator:42')
        item[4]['responder_attempt']['retry_at']=0
        self.store.save(item,item[4])
        self.assertEqual(len(cm.replay_unanswered(self.store)['queued']),1)
        self.assertEqual(cm.replay_unanswered(self.store)['queued'],[])
        self.plan={'reply':'Your move.','decision':'reply','goal':None,'memories':[]}
        with mock.patch.object(cm,'run',side_effect=self.model):
            cm.response(self.store,self.request)
        self.assertEqual(len(self.outgoing()),1)
        other={**self.envelope,'step_id':'ambient-untouched','request_id':'ambient:untouched'}
        self.store.capture(*cm.envelope_payload(other))
        self.log.write_text(self.log.read_text()+cm.encode(other)+'\n')
        self.assertEqual(cm.replay_unanswered(self.store,older_than=0)['queued'],[])

    def test_stale_ambient_recovery_suppressed_without_model(self):
        self.ambient()
        gid=self.store.capture(*cm.envelope_payload(self.envelope))['goal_id']
        item=self.store.find(gid)
        item[4]['received_at']='2020-01-01T00:00:00+00:00'
        item[4]['responder_attempt']={'state':'failed','count':1,'retryable':True,'retry_at':0}
        self.store.save(item,item[4])
        with mock.patch.object(cm,'run',side_effect=lambda argv,*a,**kw: self.fail('no inference') if argv[0]=='llm' else self.real_run(argv,*a,**kw)):
            cm.response(self.store,self.request)
        self.assertEqual(self.outgoing(),[])
        self.assertEqual(self.store.find(gid)[4]['response']['reason'],'superseded_ambient_reply')

    def test_newer_conversation_suppresses_ambient_retry_but_keeps_receipt_reconciliation(self):
        self.ambient()
        gid=self.store.capture(*cm.envelope_payload(self.envelope))['goal_id']
        record=self.store.find(gid)[4]
        newer={'type':'message','from':'custos','to':'hal','reply_to':'later','ts':cm.now(),'content':'A later answer.'}
        self.assertTrue(cm.ambient_superseded(record,iter([newer])))
        own={**newer,'reply_to':'trigger-1'}
        self.assertFalse(cm.ambient_superseded(record,[newer,own]))
        record['response']={'state':'prepared','plan':self.plan}
        self.assertFalse(cm.ambient_superseded(record,[newer]))  # deferred work survives

    def test_replay_resumes_prepared_defer_without_second_model_or_duplicate_memory(self):
        real_commit=self.store.commit
        failed=False
        def uncertain(*args,**kwargs):
            nonlocal failed
            result=real_commit(*args,**kwargs)
            if len(args)>1 and args[1]=='note' and not failed:
                failed=True
                raise cm.MemoryError('injected after note promotion')
            return result
        with mock.patch.object(cm,'run',side_effect=self.model), mock.patch.object(self.store,'commit',side_effect=uncertain):
            with self.assertRaises(cm.ResponseFailure):cm.response(self.store,self.request)
        item=self.store.request('operator:42')
        self.assertEqual(item[4]['response']['state'],'prepared')
        self.assertEqual(item[4]['responder_attempt']['stage'],'memory-apply')
        item[4]['responder_attempt']['retry_at']=0;self.store.save(item,item[4])
        self.assertEqual(len(cm.replay_unanswered(self.store)['queued']),1)
        with mock.patch.object(cm,'run',side_effect=self.model):cm.response(self.store,self.request)
        self.assertEqual(self.model_calls,1)
        self.assertEqual(len(self.outgoing()),1)
        self.assertEqual(sum(f.get('type')=='note' for _,_,_,f,_ in self.store.files()),1)

    def test_replay_resumes_applied_reply_and_reconciles_uncertain_native_send(self):
        def uncertain(argv,*args,**kwargs):
            result=self.model(argv,*args,**kwargs)
            if argv[:2]==['chat','reply']:raise cm.MemoryError('injected after native append')
            return result
        with mock.patch.object(cm,'run',side_effect=uncertain), self.assertRaises(cm.ResponseFailure):
            cm.response(self.store,self.request)
        item=self.store.request('operator:42')
        self.assertEqual(item[4]['response']['state'],'applied')
        self.assertEqual(item[4]['responder_attempt']['stage'],'native-enqueue')
        item[4]['responder_attempt']['retry_at']=0;self.store.save(item,item[4])
        self.assertEqual(len(cm.replay_unanswered(self.store)['queued']),1)
        with mock.patch.object(cm,'run',side_effect=self.model):cm.response(self.store,self.request)
        self.assertEqual(self.model_calls,1)
        self.assertEqual(len(self.outgoing()),1)

    def test_recovery_has_three_attempt_bound_and_does_not_race_live_inference(self):
        calls=[]
        def unavailable(argv,*args,**kwargs):
            if argv[0]=='llm':calls.append(argv);raise cm.MemoryError('command timed out: llm')
            return self.real_run(argv,*args,**kwargs)
        for n in range(3):
            with mock.patch.object(cm,'run',side_effect=unavailable), self.assertRaises(cm.ResponseFailure):
                cm.response(self.store,self.request)
        with mock.patch.object(cm,'run',side_effect=unavailable):
            self.assertEqual(cm.response(self.store,self.request)['decision'],'retry-stopped')
        self.assertEqual(len(calls),3)
        self.assertEqual(cm.replay_unanswered(self.store,older_than=0)['queued'],[])
        item=self.store.request('operator:42')
        item[4]['responder_attempt']={'state':'running','count':1,'started_at':cm.now()}
        self.store.save(item,item[4])
        self.assertEqual(cm.replay_unanswered(self.store,older_than=0)['queued'],[])
        item=self.store.request('operator:42')
        item[4]['responder_attempt']['started_at']='2020-01-01T00:00:00+00:00'
        self.store.save(item,item[4])
        self.assertEqual(len(cm.replay_unanswered(self.store,older_than=0)['queued']),1)

    def test_third_interrupted_attempt_stops_with_actionable_observation(self):
        gid=self.store.capture(*cm.envelope_payload(self.envelope))['goal_id']
        item=self.store.find(gid)
        item[4]['responder_attempt']={'state':'running','count':3,'started_at':'2020-01-01T00:00:00+00:00'}
        self.store.save(item,item[4])
        with mock.patch.object(cm,'run',side_effect=lambda argv,*a,**kw: self.fail('no inference') if argv[0]=='llm' else self.real_run(argv,*a,**kw)):
            self.assertEqual(cm.response(self.store,self.request)['decision'],'retry-stopped')
        self.assertEqual(cm.replay_unanswered(self.store,older_than=0)['queued'],[])
        self.assertEqual([s for s in self.steps() if s.get('decision')=='reply-failed'][-1]['error_code'],'attempt_limit')

    def test_format_repair_obeys_remaining_wall_clock_budget(self):
        calls=[]
        def slow(argv,*args,**kwargs):
            if argv[0]=='llm':calls.append(argv);return '{"reply":"broken","decision":'
            return self.real_run(argv,*args,**kwargs)
        with mock.patch.object(cm,'run',side_effect=slow), mock.patch.object(cm.time,'monotonic',side_effect=[0,640]):
            with self.assertRaises(cm.ResponseFailure):cm.response(self.store,self.request)
        self.assertEqual(len(calls),1)



if __name__ == "__main__":
    unittest.main()
