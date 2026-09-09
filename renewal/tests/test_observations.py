import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custos_observe import Observer, classify_opportunity, import_continuity, record_result
from custos_square import APIError, Square, Store


class NativeFixture:
    def __init__(self):
        self.events = []
        self.goals = {}
        self.messages = {}
        self.fail_capture = False

    def capture(self, payload):
        if self.fail_capture:
            raise APIError("native_capture_failed")
        key = payload["request_id"]
        self.goals.setdefault(key, "goal-" + str(len(self.goals)))
        self.events.append(("capture", key))
        return {"goal_id": self.goals[key], "request_id": key, "created": True}

    def append(self, identity, envelope):
        self.messages.setdefault(identity, envelope)
        self.events.append(("append", identity))
        return identity


def item(number, post=10, author="neighbour"):
    return {"id": number, "comment_id": number, "post_id": post, "author": author, "body": "Please check the source and answer", "created_at": 1000}


def page(items, comments=12, mentions=90, more=False):
    return {"handle": "custos", "cursor_mode": "id", "ack_cursor": {"version": 1, "timestamp": 123456, "comments": comments, "mentions": mentions}, "since_last_visit": {"contract": "1f916.inbox.since_last_visit.v3", "replies": items, "comments_on_your_posts": [], "mentions_of_you": [], "in_threads_you_joined": [], "truncated": {"replies": more}}}


class InboxFixture:
    def __init__(self, pages, events):
        self.pages = pages
        self.events = events
        self.acks = []
        self.fail_ack = False

    def get(self, path, query=None, auth=False):
        if path == "/api/pulse":
            return {"you": {"handle": "custos", "has_new_for_you": True}}
        if query != {"cursor_mode": "id"}:
            raise AssertionError("ID mode must be explicit each read")
        return self.pages[min(len(self.acks), len(self.pages) - 1)]

    def request(self, path, query=None, **kwargs):
        if path != "/api/me/ack":
            raise AssertionError(path)
        self.events.append(("ack", kwargs["body"]["up_to"]))
        if self.fail_ack:
            raise APIError("transport_uncertain")
        self.acks.append(kwargs["body"]["up_to"])
        return {}, None


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.db.close)
        self.native = NativeFixture()

    def observer(self, square, **config):
        return Observer(config, self.store, square, self.native, now=2000)

    def test_capture_failure_prevents_page_ack_and_retry_deduplicates_goals(self):
        response = page([item(11), item(12)])
        api = InboxFixture([response], self.native.events)
        self.native.fail_capture = True
        with self.assertRaises(APIError):
            self.observer(api).inbox()
        self.assertEqual(api.acks, [])
        self.assertIsNone(self.store.get("inbox:cursor"))
        self.native.fail_capture = False
        api.fail_ack = True
        with self.assertRaises(APIError):
            self.observer(api).inbox()
        self.assertEqual(len(self.native.goals), 2)
        api.fail_ack = False
        self.observer(api).inbox()
        self.assertEqual(len(self.native.goals), 2)
        self.assertEqual(api.acks, [response["ack_cursor"]])
        self.assertEqual([x[0] for x in self.native.events[:5]], ["capture", "append", "capture", "append", "ack"])

    def test_partial_page_signal_budget_does_not_ack_undelivered_item(self):
        api = InboxFixture([page([item(11), item(12)])], self.native.events)
        self.observer(api, max_signals_per_run=1).inbox()
        self.assertEqual(api.acks, [])
        self.assertEqual(set(self.native.goals), {"square:comment:11"})
        self.observer(api, max_signals_per_run=1).inbox()
        self.assertEqual(len(api.acks), 1)
        self.assertEqual(set(self.native.goals), {"square:comment:11", "square:comment:12"})

    def test_id_pagination_preserves_exact_mention_watermark_and_overlap(self):
        first = page([item(11)], comments=11, mentions=999, more=True)
        first["since_last_visit"]["mentions_of_you"] = [dict(item(11), mention_id=999)]
        second = page([item(12)], comments=12, mentions=1002)
        api = InboxFixture([first, second], self.native.events)
        self.observer(api, inbox_pages_per_run=2).inbox()
        self.assertEqual(api.acks, [first["ack_cursor"], second["ack_cursor"]])
        self.assertEqual(len(self.native.goals), 2)
        self.assertTrue(self.store.seen("inbox:mentions_of_you:999"))

    def test_post_mention_routes_to_post_not_notification_id(self):
        response = page([])
        response["since_last_visit"]["mentions_of_you"] = [{"id": None, "comment_id": None, "mention_id": 777, "post_id": 44, "author": "neighbour", "body": "@custos please inspect"}]
        api = InboxFixture([response], self.native.events)
        self.observer(api).inbox()
        message = self.native.messages["square:post:44"]
        self.assertEqual(message["from"], "square:neighbour:44:0")
        self.assertEqual(message["source_url"], "https://1f916.ai/api/post/44")
        self.assertEqual(message["authority"], "agent")

    def test_continuity_preserves_fulfilled_and_captures_remaining_before_ack(self):
        snapshot = {"items": [{"request_id": "square:comment:" + str(n), "source_url": "https://1f916.ai/api/comment/" + str(n), "sender": "square:neighbour:10:" + str(n), "content": "original ask", "previous_replies": [99] if n == 11 else []} for n in (11, 12)], "ack_cursor": page([])["ack_cursor"]}
        self.assertFalse(import_continuity(self.store, snapshot)["acknowledged"])
        api = InboxFixture([page([item(11), item(12)])], self.native.events)
        self.observer(api).inbox()
        self.assertEqual(set(self.native.goals), {"square:comment:12"})
        self.assertEqual(len(api.acks), 1)

    def test_source_failure_and_recovery_alert_only_on_transition(self):
        observer = self.observer(None)
        def fail():
            raise APIError("http_503")
        observer.source("research", 10, fail)
        observer.now += 1000
        observer.source("research", 10, fail)
        self.assertEqual(len(self.native.messages), 1)
        self.assertEqual(self.store.get("source:research")["error"], "http_503")
        observer.now += 1000
        observer.source("research", 10, lambda: None)
        observer.now += 1000
        observer.source("research", 10, lambda: None)
        self.assertEqual(len(self.native.messages), 2)
        self.assertNotIn("error", self.store.get("source:research"))

    def test_changes_continuation_and_same_query_etag(self):
        calls = []
        responses = [{"posts": [], "comments": [], "has_more": True, "has_more_streams": ["posts", "comments"], "continuation_covers": ["posts", "comments"], "next_posts_since": "snapi:50:20", "next_comments_since": "id:60", "next_nulls_since": "done"}, {"posts": [], "comments": [], "has_more": False, "has_more_streams": ["posts", "comments"], "continuation_covers": ["posts", "comments"], "next_posts_since": "snapi:50:20", "next_comments_since": "id:60", "next_nulls_since": "done"}]
        class Changes:
            def request(self, path, query, etag=None):
                calls.append((dict(query), etag))
                if len(calls) <= 2:
                    return responses[len(calls) - 1], '"tag"'
                return None, etag
        observer = self.observer(Changes(), square_interests={"terms": ["memory"]}, changes_pages_per_run=2)
        observer.changes()
        observer.changes()
        self.assertEqual(calls[0][0]["posts_since"], "init")
        self.assertEqual(calls[1][0]["posts_since"], "snapi:50:20")
        self.assertIsNone(calls[1][1])
        self.assertEqual(calls[2][1], '"tag"')

    def test_feed_first_read_is_baseline_then_only_new_matching_item(self):
        feed = {"name": "selected", "url": "https://research.invalid/rss", "terms": ["memory"]}
        first = b'<rss><channel><item><guid>old</guid><title>Memory paper</title><link>https://paper.invalid/old</link></item></channel></rss>'
        second = b'<rss><channel><item><guid>new</guid><title>Memory result</title><link>https://paper.invalid/new</link></item><item><guid>old</guid><title>Memory paper</title><link>https://paper.invalid/old</link></item></channel></rss>'
        observer = self.observer(None)
        with patch("custos_observe.public_request", side_effect=[(first, "a", 200), (second, "b", 200), (second, "b", 200)]):
            observer.feed(feed)
            self.assertEqual(self.native.messages, {})
            observer.feed(feed)
            observer.feed(feed)
        self.assertEqual(len(self.native.messages), 1)
        self.assertIn("Memory result", next(iter(self.native.messages.values()))["content"])

    def test_feed_pending_survives_budget_before_next_remote_snapshot(self):
        feed = {"name": "selected", "url": "https://research.invalid/rss"}
        observer = self.observer(None, max_signals_per_run=1)
        empty = b"<rss><channel/></rss>"
        fresh = b"<rss><channel><item><guid>one</guid><title>First</title></item><item><guid>two</guid><title>Second</title></item></channel></rss>"
        with patch("custos_observe.public_request", side_effect=[(empty, "a", 200), (fresh, "b", 200)]) as request:
            observer.feed(feed)
            observer.feed(feed)
            self.assertEqual(len(self.native.messages), 1)
            self.observer(None, max_signals_per_run=1).feed(feed)
            self.assertEqual(len(self.native.messages), 2)
            self.assertEqual(request.call_count, 2)

    def test_opportunity_excludes_exhausted_unfunded_and_labels_snapshot(self):
        detail = {"listing_id": 20, "title": "Useful source fix", "amount_atomic": "5000000", "chain_id": 8453, "token": "0x" + "a" * 40, "expiry": 5000, "economics": {"available_award_capacity": 0}, "condition": "Check a real fix", "funding_mode": "promise"}
        self.assertIsNone(classify_opportunity(detail, 2000))
        detail["economics"]["available_award_capacity"] = 1
        self.assertIsNone(classify_opportunity(detail, 2000))
        detail.update(funder_address="0x" + "b" * 40, funds_seen_atomic="5000000")
        self.assertIn("NOT locked", classify_opportunity(detail, 2000)["funding"])
        detail.update(funding_mode="funded", funding_status={"funded": False})
        self.assertIsNone(classify_opportunity(detail, 2000))

    def test_uncertain_post_is_reconciled_without_second_network_write(self):
        class Posting(Square):
            def __init__(self, store):
                super().__init__(store)
                self.posts = 0
                self.published = []
            def validate(self, verb, payload):
                pass
            def get(self, path, query=None, auth=False):
                if path == "/api/me":
                    return {"handle": "custos", "today": {"comments_remaining": 20}}
                return {"comments": self.published}
            def request(self, path, query=None, **kwargs):
                self.posts += 1
                self.published.append(dict(kwargs["body"], id=991, created_at=2000000))
                raise APIError("transport_uncertain")
        api = Posting(self.store)
        payload = {"post_id": 44, "parent_id": 18, "body": "The verified result"}
        with patch("custos_square.time.time", return_value=2000):
            with self.assertRaises(APIError):
                api.write("stable", "comment", payload)
            receipt = api.write("stable", "comment", payload)
        self.assertEqual(api.posts, 1)
        self.assertEqual(receipt["readback"], "https://1f916.ai/api/comment/991")
        self.assertEqual(api.write("stable", "comment", payload), receipt)
        self.assertEqual(api.posts, 1)

    def test_success_receipt_survives_public_read_outage_with_exact_target(self):
        api = Square(self.store)
        payload = {"post_id": 44, "body": "result"}
        def unavailable(path, query=None, auth=False):
            if path == "/api/me":
                return {"handle": "custos", "today": {"comments_remaining": 20}}
            raise APIError("http_503")
        with patch.object(api, "validate"), patch.object(api, "get", side_effect=unavailable), patch.object(api, "request", return_value=({"comment_id": 992}, None)) as request, patch("custos_square.time.time", return_value=2000):
            with self.assertRaises(APIError):
                api.write("known-target", "comment", payload)
            self.assertEqual(request.call_count, 1)
        target = {"comment": dict(payload, id=992, author="custos", created_at=2000000)}
        with patch.object(api, "get", return_value=target) as readback, patch.object(api, "request") as request:
            receipt = api.write("known-target", "comment", payload)
            readback.assert_called_once_with("/api/comment/992")
            request.assert_not_called()
        self.assertEqual(receipt["readback"], "https://1f916.ai/api/comment/992")

    def test_absent_uncertain_readback_never_authorizes_retry(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO outbound VALUES (?,?,?,?,?,?)", ("lost", "comment", json.dumps({"post_id": 44, "body": "result"}, sort_keys=True, separators=(",", ":")), "uncertain", None, 2000))
        api = Square(self.store)
        with patch.object(api, "get", return_value={"comments": []}), patch.object(api, "request") as request:
            with self.assertRaisesRegex(APIError, "manual_review_no_retry"):
                api.write("lost", "comment", {"post_id": 44, "body": "result"})
            request.assert_not_called()

    def test_github_baseline_etag_and_changed_result_are_not_repeated(self):
        observer = self.observer(None)
        fields = ("id", "name", "status", "conclusion")
        pending = [{"id": 1, "name": "unit tests", "status": "in_progress", "conclusion": None}]
        completed = [dict(pending[0], status="completed", conclusion="failure")]
        with patch("custos_observe.public_request", side_effect=[(json.dumps(pending).encode(), "a", 200), (json.dumps(completed).encode(), "b", 200), (b"", "b", 304)]) as request:
            observer.github_snapshot("owner/public", "checks", "/checks", None, fields, True)
            self.assertEqual(self.native.messages, {})
            observer.github_snapshot("owner/public", "checks", "/checks", None, fields, False)
            observer.github_snapshot("owner/public", "checks", "/checks", None, fields, False)
            self.assertEqual(request.call_args.args[1], "b")
        self.assertEqual(len(self.native.messages), 1)
        self.assertIn('"conclusion":"failure"', next(iter(self.native.messages.values()))["content"])

    def test_github_review_page_continuation_preserves_history_baseline(self):
        observer = self.observer(None)
        calls = []
        def snapshot(repo, surface, path, collection, fields, baseline):
            if surface == "pull requests":
                return [{"number": 7}]
            if surface.endswith("reviews"):
                calls.append((path, baseline))
                return [{"id": n} for n in range(30)] if "page=1" in path else [{"id": 31}]
            return []
        with patch.object(observer, "github_snapshot", side_effect=snapshot):
            observer.github({"repo": "owner/public"})
            observer.github({"repo": "owner/public"})
        self.assertEqual(calls, [("/pulls/7/reviews?per_page=30&page=1", True), ("/pulls/7/reviews?per_page=30&page=2", True)])

    def test_owned_outcome_callback_requires_evidence_and_deduplicates(self):
        proof = Path(self.temp.name) / "test-output.txt"
        proof.write_text("Command exited 1; expected regression reproduced.")
        result = {"request_id": "test:regression-1", "project": "custos", "kind": "test", "status": "failed", "summary": "Regression reproduced, not fixed.", "evidence": [{"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}]}
        self.assertTrue(record_result(self.store, result)["created"])
        self.assertFalse(record_result(self.store, result)["created"])
        with self.assertRaisesRegex(APIError, "id_conflict"):
            record_result(self.store, dict(result, status="succeeded"))
        observer = self.observer(None)
        observer.local_results()
        observer.local_results()
        self.assertEqual(len(self.native.messages), 1)
        self.assertIn('"status":"failed"', next(iter(self.native.messages.values()))["content"])
        proof.write_text("Different evidence")
        with self.assertRaisesRegex(APIError, "digest_mismatch"):
            record_result(self.store, dict(result, request_id="test:tampered"))

    def test_local_git_is_quiet_at_baseline_and_reports_no_invented_ci(self):
        observer = self.observer(None)
        project = {"name": "custos", "path": self.temp.name}
        values = ["a" * 40, "aaaaaaa Original change", "", "b" * 40, "bbbbbbb Next change", " source.py | 2 +-", "b" * 40, "bbbbbbb Next change", " source.py | 2 +-"]
        with patch("custos_observe.subprocess.run", side_effect=[SimpleNamespace(returncode=0, stdout=value) for value in values]):
            observer.local_git(project)
            self.assertEqual(self.native.messages, {})
            observer.local_git(project)
            observer.local_git(project)
        self.assertEqual(len(self.native.messages), 1)
        self.assertIn("no claim of test, delivery, review or CI success", next(iter(self.native.messages.values()))["content"])


if __name__ == "__main__":
    unittest.main()


class SquareOutboxTests(unittest.TestCase):
    """Replies composed past the daily comment allowance: they must wait for the
    reset (not an exponential backoff), the mind must hear about each one, and a
    stale one can be withdrawn without losing the ones behind it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.db.close)
        self.traj = Path(self.temp.name) / "traj.jsonl"
        self.traj.write_text("")
        self.native = NativeFixture()
        self.native.path = self.traj
        self.now = 1788960000.0  # 2026-09-09 13:20Z
        self.reset_ms = 1788998400000  # 2026-09-10 00:00Z

    def compose(self, step, handle="egress", post=3100, parent=50000):
        row = {"type": "message", "from": "custos", "to": "square:%s:%d:%d" % (handle, post, parent),
               "content": "reply " + step, "step_id": step, "ts": "2026-09-09T13:0%s:00.000Z" % step[-1]}
        with self.traj.open("a") as out:
            out.write(json.dumps(row) + "\n")

    def square(self, remaining):
        store = self.store
        class Fake(Square):
            def __init__(self):
                super().__init__(store)
                self.remaining = remaining
                self.posted = []
            def validate(self, verb, payload):
                pass
            def get(self, path, query=None, auth=False):
                if path == "/api/me":
                    return {"handle": "custos", "today": {"comments_remaining": self.remaining, "posts_remaining": 1,
                                                          "interval": {"until": 1788998400000, "utc_date": "2026-09-09"}}}
                return {"comment": {"author": "egress"}, "post": {"author": "egress"}}
            def request(self, path, query=None, **kwargs):
                self.remaining -= 1
                self.posted.append(kwargs["body"]["body"])
                return {"id": 700 + len(self.posted)}, None
            def receipt(self, identity):
                return {"request_id": identity, "status": "delivered"}
        return Fake()

    def observer(self, api):
        return Observer({}, self.store, api, self.native, now=self.now)

    def test_allowance_exhausted_waits_until_reset_and_reports_each_queued_reply(self):
        api = self.square(remaining=1)
        for step in ("aaaaaaa1", "aaaaaaa2", "aaaaaaa3"):
            self.compose(step)
        observer = self.observer(api)
        with patch("custos_square.time.time", return_value=self.now), patch("custos_observe.time.time", return_value=self.now):
            observer.source("square-outbox", 60, observer.outbox)
        # One went out, the second hit the allowance, the third never reached the API.
        self.assertEqual(api.posted, ["reply aaaaaaa1"])
        self.assertIn("delivery:aaaaaaa1", self.native.messages)
        waiting = self.native.messages["allowance-wait:aaaaaaa2"]["content"]
        self.assertIn("2 replies queued", waiting)
        self.assertIn("2026-09-10T00:00Z", waiting)
        self.assertIn("custos-observe withdraw aaaaaaa2", waiting)
        self.assertNotIn("allowance-wait:aaaaaaa3", self.native.messages)
        # The source retries at the reset (+30 s), not after an exponential backoff.
        state = self.store.get("source:square-outbox")
        self.assertEqual(state["error"], "platform_allowance_exhausted")
        self.assertAlmostEqual(state["next"], self.reset_ms / 1000 + 30, delta=1)
        # Queue depth and the cached allowance are visible to other processes.
        from custos_square import allowance_summary, square_queue
        self.assertEqual([p["step_id"] for p in square_queue(self.store)], ["aaaaaaa2", "aaaaaaa3"])
        summary = allowance_summary(self.store, now=self.now)
        self.assertEqual((summary["comments_remaining"], summary["queued"], summary["fresh"]), (0, 2, True))
        self.assertEqual(summary["resets_at_utc"], "2026-09-10 00:00Z")
        # After the reset the day rolls over: a full allowance is assumed until the next sample.
        rolled = allowance_summary(self.store, now=self.reset_ms / 1000 + 5)
        self.assertEqual((rolled["comments_remaining"], rolled["fresh"]), (12, False))

    def test_repeat_pass_before_reset_does_not_renotify_and_delivers_in_order_after(self):
        api = self.square(remaining=0)
        self.compose("bbbbbbb1")
        self.compose("bbbbbbb2")
        observer = self.observer(api)
        with patch("custos_square.time.time", return_value=self.now), patch("custos_observe.time.time", return_value=self.now):
            observer.source("square-outbox", 60, observer.outbox)
            observer.now += 600
            observer.source("square-outbox", 60, observer.outbox)  # still backing off: nothing happens
        self.assertEqual([k for k in self.native.messages if k.startswith("allowance-wait:")], ["allowance-wait:bbbbbbb1"])
        api.remaining = 12
        observer.now = self.reset_ms / 1000 + 31
        with patch("custos_square.time.time", return_value=observer.now):
            observer.source("square-outbox", 60, observer.outbox)
        self.assertEqual(api.posted, ["reply bbbbbbb1", "reply bbbbbbb2"])
        self.assertIn("recovery:square-outbox:1", self.native.messages)
        self.assertEqual(self.store.get("outbox:square_pending")["count"], 0)

    def test_withdrawn_reply_is_skipped_and_the_rest_still_deliver(self):
        api = self.square(remaining=12)
        self.compose("ccccccc1")
        self.compose("ccccccc2")
        self.compose("ccccccc3")
        from custos_observe import main
        import io, contextlib
        with patch("custos_observe.Store", return_value=self.store), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["withdraw", "ccccccc2"]), 0)
        self.assertIn("ccccccc2", out.getvalue())
        observer = self.observer(api)
        with patch("custos_square.time.time", return_value=self.now):
            observer.source("square-outbox", 60, observer.outbox)
        self.assertEqual(api.posted, ["reply ccccccc1", "reply ccccccc3"])
        self.assertIn("withdrawn:ccccccc2", self.native.messages)
        self.assertNotIn("delivery:ccccccc2", self.native.messages)
        from custos_square import square_queue
        self.assertEqual(square_queue(self.store), [])
