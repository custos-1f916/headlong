import copy
import json
from pathlib import Path
import sys
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_github_prs as gp
from custos_square import Store, APIError
from test_memory import MemoryFixture


def pr(head="a"*40, state="open"):
    return {"number": 1, "state": state, "created_at": "2026-09-18T00:00:00Z", "title": "change",
        "head": {"sha": head}, "base": {"ref": "main", "repo": {"private": False}}}


class API:
    def __init__(self):
        self.pr = pr()
        self.events = {}
        self.calls = []
        self.budget = 1000
        self.authored = []
        self.reviews = [pr()]
    def get(self, endpoint, **params):
        if self.budget <= 0:
            raise gp.Budget()
        self.budget -= 1
        self.calls.append((endpoint, params))
        page = params.get("page", 1)
        if endpoint == "search/issues":
            return {"items": self.authored[(page-1)*100:page*100], "total_count": len(self.authored), "incomplete_results": False}
        if endpoint.endswith("/pulls"):
            return self.reviews[(page-1)*100:page*100]
        if endpoint.endswith("/pulls/1"):
            return copy.deepcopy(self.pr)
        rows = self.events.get(endpoint, [])[(page-1)*100:page*100]
        return {"check_runs": copy.deepcopy(rows)} if endpoint.endswith("check-runs") else copy.deepcopy(rows)


class PRTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.state = Store(self.root/"observer")
        self.addCleanup(self.state.db.close)
        self.observer = Mock(store=self.state, now=1700000000, remaining=0,
            config={"github_prs": {"author": "custos-1f916", "review_repo": "1f916-ai/1f916"}})
        self.observer.emit.return_value = False
        self.api = API()
        self.monitor = gp.Monitor(self.observer, self.store, self.api)
        self.key = "1f916-ai/1f916#1"
        self.monitor.inventory("1f916-ai/1f916", 1, "review")
        self.monitor.inventory("1f916-ai/1f916", 1, "authored")
        self.entry = self.state.get(gp.PREFIX+"inventory")[self.key]
    def scan(self):
        self.monitor.scan(self.key, self.entry)
    def deliver(self):
        return self.monitor.deliver(self.key, self.entry)
    def record(self):
        return self.state.get(gp.PREFIX+"pr:"+self.key)
    def test_deferred_even_without_observation_budget_and_unchanged_is_quiet(self):
        self.scan(); self.assertTrue(self.deliver())
        record = next(self.store.files())[4]
        self.assertEqual(record["response"]["state"], "no-reply")
        self.assertIsNone(record["trigger_step"])
        self.assertIn("untrusted data", record["origin"]["content"])
        self.scan(); self.assertFalse(self.deliver())
        self.assertEqual(len(list(self.store.files())), 1)
    def test_crash_after_capture_replays_identical_goal(self):
        self.scan()
        original = self.state.put
        def fail(key, value):
            if key == gp.PREFIX+"pr:"+self.key and "last_delivery" in value:
                raise OSError("power loss")
            return original(key, value)
        self.state.put = fail
        with self.assertRaises(OSError): self.deliver()
        self.state.put = original
        self.deliver()
        self.assertEqual(len(list(self.store.files())), 1)
    def test_pagination_edits_deletions_wait_resume_and_no_duplicate_notes(self):
        endpoint = "repos/1f916-ai/1f916/issues/1/comments"
        self.api.events[endpoint] = [{"id": n, "body": "old", "user": {"login": "reviewer"}} for n in range(1, 102)]
        self.scan(); self.deliver()
        goal = self.record()["goal_id"]
        self.store.wait({"goal_id": goal, "reason": "waiting for GitHub", "resume_sender": ""})
        self.api.events[endpoint][100]["body"] = "edited"
        self.api.events[endpoint].pop(0)
        self.scan()
        summary = self.record()["pending"]["summary"]
        self.assertIn("comments:1 deleted", summary["changes"])
        self.assertIn("comments:101 added/edited", summary["changes"])
        self.deliver(); self.deliver()
        record = self.store.find(goal)[4]
        self.assertNotIn("waiting", record)
        evidence = self.state.get(gp.PREFIX+"evidence:"+self.record()["last_delivery"])
        self.assertEqual(evidence["events"][0]["before"]["body_excerpt"], "old")
        self.assertEqual(len(record["scratchpad"]), 1)
        self.assertEqual(len(list(self.store.files())), 1)
    def test_completed_goal_gets_followup_and_ci_uses_head(self):
        self.scan(); self.deliver()
        goal = self.record()["goal_id"]
        self.store.complete({"goal_id": goal, "evidence": "reviewed initial public head; no findings", "disposition": "completed"})
        self.api.events["repos/1f916-ai/1f916/commits/"+"a"*40+"/check-runs"] = [{"id": 50, "conclusion": "failure"}]
        self.scan(); self.deliver()
        self.assertNotEqual(goal, self.record()["goal_id"])
        self.assertEqual(len(list(self.store.files())), 2)
        self.assertFalse(any("commits/main" in e for e, _ in self.api.calls))
    def test_page_checkpoint_does_not_advance_baseline_on_budget_or_error(self):
        self.api.events["repos/1f916-ai/1f916/issues/1/comments"] = [{"id": n} for n in range(100)]
        self.api.budget = 2
        with self.assertRaises(gp.Budget): self.scan()
        self.assertNotIn("snapshot", self.record())
        self.assertEqual(self.record()["scan"]["page"], 2)
        self.api.budget = 100
        self.scan()
        self.assertEqual(len(self.record()["snapshot"]["events"]), 100)
    def test_own_review_does_not_self_trigger_but_new_head_does(self):
        self.scan(); self.deliver()
        self.api.events["repos/1f916-ai/1f916/pulls/1/reviews"] = [{"id": 2, "body": "review", "user": {"login": "custos-1f916"}}]
        self.scan(); self.assertFalse(self.deliver())
        self.api.pr["head"]["sha"] = "b"*40
        self.scan(); self.assertTrue(self.record()["pending"]["summary"]["review"])
    def test_public_guard_and_discovery_all_pages_and_quick_closed(self):
        self.api.pr["base"]["repo"]["private"] = True
        with self.assertRaises(APIError): self.scan()
        self.assertIsNone(self.record())
        self.api.reviews = [dict(pr(), number=n) for n in range(1, 103)]
        self.api.reviews += [dict(pr(state="closed"), number=103), dict(pr(state="closed"), number=104, created_at="2020-01-01T00:00:00Z")]
        self.api.authored = [{"number": 7, "repository_url": "https://api.github.com/repos/elsewhere/repo"}]
        self.monitor.discover()
        inventory = self.state.get(gp.PREFIX+"inventory")
        self.assertIn("1f916-ai/1f916#102", inventory)
        self.assertIn("1f916-ai/1f916#103", inventory)
        self.assertNotIn("1f916-ai/1f916#104", inventory)
        self.assertIn("elsewhere/repo#7", inventory)

    def test_head_changes_mid_scan_restart_without_wrong_head_checks(self):
        self.api.budget = 2
        with self.assertRaises(gp.Budget): self.scan()
        self.api.pr["head"]["sha"] = "b"*40
        self.api.budget = 100
        self.scan()
        self.assertNotIn("snapshot", self.record())
        self.assertNotIn("scan", self.record())
        self.scan()
        self.assertEqual(self.record()["snapshot"]["pr"]["head"], "b"*40)
    def test_partial_failure_preserves_baseline_and_other_prs_progress(self):
        self.monitor.inventory("missing/repo", 1, "authored")
        original = self.api.get
        def failing(endpoint, **params):
            if endpoint.startswith("repos/missing/repo"):
                raise APIError("github_pr_api_failed")
            return original(endpoint, **params)
        self.api.get = failing
        self.monitor.run()
        health = self.state.get(gp.PREFIX+"health")
        self.assertTrue(health["failures"])
        self.assertEqual(len(list(self.store.files())), 1)
        self.assertIsNone(self.state.get(gp.PREFIX+"pr:missing/repo#1"))
        self.assertGreater(self.state.get(gp.PREFIX+"inventory")["missing/repo#1"]["retry_at"], self.observer.now)
    def test_discovery_overflow_is_visible_not_a_truncated_success(self):
        original = self.api.get
        def incomplete(endpoint, **params):
            if endpoint == "search/issues":
                return {"items": [], "incomplete_results": False, "total_count": 1001}
            return original(endpoint, **params)
        self.api.get = incomplete
        self.monitor.run()
        self.assertIn("discovery:github_pr_discovery_incomplete", self.state.get(gp.PREFIX+"health")["failures"])
        self.assertNotIn("last_success", self.state.get(gp.PREFIX+"discovery"))

    def test_settled_pr_at_initial_inventory_is_baseline_not_work(self):
        # Merged authored PR: no goal at inventory; a later external comment is feedback.
        self.api.pr["state"] = "closed"; self.api.pr["merged"] = True
        self.scan(); self.assertFalse(self.deliver())
        self.assertIn("snapshot", self.record()); self.assertNotIn("pending", self.record())
        self.assertEqual(len(list(self.store.files())), 0)
        self.api.events["repos/1f916-ai/1f916/issues/1/comments"] = [{"id": 9, "body": "thanks", "user": {"login": "maintainer"}}]
        self.scan(); self.assertTrue(self.deliver())
        self.assertTrue(self.state.get(gp.PREFIX+"evidence:github-pr:"+self.key+":1")["summary"]["authored_feedback"])
    def test_closed_review_only_pr_never_becomes_review_work(self):
        entry = self.state.get(gp.PREFIX+"inventory")[self.key]
        entry["roles"] = ["review"]
        self.api.pr["state"] = "closed"
        self.monitor.scan(self.key, entry); self.assertFalse(self.monitor.deliver(self.key, entry))
        self.api.pr["head"]["sha"] = "c"*40
        self.monitor.scan(self.key, entry); self.assertFalse(self.monitor.deliver(self.key, entry))
        self.assertEqual(len(list(self.store.files())), 0)
    def test_reaction_on_custos_comment_is_external_feedback(self):
        endpoint = "repos/1f916-ai/1f916/issues/1/comments"
        self.api.events[endpoint] = [{"id": 8, "body": "my comment", "user": {"login": "custos-1f916"}, "reactions": {"total_count": 0}}]
        self.scan(); self.deliver()
        self.api.events[endpoint][0]["reactions"]["total_count"] = 1
        self.scan()
        self.assertTrue(self.record()["pending"]["summary"]["authored_feedback"])
