"""Regressions from the 2026-09-11 audit: messages the guest booked as delivered
that never left it, a memory rewrite the reader could not parse, duplicate
person notes, and the summary/search defects behind a stale re-raise.

Real native chat/mem/traj in temporary identities; llm and custos-actions are
replaced by fakes on PATH. No model, network or service calls.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_memory as cm
import custos_transport as ct
from test_memory import HEADLONG, MemoryFixture

RENEWAL = Path(__file__).resolve().parents[1]
UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"
UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"


def now_iso(offset=0):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + offset))


def route_for(target):
    return "signal-" + hashlib.sha256(target.encode()).hexdigest()[:24]


def executable(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class ChatFixture(unittest.TestCase):
    def setUp(self):
        self.temp = unittest.mock.MagicMock()
        import tempfile
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.identity = self.root / "identity"
        self.traj_id = "cafe0000-0000-0000-0000-0000000000ee"
        self.traj_dir = self.identity / "trajectories" / self.traj_id
        self.traj_dir.mkdir(parents=True)
        (self.identity / "memories").mkdir()
        self.log = self.traj_dir / "trajectory.jsonl"
        self.log.write_text(cm.encode({"type": "trajectory", "step_id": "header-1", "ts": now_iso(-7200)}) + "\n")
        self.fake_bin = self.root / "fakebin"
        self.fake_bin.mkdir()
        self.env = mock.patch.dict(os.environ, {
            "IDENTITY_DIR": str(self.identity), "IDENTITY_NAME": "custos", "MEM_DIR": str(self.identity / "memories"),
            "TRAJ_DIR": str(self.identity / "trajectories"), "TRAJ_ID": self.traj_id, "ROOT_TRAJ_ID": self.traj_id,
            "PATH": str(self.fake_bin) + ":" + str(HEADLONG / "bin") + ":" + str(HEADLONG / "tools") + ":" + os.environ["PATH"],
            "HOME": str(self.root / "home"), "CHAT_DOUBLE_TEXT_GUARD_HOURS": "", "CHAT_SOCIAL_BLOCKED": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.contact = "dm:bob-aci"
        self.bob = route_for(self.contact)
        self.alice = "signal-aaaaaaaaaaaaaaaaaaaaaaaa"

    def append(self, step):
        step = dict(step)
        step.setdefault("ts", now_iso())
        with self.log.open("a") as handle:
            handle.write(cm.encode(step) + "\n")

    def inbound(self, route, step_id, content="hello", receipt=True, request_id=None):
        request_id = request_id or "signal:" + hashlib.sha256((route + step_id).encode()).hexdigest()
        self.append({"type": "message", "step_id": step_id, "from": route, "to": "custos", "source": "operator-transport",
                     "request_id": request_id, "authority": "external", "content": content})
        if receipt:
            state = self.identity / ".state" / "transport"
            state.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(request_id.encode()).hexdigest()
            (state / (key + ".json")).write_text(json.dumps({
                "original": {"request_id": request_id, "sender": route, "authority": "external", "source_url": "", "content": content},
                "step_id": step_id, "phase": "queued", "reply_offset": 0}))
        return request_id

    def steps(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def outgoing(self):
        return [s for s in self.steps() if s.get("type") == "message" and s.get("from") == "custos"]

    def chat(self, *args, stdin=None):
        return subprocess.run(["chat", *args], input=stdin, text=True, capture_output=True, timeout=120)

    def fake_actions(self, contacts):
        log = self.root / "actions.log"
        executable(self.fake_bin / "custos-actions", "#!/usr/bin/env python3\nimport json, sys\n"
                   "if sys.argv[1] == 'signal-contacts':\n    print(json.dumps({'ok': True, 'contacts': " + repr(contacts) + "})); sys.exit(0)\n"
                   "if sys.argv[1] == 'signal-send':\n    raw = sys.stdin.read(); open(" + repr(str(log)) + ", 'a').write(raw + '\\n')\n"
                   "    print(json.dumps({'ok': True, 'request_id': json.loads(raw)['request_id'], 'phase': 'queued'})); sys.exit(0)\n"
                   "sys.exit(2)\n")
        return log


class ChatOutboundTests(ChatFixture):
    def test_reply_to_prefix_resolves_to_the_delivered_message_and_is_queued_for_the_bridge(self):
        self.inbound(self.alice, UUID_A, "what is the plan?")
        result = self.chat("reply", "--follow-up", "--reply-to", UUID_A[:8], self.alice, "here is the plan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("queued for the Signal bridge as a reply to " + UUID_A[:8], result.stderr)
        sent = self.outgoing()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["reply_to"], UUID_A, "the prefix must be stamped as the full step id the bridge knows")
        self.assertEqual(sent[0]["to"], self.alice)

    def test_reply_to_ambiguous_prefix_is_refused(self):
        self.inbound(self.alice, "abcd0000-1111-4111-8111-111111111111", "one")
        self.inbound(self.alice, "abcd0000-2222-4222-8222-222222222222", "two")
        result = self.chat("reply", "--follow-up", "--reply-to", "abcd0000", self.alice, "which?")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("more than one message step", result.stderr)
        self.assertEqual(self.outgoing(), [])

    def test_display_name_is_not_a_route(self):
        self.inbound(self.alice, UUID_A, "hi from a route the log knows")
        result = self.chat("reply", "--follow-up", "--reply-to", UUID_A, "Hal", "Your guess was right")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not sent", result.stderr)
        self.assertIn("display name is not a route", result.stderr)
        self.assertEqual(self.outgoing(), [])

    def test_signal_route_without_a_delivered_message_goes_through_custos_actions(self):
        log = self.fake_actions([{"target": self.contact, "label": "Bob", "kind": "dm"}])
        # Bob has spoken (so the route is known) but the reply names nothing the bridge delivered.
        self.inbound(self.bob, UUID_B, "earlier chatter", receipt=False)
        result = self.chat("send", "--from", "custos", "--to", self.bob, "Napa is on the list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Sent through custos-actions", result.stderr)
        self.assertEqual(self.outgoing(), [], "custos-actions records the step; chat must not append a second one")
        payload = json.loads(log.read_text().strip().splitlines()[-1])
        self.assertEqual(payload["target"], self.contact)
        self.assertEqual(payload["message"], "Napa is on the list")
        self.assertTrue(payload["request_id"].startswith("chat-"))

    def test_signal_route_the_broker_cannot_reach_is_refused_not_booked(self):
        self.fake_actions([{"target": "dm:someone-else", "label": "Else", "kind": "dm"}])
        self.inbound(self.bob, UUID_B, "earlier chatter", receipt=False)
        result = self.chat("reply", "--follow-up", "--reply-to", UUID_B, self.bob, "That actually did ring a bell")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not sent", result.stderr)
        self.assertIn("bridge only carries replies", result.stderr)
        self.assertEqual(self.outgoing(), [])

    def test_follow_up_delivery_is_exempt_from_the_double_text_guard(self):
        self.inbound(self.alice, UUID_A, "can you build it?")
        self.append({"type": "message", "step_id": "custos-said-1", "from": "custos", "to": self.alice,
                     "reply_to": UUID_A, "content": "I will look into it", "source": "chat"})
        with mock.patch.dict(os.environ, {"CHAT_DOUBLE_TEXT_GUARD_HOURS": "2"}):
            plain = self.chat("reply", "--reply-to", UUID_A, self.alice, "just checking in again")
            self.assertNotEqual(plain.returncode, 0)
            self.assertIn("you spoke last", plain.stderr)
            delivery = self.chat("reply", "--follow-up", "--reply-to", UUID_A, self.alice, "Built. Here it is.")
            self.assertEqual(delivery.returncode, 0, delivery.stderr)
        self.assertEqual([s["content"] for s in self.outgoing()][-1], "Built. Here it is.")

    def test_inbound_direction_is_untouched(self):
        result = self.chat("send", "--from", "alice-phone", "--to", "custos", "a message to the identity")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.steps()[-1]["from"], "alice-phone")


class TransportPrefixTests(unittest.TestCase):
    def test_receipt_lookup_accepts_a_unique_prefix_and_refuses_an_ambiguous_one(self):
        receipts = {UUID_A: "signal:one", UUID_B: "signal:two", "aaaaaaaa-9999-4999-8999-999999999999": "signal:three"}
        self.assertEqual(ct.receipt_lookup(receipts, UUID_A), "signal:one")
        self.assertEqual(ct.receipt_lookup(receipts, "bbbbbbbb"), "signal:two")
        self.assertIsNone(ct.receipt_lookup(receipts, "aaaaaaaa"), "two receipts share the prefix")
        self.assertIsNone(ct.receipt_lookup(receipts, "aa"))
        self.assertIsNone(ct.receipt_lookup(receipts, ""))
        self.assertIsNone(ct.receipt_lookup(receipts, None))


class StoreValidationTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.identity = self.root / "identity"
        self.traj_id = "cafe0000-0000-0000-0000-0000000000dd"
        self.traj_dir = self.identity / "trajectories" / self.traj_id
        self.traj_dir.mkdir(parents=True)
        self.log = self.traj_dir / "trajectory.jsonl"
        self.log.write_text(cm.encode({"type": "trajectory", "step_id": "header", "ts": "2020-01-01T00:00:00.000Z"}) + "\n")
        self.native_env = mock.patch.dict(os.environ, {"IDENTITY_DIR": str(self.identity), "IDENTITY_NAME": "custos",
                                                      "TRAJ_DIR": str(self.identity / "trajectories"),
                                                      "TRAJ_ID": self.traj_id, "ROOT_TRAJ_ID": self.traj_id})
        self.native_env.start()
        self.addCleanup(self.native_env.stop)

    def envelope(self, step_id, content):
        return {"type": "message", "step_id": step_id, "from": "signal-route-1", "to": "custos", "request_id": "signal:" + step_id,
                "authority": "external", "source_url": "", "content": content, "ts": "2020-01-01T00:00:01.000Z"}

    def append(self, step):
        with self.log.open("a") as handle:
            handle.write(cm.encode(step) + "\n")

    def steps(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_reconcile_sets_conflicting_records_aside_in_one_observation_not_a_note_each(self):
        for n in range(3):
            step = self.envelope("step-%d" % n, "original text %d" % n)
            self.append(step)
            self.store.capture(*cm.envelope_payload(step))
        # A rewrite changes the captured provenance of every record (the density-floor pass, 2026-09-11).
        for path, header, body, fields, record in list(self.store.files()):
            record["origin"]["content"] = "[collapsed]"
            self.store.save((path, header, body, fields, record), record)
        self.store.reconcile()
        notes = [f for f in self.store.files() if "Unusable incoming" in f[2]]
        self.assertEqual(notes, [], "no junk note per rejected step")
        state = json.loads((self.identity / ".state" / "ingress-rejected.json").read_text())
        self.assertEqual(sorted(e["step_id"] for e in state), ["step-0", "step-1", "step-2"])
        observations = [s for s in self.steps() if s.get("type") == "observation" and s.get("source") == "custos-memory"]
        self.assertEqual(len(observations), 1)
        self.assertIn("3 incoming messages could not be matched", observations[0]["content"])
        self.assertIn("custos-memory validate", observations[0]["content"])
        # Idempotent: a second pass records nothing new.
        self.store.reconcile()
        self.assertEqual(len([s for s in self.steps() if s.get("source") == "custos-memory"]), 1)
        self.assertEqual(len(json.loads((self.identity / ".state" / "ingress-rejected.json").read_text())), 3)

    def test_validate_reports_conflicts_and_unparseable_files_without_writing(self):
        step = self.envelope("step-9", "keep me")
        self.append(step)
        goal_id = self.store.capture(*cm.envelope_payload(step))["goal_id"]
        clean = self.store.validate()
        self.assertEqual((clean["problems"], clean["conflicts"]), ([], []))
        item = self.store.find(goal_id)
        record = item[4]
        record["origin"]["content"] = "[collapsed]"
        self.store.save(item, record)
        broken = self.root / "memories" / "2020-01-01-00-00-00_deadbeef_broken.md"
        broken.write_text("---\nid: deadbeef\nsummary: x\ntype: memory\ncreated: 2020-01-01 00:00:00\n---\n\nx" + cm.MARKER + '{"version":1,"status":"bogus"}\n')
        before = {p.name: p.read_bytes() for p in (self.root / "memories").glob("*.md")}
        result = self.store.validate()
        self.assertEqual([c["step_id"] for c in result["conflicts"]], ["step-9"])
        self.assertEqual(result["conflicts"][0]["differs"], ["content"])
        self.assertEqual([p["file"] for p in result["problems"]], [broken.name])
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / "memories").glob("*.md")})
        single = self.store.validate([str(broken)])
        self.assertEqual(single["checked"], 1)
        self.assertEqual(len(single["problems"]), 1)

    def test_validate_command_exit_status_and_mem_edit_hook(self):
        env = dict(os.environ, PATH=str(RENEWAL / "bin") + ":" + os.environ["PATH"])
        run = lambda *args, **kw: subprocess.run(list(args), env=env, text=True, capture_output=True, timeout=120, **kw)
        good = run("mem", "add", "--type", "memory", "Observed conversation: hi" + cm.MARKER + cm.encode({
            "version": 1, "origin": {"request_id": "r1", "sender": "s", "source_url": "", "content": "c", "authority": "external"},
            "goal": {"outcome": "o", "next_action": "n", "completion": "c"}, "status": "active", "events": [], "response": None}))
        self.assertEqual(good.returncode, 0, good.stderr)
        bad = run("mem", "add", "--type", "memory", "Observed conversation: hi" + cm.MARKER + '{"version":1,"status":"bogus"}')
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("write refused", bad.stderr)
        self.assertEqual([p.name for p in (self.root / "memories").glob("*bogus*")], [])
        self.assertEqual(len(list((self.root / "memories").glob("*.md"))), 1)
        stem = good.stdout.strip()
        original = (self.root / "memories" / (stem + ".md")).read_text()
        edit = run("mem", "edit", stem.split("_")[1], "Observed conversation: rewritten" + cm.MARKER + '{"version":1,"status":"bogus"}')
        self.assertNotEqual(edit.returncode, 0)
        self.assertIn("record restored", edit.stderr)
        self.assertEqual((self.root / "memories" / (stem + ".md")).read_text(), original)
        verdict = run("custos-memory", "validate")
        self.assertEqual(verdict.returncode, 0, verdict.stdout + verdict.stderr)
        (self.root / "memories" / "2020-01-01-00-00-00_deadbeef_broken.md").write_text(
            "---\nid: deadbeef\nsummary: x\ntype: memory\ncreated: 2020-01-01 00:00:00\n---\n\nx" + cm.MARKER + '{"version":1,"status":"bogus"}\n')
        verdict = run("custos-memory", "validate")
        self.assertEqual(verdict.returncode, 1)
        self.assertIn("deadbeef", verdict.stdout)


class NativeMemTests(MemoryFixture):
    def mem(self, *args, stdin=None):
        return subprocess.run(["mem", *args], input=stdin, text=True, capture_output=True, timeout=120)

    def read(self, stem):
        files = list((self.root / "memories").glob("*" + stem.split("_")[1] + "*"))
        self.assertEqual(len(files), 1)
        return files[0].read_text()

    def test_summary_is_the_first_non_blank_line_and_leading_blanks_are_dropped(self):
        added = self.mem("add", "--type", "note", stdin="\n\nPerson: Ryan\n\nlikes riddles\n")
        self.assertEqual(added.returncode, 0, added.stderr)
        text = self.read(added.stdout.strip())
        self.assertIn("summary: Person: Ryan\n", text)
        self.assertTrue(text.split("---\n", 2)[2].startswith("\nPerson: Ryan"), text)
        edited = self.mem("edit", added.stdout.strip().split("_")[1], stdin="\n\n\nPerson: Ryan (edited)\nstill likes riddles\n")
        self.assertEqual(edited.returncode, 0, edited.stderr)
        text = self.read(added.stdout.strip())
        self.assertIn("summary: Person: Ryan (edited)\n", text)
        self.assertNotIn("summary: \n", text)

    def test_search_drops_result_lines_naming_memories_that_do_not_exist(self):
        added = self.mem("add", "--type", "fact", "Ryan called Custos a chopped idiot and apologized")
        real = added.stdout.strip()
        fake_bin = self.root / "fakebin"
        fake_bin.mkdir()
        executable(fake_bin / "llm", "#!/usr/bin/env bash\nsys=$(cat >/dev/null)\n"
                   "printf '%s\\n' '" + real + " — the apology' '2026-01-01-00-00-00_deadbeef_ghost-record — invented' 'No other matches.'\n")
        with mock.patch.dict(os.environ, {"PATH": str(fake_bin) + ":" + os.environ["PATH"], "MEM_SEARCH_HEARTBEAT_S": "0"}):
            result = self.mem("search", "chopped idiot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(real, result.stdout)
        self.assertNotIn("deadbeef", result.stdout)
        self.assertIn("No other matches.", result.stdout)
        self.assertIn("dropped 1 result line", result.stderr)


class PeopleTests(MemoryFixture):
    def note(self, display, notes, updated, aliases=()):
        meta = {"person_key": "signal:jack-aci", "display": display, "aliases": list(aliases), "routes": ["signal-g"], "updated": updated}
        body = "Person: " + display + "\n\n" + notes + cm.PERSON_MARKER + cm.encode(meta)
        return self.store.commit(body, memory_type="person")

    def test_find_prefers_the_most_recently_updated_duplicate_and_merge_folds_them(self):
        older = self.note("Jack", "Hal's friend; likes riddles.", "2026-09-10T20:22:00+00:00", aliases=["Friend (group admin)"])
        newer = self.note("Jack", "Playful, probing; the human behind Kim.", "2026-09-11T05:39:00+00:00")
        people = cm.People(self.store)
        self.assertEqual(len(people.find_all("signal:jack-aci")), 2)
        self.assertIn("behind Kim", people.find("signal:jack-aci")[2])
        result = people.merge(newer, older)
        self.assertEqual(result["kept"], older)
        self.assertEqual(result["merged"], newer)
        self.assertEqual(len(people.find_all("signal:jack-aci")), 1)
        kept = people.find("signal:jack-aci")
        self.assertIn("likes riddles", kept[2])
        self.assertIn("behind Kim", kept[2])
        self.assertEqual(kept[1]["aliases"], ["Friend (group admin)"])
        self.assertEqual(kept[1]["display"], "Jack")
        archived = self.root / "dream" / "operator-cleanup"
        self.assertEqual(len(list((archived / "changes").glob("*.before.md"))), 2)
        self.assertEqual(len(list((archived / "merged").glob("*.md"))), 1)

    def test_merge_refuses_two_different_people(self):
        a = self.note("Jack", "one", "2026-09-10T00:00:00+00:00")
        body = "Person: Kim\n\nbot" + cm.PERSON_MARKER + cm.encode({"person_key": "signal:kim-aci", "display": "Kim", "aliases": [], "routes": [], "updated": "x"})
        b = self.store.commit(body, memory_type="person")
        with self.assertRaises(cm.MemoryError):
            cm.People(self.store).merge(a, b)
        self.assertEqual(len(list((self.root / "memories").glob("*.md"))), 2)


if __name__ == "__main__":
    unittest.main()
