import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custos_fund import Fund, balances


class FundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fund = Fund(self.root / "fund.jsonl")
        self.usdc = {"currency": "USDC", "chain_id": 8453, "token": "0x" + "a" * 40, "decimals": 6}
        self.token = {"currency": "1F916", "chain_id": 8453, "token": "0x" + "b" * 40, "decimals": 18}

    def event(self, identity, kind, n="1000000", asset=None, applies_to=None):
        evidence = self.root / (identity + ".receipt")
        evidence.write_text("Retained operator-qualified financial evidence: " + identity)
        result = {"id": identity, "kind": kind, "asset": asset or self.usdc, "amount_atomic": n, "description": "A real accounting event, not a transfer instruction", "evidence": {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(), "verified_by": "hal", "verification": "Operator checked this retained receipt against the original transaction or acceptance.", "source_url": evidence.as_uri()}}
        if applies_to:
            result["applies_to"] = applies_to
        return result

    def test_initial_zero_and_separate_earned_owed_received_reserve(self):
        initial = balances(self.fund.events())
        self.assertEqual(initial["assets"], {})
        self.assertEqual(initial["autonomous_spend_usd"], "0")
        self.fund.record(self.event("acceptance", "earned"))
        state = next(iter(balances(self.fund.events())["assets"].values()))
        self.assertEqual(state["earned_atomic"], "1000000")
        self.assertEqual(state["received_atomic"], "0")
        self.assertEqual(state["spendable_atomic"], "0")
        self.fund.record(self.event("invoice", "receivable", applies_to="acceptance"))
        self.fund.record(self.event("payment", "received", "600000", applies_to="invoice"))
        self.fund.record(self.event("tax", "reserve", "100000"))
        state = next(iter(balances(self.fund.events())["assets"].values()))
        self.assertEqual(state["receivable_atomic"], "400000")
        self.assertEqual(state["received_atomic"], "600000")
        self.assertEqual(state["reserve_atomic"], "100000")
        self.assertEqual(state["spendable_atomic"], "500000")
        self.assertEqual(state["earned_atomic"], "1000000")

    def test_assets_never_add_and_cannot_settle_other_currency(self):
        self.fund.record(self.event("token-gift", "received", "30000000000000000000000000", self.token))
        self.fund.record(self.event("cash-gift", "received", "1000000"))
        state = balances(self.fund.events())
        self.assertEqual(len(state["assets"]), 2)
        self.assertNotIn("total_atomic", state)
        self.fund.record(self.event("earned", "earned"))
        self.fund.record(self.event("owed", "receivable", applies_to="earned"))
        with self.assertRaises(ValueError):
            self.fund.record(self.event("wrong-asset", "received", "1", self.token, "owed"))
        self.assertEqual(len(self.fund.events()), 4)

    def test_missing_or_modified_evidence_cannot_confirm_money(self):
        event = self.event("receipt", "received")
        event["evidence"]["verified_by"] = "custos"
        with self.assertRaises(ValueError):
            self.fund.record(event)
        event["evidence"]["verified_by"] = "hal"
        Path(event["evidence"]["path"]).write_text("different document")
        with self.assertRaises(ValueError):
            self.fund.record(event)
        self.assertEqual(self.fund.events(), [])

    def test_receipt_replay_and_duplicate_evidence_cannot_inflate_balance(self):
        event = self.event("paid", "received")
        first = self.fund.record(event)
        self.assertEqual(self.fund.record(event), first)
        clone = dict(event, id="different-id")
        with self.assertRaises(ValueError):
            self.fund.record(clone)
        self.assertEqual(len(self.fund.events()), 1)

    def test_units_and_overreservation_are_rejected_without_append(self):
        self.fund.record(self.event("paid", "received", "100"))
        with self.assertRaises(ValueError):
            self.fund.record(self.event("reserve-too-much", "reserve", "101"))
        changed = dict(self.usdc, decimals=18)
        with self.assertRaises(ValueError):
            self.fund.record(self.event("relabelled", "received", "1", changed))
        with self.assertRaises(ValueError):
            self.fund.record(self.event("float", "received", "0.1"))
        self.assertEqual(len(self.fund.events()), 1)

    def test_reserve_release_and_actual_expense_are_distinct(self):
        self.fund.record(self.event("paid", "received", "100"))
        self.fund.record(self.event("reserve", "reserve", "70"))
        with self.assertRaises(ValueError):
            self.fund.record(self.event("premature-expense", "expense", "50"))
        self.fund.record(self.event("release", "release_reserve", "50", applies_to="reserve"))
        self.fund.record(self.event("actual-expense", "expense", "50"))
        state = next(iter(balances(self.fund.events())["assets"].values()))
        self.assertEqual(state["reserve_atomic"], "20")
        self.assertEqual(state["expense_atomic"], "50")
        self.assertEqual(state["spendable_atomic"], "30")

    def test_partial_append_and_chain_tamper_fail_closed(self):
        self.fund.record(self.event("paid", "received", "100"))
        original = self.fund.path.read_text()
        self.fund.path.write_text(original.replace('"100"', '"900"'))
        with self.assertRaises(ValueError):
            self.fund.events()
        self.fund.path.write_text(original + '{"id":"incomplete"')
        with self.assertRaises(ValueError):
            self.fund.events()


if __name__ == "__main__":
    unittest.main()
