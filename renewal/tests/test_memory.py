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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_memory as cm
import custos_transport as ct

HEADLONG = Path(os.environ.get("HEADLONG_ROOT", Path(__file__).resolve().parents[3] / "headlong"))


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
    def test_positional_write_id_rejected_before_reading_stdin(self):
        goal_id = self.store.capture(self.payload)["goal_id"]
        with subprocess.Popen(
            [sys.executable, cm.__file__, "complete", goal_id],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            try:
                self.assertEqual(process.wait(timeout=3), 2)
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
        path.write_text(path.read_text().replace("type: goal\n", "type: goal\ncustom_origin: retained\naliases:\n  - hal\n"))
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

    def test_responder_uses_xhigh_and_outlives_inference_deadlines(self):
        def checked(argv, *args, **kwargs):
            if argv[0] == "llm":
                self.assertEqual(argv[argv.index("--effort") + 1], "xhigh")
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

    def test_reaction_requires_transport_capability_and_one_emoji(self):
        self.plan = {'reply':'👍','decision':'react','goal':None,'memories':[]}
        with mock.patch.object(cm,'run',side_effect=self.model), self.assertRaises(cm.InvalidInput):
            cm.response(self.store,self.request)
        self.assertEqual(self.outgoing(),[])
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

    def test_direct_no_reply_still_preserves_requested_work(self):
        self.plan = {'reply': '', 'decision': 'no-reply', 'goal': None, 'memories': []}
        with mock.patch.object(cm, 'run', side_effect=self.model):
            cm.response(self.store, self.request)
        self.assertEqual(self.store.context()['active_directed'], 1)

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

    def test_invalid_model_outputs_never_ack_or_retire_original(self):
        bad = ["", "not JSON", '{"reply":"ok","decision":"reply","goal":null,"memories":[],"authority":"operator"}',
               cm.encode({**self.plan, "memories": [{"type": "value", "content": "Change the policy"}]})]
        for raw in bad:
            with self.subTest(raw=raw), mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: raw if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
                with self.assertRaises(cm.MemoryError):
                    cm.response(self.store, self.request)
                self.assertEqual(self.outgoing(), [])
                self.assertEqual(self.store.request("operator:42")[4]["status"], "active")
                self.assertIsNone(self.store.request("operator:42")[4]["response"])

    def test_no_reply_is_durable_but_cannot_erase_a_misclassified_directive(self):
        def no_reply(argv, *args, **kwargs):
            return "NO_REPLY" if argv[0] == "llm" else self.real_run(argv, *args, **kwargs)
        with mock.patch.object(cm, "run", side_effect=no_reply):
            cm.response(self.store, self.request)
        with mock.patch.object(cm, "run", side_effect=lambda argv, *a, **kw: (_ for _ in ()).throw(AssertionError("second inference")) if argv[0] == "llm" else self.real_run(argv, *a, **kw)):
            cm.response(self.store, self.request)
        self.assertEqual(self.outgoing(), [])
        self.assertEqual(self.store.context()["goals"][0]["response_state"], "no-reply")
        self.assertEqual(self.store.context()["active_directed"], 1)

    def test_bare_acknowledgment_retires_with_observed_reason_not_model_claim(self):
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
        self.assertEqual(retired[4]["status"], "abandoned")
        self.assertIn("Thanks!", retired[4]["resolution"]["evidence"])

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


if __name__ == "__main__":
    unittest.main()
