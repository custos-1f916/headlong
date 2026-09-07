import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custos_work import WorkChannel, WorkError, validate


class Tracker:
    """In-memory tracker semantics; never invokes bd, network or project files."""
    def __init__(self):
        self.issues = {}
        self.comments_by_issue = {}
        self.writes = []
        self.fail_before = False
        self.fail_after = False
        self.claim_race = False
        self.close_race = False

    def issue(self, identity="vd-one", owner="", status="open"):
        issue = {"id": identity, "title": "A scoped defect", "status": status, "assignee": owner,
                 "created_by": "hal", "metadata": {}}
        self.issues[identity] = issue
        return issue

    def run(self, args, body=None, file_flag=None):
        command = args[0]
        if command == "show":
            return [copy.deepcopy(self.issues[args[1]])] if args[1] in self.issues else []
        if command in ("ready", "list"):
            items = self.issues.values()
            if command == "ready":
                items = [item for item in items if item["status"] == "open" and not item["assignee"]]
            else:
                items = [item for item in items if item["assignee"] == "custos"]
            return copy.deepcopy(list(items))
        if command == "comments" and args[1] != "add":
            return copy.deepcopy(self.comments_by_issue.get(args[1], []))
        self.writes.append((args, body))
        if self.fail_before:
            raise WorkError("tracker_uncertain", "Lost before write", 503)
        if command == "create":
            identity = args[args.index("--id") + 1]
            if identity in self.issues:
                raise WorkError("tracker_rejected", "Duplicate ID")
            item = self.issue(identity)
            item.update(title=args[args.index("--title") + 1], description=body, created_by="custos",
                        metadata=json.loads(args[args.index("--metadata") + 1]),
                        issue_type=args[args.index("--type") + 1], priority=int(args[args.index("--priority") + 1]))
        elif command == "update":
            item = self.issues[args[1]]
            if "--claim" in args:
                if self.claim_race:
                    item["assignee"] = "hal"
                if item["assignee"] not in ("", "custos"):
                    raise WorkError("tracker_rejected", "Atomic claim conflict")
                item.update(assignee="custos", status="in_progress")
            else:
                item.update(assignee=args[args.index("--assignee") + 1], status=args[args.index("--status") + 1])
            if file_flag == "--metadata":
                item["metadata"] = json.loads(body)
            else:
                item["metadata"]["custos_work"] = args[args.index("--set-metadata") + 1].split("=", 1)[1]
        elif command == "comments":
            item = {"id": len(self.writes), "author": "custos", "text": body}
            self.comments_by_issue.setdefault(args[2], []).append(item)
        elif command == "close":
            item = self.issues[args[1]]
            item.update(status="closed", close_reason=body)
            if self.close_race:
                item["assignee"] = "hal"
        else:
            raise AssertionError("Unexpected tracker action")
        if self.fail_after:
            raise WorkError("tracker_uncertain", "Lost after write", 503)
        return copy.deepcopy(item)


class WorkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tracker = Tracker()
        self.channel = WorkChannel(self.temp.name, self.tracker)

    def claim(self, identity="vd-one", request_id="claim-one"):
        return {"action": "claim", "issue_id": identity, "request_id": request_id,
                "goal_id": "abc123ef", "branch": "custos/fix-scoped-defect"}

    def create(self):
        return {"action": "create", "request_id": "create-migration", "goal_id": "abc123ef",
                "title": "Retire obsolete worker", "description": "Move ownership without losing work",
                "type": "task", "priority": 2}

    def close(self):
        output = "Scoped scenario: request replay preserved one issue; exit 0\n"
        return {"action": "close", "issue_id": "vd-one", "request_id": "close-one", "goal_id": "abc123ef",
                "summary": "Delivered the scoped fix with recorded verification",
                "evidence": {"commit": "a" * 40, "source_refs": ["scripts/game.gd"],
                             "verification": {"command": "godot --headless --script scripts/check.gd",
                                              "output": output, "sha256": hashlib.sha256(output.encode()).hexdigest(), "exit_code": 0}}}

    def assert_error(self, code, payload):
        with self.assertRaises(WorkError) as caught:
            self.channel.handle(payload)
        self.assertEqual(caught.exception.code, code)

    def test_no_generic_actions_or_extra_flags_and_exact_namespace(self):
        for payload in ({"action": "sql", "query": "DROP TABLE issues"},
                        {"action": "show", "issue_id": "other-123"},
                        {"action": "show", "issue_id": "--help"},
                        {**self.create(), "assignee": "hal"},
                        {**self.create(), "type": "epic"}):
            with self.subTest(payload=payload), self.assertRaises(WorkError):
                self.channel.handle(payload)
        self.assertEqual(self.tracker.writes, [])
        self.tracker.issue("vd-one-long")
        self.assert_error("namespace", {"action": "show", "issue_id": "vd-one"})

    def test_ready_hides_foreign_ownership_and_reports_own_work(self):
        self.tracker.issue("vd-open")
        self.tracker.issue("vd-foreign", "hal")
        self.tracker.issue("vd-owned", "custos", "in_progress")
        result = self.channel.handle({"action": "ready"})
        self.assertEqual([item["id"] for item in result["issues"]], ["vd-open"])
        self.assertEqual([item["id"] for item in result["active"]], ["vd-owned"])

    def test_foreign_owner_never_claimed_released_commented_or_closed(self):
        self.tracker.issue(owner="hal", status="in_progress")
        for payload in (self.claim(), self.close(),
                        {"action": "release", "issue_id": "vd-one", "request_id": "release-one", "goal_id": "abc123ef", "reason": "Done investigating"},
                        {"action": "comment", "issue_id": "vd-one", "request_id": "comment-one", "goal_id": "abc123ef", "text": "A finding"}):
            self.assert_error("owner_conflict", payload)
        self.assertEqual(self.tracker.writes, [])

    def test_atomic_claim_loses_to_foreign_owner_without_stealing(self):
        self.tracker.issue()
        self.tracker.claim_race = True
        self.assert_error("pending", self.claim())
        self.assertEqual(self.tracker.issues["vd-one"]["assignee"], "hal")
        self.assert_error("pending", self.claim())
        self.assertEqual(len(self.tracker.writes), 1)

    def test_one_active_claim_and_goal_link_enforced(self):
        self.tracker.issue()
        self.tracker.issue("vd-two")
        self.channel.handle(self.claim())
        self.assert_error("active_limit", self.claim("vd-two", "claim-two"))
        self.assert_error("goal_conflict", {**self.close(), "goal_id": "ffffffff"})
        self.assertEqual(len(self.tracker.writes), 1)

    def test_create_replay_survives_process_restart_without_duplicate(self):
        first = self.channel.handle(self.create())
        self.channel = WorkChannel(self.temp.name, self.tracker)
        second = self.channel.handle(self.create())
        self.assertEqual(first["issue"]["id"], second["issue"]["id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(len(self.tracker.issues), 1)
        self.assertEqual(len(self.tracker.writes), 1)
        self.assert_error("request_conflict", {**self.create(), "title": "Different task"})

    def test_lost_write_response_reconciles_actual_create_marker(self):
        self.tracker.fail_after = True
        result = self.channel.handle(self.create())
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.tracker.issues), 1)
        self.channel.handle(self.create())
        self.assertEqual(len(self.tracker.writes), 1)

    def test_unobserved_write_stays_pending_blocks_new_writes_but_not_reads(self):
        self.tracker.fail_before = True
        self.assert_error("pending", self.create())
        self.tracker.fail_before = False
        self.channel = WorkChannel(self.temp.name, self.tracker)
        self.assert_error("pending", self.create())
        self.assert_error("pending", {**self.create(), "request_id": "different-request"})
        self.assertEqual(self.channel.handle({"action": "ready"})["issues"], [])
        self.assertEqual(len(self.tracker.writes), 1)

    def test_pending_create_can_reconcile_later_visible_commit(self):
        payload = self.create()
        self.tracker.fail_before = True
        self.assert_error("pending", payload)
        self.tracker.fail_before = False
        args, body = self.tracker.writes[0]
        # Emulate a server-side commit becoming visible after a lost connection.
        self.tracker.run(args, body)
        before = len(self.tracker.writes)
        result = self.channel.handle(payload)
        self.assertTrue(result["reconciled"])
        self.assertEqual(len(self.tracker.writes), before)

    def test_comment_timeout_never_duplicates_and_release_frees_slot(self):
        self.tracker.issue()
        self.tracker.issue("vd-two")
        self.channel.handle(self.claim())
        payload = {"action": "comment", "issue_id": "vd-one", "request_id": "comment-one", "goal_id": "abc123ef", "text": "Reproduced boundary error"}
        self.tracker.fail_after = True
        self.channel.handle(payload)
        self.channel.handle(payload)
        self.assertEqual(len(self.tracker.comments_by_issue["vd-one"]), 1)
        self.tracker.fail_after = False
        result = self.channel.handle({"action": "release", "issue_id": "vd-one", "request_id": "release-one", "goal_id": "abc123ef", "reason": "Waiting for hardware; retain findings"})
        self.assertEqual(result["issue"]["status"], "open")
        self.assertEqual(result["issue"]["assignee"], "")
        self.channel.handle(self.claim("vd-two", "claim-two"))
        self.assertEqual(self.tracker.issues["vd-two"]["assignee"], "custos")

    def test_close_requires_intact_successful_commit_addressed_evidence(self):
        self.tracker.issue()
        self.channel.handle(self.claim())
        payload = self.close()
        for change in ("output", "exit", "commit", "source", "missing"):
            invalid = copy.deepcopy(payload)
            if change == "output":
                invalid["evidence"]["verification"]["output"] += "Altered"
            elif change == "exit":
                invalid["evidence"]["verification"]["exit_code"] = 1
            elif change == "commit":
                invalid["evidence"]["commit"] = "main"
            elif change == "source":
                invalid["evidence"]["source_refs"] = ["../../etc/passwd"]
            else:
                del invalid["evidence"]
            with self.subTest(change=change), self.assertRaises(WorkError):
                self.channel.handle(invalid)
        self.assertEqual(self.tracker.issues["vd-one"]["status"], "in_progress")
        result = self.channel.handle(payload)
        reason = json.loads(result["issue"]["close_reason"])
        self.assertEqual(reason["evidence"], payload["evidence"])
        self.assertEqual(reason["goal_id"], "abc123ef")
        self.assertEqual(reason["branch"], self.claim()["branch"])
        self.assertEqual(result["issue"]["status"], "closed")

    def test_close_external_reassignment_never_reports_success(self):
        self.tracker.issue()
        self.channel.handle(self.claim())
        self.tracker.close_race = True
        self.assert_error("pending", self.close())
        self.assert_error("pending", self.close())
        self.assertEqual(sum(args[0] == "close" for args, _ in self.tracker.writes), 1)


if __name__ == "__main__":
    unittest.main()
