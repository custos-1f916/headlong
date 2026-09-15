import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custos_reminders as cr


CONTACTS = {"ok": True, "destinations": [
    {"target": "dm:opaque-dani", "label": "Dani", "kind": "dm"},
    {"target": "dm:opaque-hal", "label": "Hal", "kind": "dm"},
]}


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "reminders.sqlite"
        self.now = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)

    def payload(self):
        return {"at": "2026-09-15T06:15:00", "timezone": "America/Denver",
                "target": "Dani", "message": "Morning — put the rice in the rice cooker.",
                "goal_id": "0123abcd"}

    def test_local_time_is_resolved_now_and_schedule_is_idempotent(self):
        with patch.object(cr, "actions_call", return_value=(0, CONTACTS)):
            first = cr.schedule(self.payload(), self.state, self.now)
            second = cr.schedule(self.payload(), self.state, self.now)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["due_utc"], "2026-09-15T12:15:00+00:00")
        self.assertEqual(first["target_label"], "Dani")

    def test_dst_gaps_and_ambiguous_wall_times_are_refused(self):
        with self.assertRaisesRegex(cr.ReminderError, "does not exist"):
            cr.parse_due("2026-03-08T02:30:00", "America/Denver",
                         datetime(2026, 1, 1, tzinfo=timezone.utc))
        with self.assertRaisesRegex(cr.ReminderError, "ambiguous"):
            cr.parse_due("2026-11-01T01:30:00", "America/Denver",
                         datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_dispatch_never_sends_early_and_hands_off_once(self):
        calls = []
        def action(arguments, payload=None, timeout=50):
            calls.append((arguments, payload))
            if arguments == ["signal-contacts"]:
                return 0, CONTACTS
            if arguments[0] == "status":
                return 1, {"ok": False, "error": "unknown request"}
            return 0, {"ok": True, "phase": "queued", "request_id": payload["request_id"]}
        with patch.object(cr, "actions_call", side_effect=action):
            reminder = cr.schedule(self.payload(), self.state, self.now)
            early = cr.dispatch(self.state, datetime(2026, 9, 15, 12, 14, 59, tzinfo=timezone.utc).timestamp())
            due = cr.dispatch(self.state, datetime(2026, 9, 15, 12, 15, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(early["due"], 0)
        sends = [call for call in calls if call[0] == ["signal-send"]]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0][1]["request_id"], reminder["request_id"])
        self.assertEqual(due["queued"], 1)

    def test_timeout_recovery_checks_same_request_before_any_replay(self):
        with patch.object(cr, "actions_call", return_value=(0, CONTACTS)):
            reminder = cr.schedule(self.payload(), self.state, self.now)
        calls = []
        def first(arguments, payload=None, timeout=50):
            calls.append((arguments, payload))
            if arguments[0] == "status":
                return 1, {"ok": False, "error": "unknown request"}
            raise subprocess.TimeoutExpired("custos-actions", 50)
        due = datetime(2026, 9, 15, 12, 15, tzinfo=timezone.utc).timestamp()
        with patch.object(cr, "actions_call", side_effect=first):
            self.assertEqual(cr.dispatch(self.state, due)["retry"], 1)
        def recovered(arguments, payload=None, timeout=50):
            calls.append((arguments, payload))
            return 0, {"ok": True, "phase": "submitted", "request_id": reminder["request_id"]}
        with patch.object(cr, "actions_call", side_effect=recovered):
            result = cr.dispatch(self.state, due + 60)
        self.assertEqual(result["submitted"], 1)
        self.assertEqual(len([call for call in calls if call[0] == ["signal-send"]]), 1)

    def test_cancel_stops_a_pending_reminder(self):
        with patch.object(cr, "actions_call", return_value=(0, CONTACTS)):
            reminder = cr.schedule(self.payload(), self.state, self.now)
        self.assertEqual(cr.cancel(reminder["id"], self.state)["phase"], "cancelled")
        with patch.object(cr, "actions_call") as actions:
            self.assertEqual(cr.dispatch(self.state, datetime(2026, 9, 15, 12, 16, tzinfo=timezone.utc).timestamp())["due"], 0)
            actions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
