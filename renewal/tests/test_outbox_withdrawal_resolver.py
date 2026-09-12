import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custos_observe import main, resolve_outbox_step_ids
from custos_square import NATIVE_LINE_CAP, Store, canonical, read_native_line


NORMAL_STEP = "a1b2c3d4-1111-2222-3333-444444444444"
SECOND_STEP = "a1b2c3d4-5555-6666-7777-888888888888"
OTHER_STEP = "b1b2c3d4-1111-2222-3333-444444444444"


class OutboxWithdrawalResolverTests(unittest.TestCase):
    """Resolver-only coverage uses an isolated Store and disposable JSONL files."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.db.close)
        self.traj = Path(self.temp.name) / "active.jsonl"

    @staticmethod
    def encoded(row):
        return json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"

    @staticmethod
    def square_message(step_id):
        body = "Ordinary bounded Square reply."
        # Square's public client limit is 8000 UTF-16 units; this is deliberately
        # normal-sized, so the preceding non-message is the only large record.
        assert len(body.encode("utf-16-le")) // 2 <= 8000
        return {
            "type": "message",
            "from": "custos",
            "to": "square:egress:3100:50000",
            "content": body,
            "step_id": step_id,
            "ts": "2026-09-12T17:00:00.000Z",
        }

    def use_active_cursor(self, offset=0):
        self.store.put("outbox:cursor", {"path": str(self.traj), "offset": offset})

    def write_active(self, *records):
        with self.traj.open("wb") as output:
            for record in records:
                output.write(record if isinstance(record, bytes) else self.encoded(record))

    def completed_huge_non_message(self):
        row = {
            "type": "reasoning",
            "run_id": "completed-large-reasoning",
            "thought": "",
            "ts": "2026-09-12T16:59:00.000Z",
        }
        empty = self.encoded(row)
        row["thought"] = "x" * (215917 - len(empty))
        result = self.encoded(row)
        self.assertEqual(len(result), 215917)
        self.assertTrue(result.endswith(b"\n"))
        self.assertGreater(len(result), NATIVE_LINE_CAP)
        return result

    def withdraw(self, prefix):
        output, errors = io.StringIO(), io.StringIO()
        with patch("custos_observe.Store", return_value=self.store), \
                patch("custos_observe.time.time", return_value=1700000000.0), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = main(["withdraw", prefix])
        return result, output.getvalue(), errors.getvalue()

    def test_normal_current_file_message_resolves_by_prefix(self):
        self.write_active(self.square_message(NORMAL_STEP))
        self.use_active_cursor()
        self.assertEqual(resolve_outbox_step_ids(self.store, NORMAL_STEP[:8]), [NORMAL_STEP])

    def test_same_id_in_another_file_does_not_create_a_second_active_match(self):
        old = Path(self.temp.name) / "completed-previous.jsonl"
        old.write_bytes(self.encoded(self.square_message(NORMAL_STEP)))
        self.write_active(self.square_message(NORMAL_STEP))
        self.use_active_cursor()
        self.assertEqual(resolve_outbox_step_ids(self.store, NORMAL_STEP[:8]), [NORMAL_STEP])

    def test_ambiguous_active_prefix_is_rejected_without_a_withdrawal_mark(self):
        self.write_active(self.square_message(NORMAL_STEP), self.square_message(SECOND_STEP))
        self.use_active_cursor()
        result, output, errors = self.withdraw(NORMAL_STEP[:8])
        self.assertEqual(result, 2)
        self.assertEqual(output, "")
        self.assertIn("matches 2 outgoing square rows", errors)
        self.assertIsNone(self.store.get("outbox:skip:" + NORMAL_STEP))
        self.assertIsNone(self.store.get("outbox:skip:" + SECOND_STEP))

    def test_completed_215917_byte_non_message_does_not_hide_later_withdrawal_target(self):
        self.write_active(self.completed_huge_non_message(), self.square_message(NORMAL_STEP))
        self.use_active_cursor()
        self.assertEqual(resolve_outbox_step_ids(self.store, NORMAL_STEP[:8]), [NORMAL_STEP])
        result, output, errors = self.withdraw(NORMAL_STEP[:8])
        self.assertEqual(result, 0)
        self.assertEqual(errors, "")
        self.assertEqual(
            output,
            canonical({
                "withdrawn": NORMAL_STEP,
                "matched_row": True,
                "note": "skipped at the next outbox pass; already-delivered replies cannot be withdrawn",
            }) + "\n",
        )
        self.assertEqual(
            self.store.get("outbox:skip:" + NORMAL_STEP),
            {"at": 1700000000.0, "requested": NORMAL_STEP[:8]},
        )

    def test_completed_malformed_row_is_skipped_before_a_later_message(self):
        self.write_active(b'{"type":"reasoning","thought":not-json}\n', self.square_message(NORMAL_STEP))
        self.use_active_cursor()
        self.assertEqual(resolve_outbox_step_ids(self.store, NORMAL_STEP[:8]), [NORMAL_STEP])

    def test_incomplete_tail_is_not_resolved(self):
        partial_step = "c1b2c3d4-1111-2222-3333-444444444444"
        partial = json.dumps(self.square_message(partial_step), sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.write_active(self.square_message(NORMAL_STEP), partial)
        self.use_active_cursor()
        self.assertEqual(resolve_outbox_step_ids(self.store, NORMAL_STEP[:8]), [NORMAL_STEP])
        self.assertEqual(resolve_outbox_step_ids(self.store, partial_step[:8]), [])

    def test_large_incomplete_tail_remains_at_its_start_and_is_not_parsed_in_chunks(self):
        start = len(self.encoded(self.square_message(NORMAL_STEP)))
        partial = self.completed_huge_non_message()[:-1]
        self.write_active(self.square_message(NORMAL_STEP), partial)
        self.use_active_cursor()
        self.assertEqual(resolve_outbox_step_ids(self.store, NORMAL_STEP[:8]), [NORMAL_STEP])
        self.assertEqual(resolve_outbox_step_ids(self.store, "d1b2c3d4"), [])
        with self.traj.open("rb") as source:
            source.seek(start)
            self.assertEqual(read_native_line(source), (b"", "partial"))
            self.assertEqual(source.tell(), start)


if __name__ == "__main__":
    unittest.main()
