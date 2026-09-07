"""Append-only hardware-fund accounting; no wallet or payment execution.

An evidence file is a retained receipt/acceptance, not a model's assertion.
Money events require Hal's recorded verification statement and matching digest.
These local records are operator-attested, not independent chain verification.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from custos_square import canonical, digest

LEDGER = Path(os.environ.get("CUSTOS_FUND_LEDGER", "/var/lib/custos-fund/ledger.jsonl"))
KINDS = {"earned", "receivable", "received", "expense", "reserve", "release_reserve"}


def asset_key(asset):
    if not isinstance(asset, dict) or set(asset) != {"currency", "chain_id", "token", "decimals"}:
        raise ValueError("asset requires currency, chain_id, token, decimals")
    if not isinstance(asset["currency"], str) or not re.fullmatch(r"[A-Z0-9]{2,12}", asset["currency"]):
        raise ValueError("invalid currency")
    if type(asset["decimals"]) is not int or not 0 <= asset["decimals"] <= 18:
        raise ValueError("invalid decimals")
    if asset["chain_id"] is None:
        if asset["token"] is not None:
            raise ValueError("fiat asset cannot have token")
        return "fiat:" + asset["currency"]
    if type(asset["chain_id"]) is not int or asset["chain_id"] < 1 or not isinstance(asset["token"], str) or not re.fullmatch(r"0x[0-9a-f]{40}", asset["token"]):
        raise ValueError("chain asset requires lowercase token contract")
    return str(asset["chain_id"]) + ":" + asset["token"]


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,77}", value):
        raise ValueError("amount_atomic must be a positive canonical integer string")
    return int(value)


def evidence_checked(evidence):
    if not isinstance(evidence, dict) or set(evidence) != {"path", "sha256", "verified_by", "verification", "source_url"}:
        raise ValueError("evidence requires path, sha256, verified_by, verification, source_url")
    if evidence["verified_by"] != "hal" or not isinstance(evidence["verification"], str) or len(evidence["verification"].strip()) < 20:
        raise ValueError("confirmed accounting requires Hal's substantive verification statement")
    if not isinstance(evidence["source_url"], str) or not evidence["source_url"].startswith(("https://", "file://")):
        raise ValueError("evidence source required")
    path = Path(evidence["path"])
    if not path.is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"]):
        raise ValueError("evidence requires absolute retained path and sha256")
    with path.open("rb") as source:
        data = source.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != evidence["sha256"]:
        raise ValueError("evidence digest mismatch or size exceeded")


def balances(events):
    assets = {}
    receivables = {}
    reserves = {}
    ids = {}
    proofs = set()
    for event in events:
        key = asset_key(event["asset"])
        state = assets.setdefault(key, {"asset": event["asset"], "earned": 0, "receivable": 0, "received": 0, "expense": 0, "reserve": 0})
        if state["asset"] != event["asset"]:
            raise ValueError("asset metadata changed; units cannot be relabelled")
        if event["id"] in ids:
            raise ValueError("duplicate event id")
        n = amount(event["amount_atomic"])
        kind = event["kind"]
        ref = event.get("applies_to")
        if kind not in KINDS:
            raise ValueError("unsupported ledger event")
        if kind in {"earned", "receivable", "received", "expense"}:
            proof = (kind, event["evidence"]["sha256"], event["evidence"]["source_url"])
            if proof in proofs:
                raise ValueError("same economic evidence already booked for this kind")
            proofs.add(proof)
        if kind == "earned":
            if ref:
                raise ValueError("earned does not settle another event")
            state["earned"] += n
        elif kind == "receivable":
            if ref not in ids or ids[ref]["kind"] != "earned" or ids[ref]["asset"] != event["asset"]:
                raise ValueError("receivable must reference same-asset earned acceptance")
            existing = sum(amount(e["amount_atomic"]) for e in ids.values() if e["kind"] == "receivable" and e.get("applies_to") == ref)
            if existing + n > amount(ids[ref]["amount_atomic"]):
                raise ValueError("receivable exceeds evidenced earned amount")
            receivables[event["id"]] = n
            state["receivable"] += n
        elif kind == "received":
            if ref:
                if ref not in receivables or ids[ref]["asset"] != event["asset"] or n > receivables[ref]:
                    raise ValueError("receipt exceeds or mismatches referenced receivable")
                receivables[ref] -= n
                state["receivable"] -= n
            state["received"] += n
        elif kind == "expense":
            if ref:
                raise ValueError("release reserve separately before booking actual expense")
            if n > state["received"] - state["expense"] - state["reserve"]:
                raise ValueError("expense exceeds unreserved confirmed funds")
            state["expense"] += n
        elif kind == "reserve":
            if ref or n > state["received"] - state["expense"] - state["reserve"]:
                raise ValueError("reserve exceeds available confirmed funds")
            reserves[event["id"]] = n
            state["reserve"] += n
        elif kind == "release_reserve":
            if ref not in reserves or ids[ref]["asset"] != event["asset"] or n > reserves[ref]:
                raise ValueError("release exceeds referenced reserve")
            reserves[ref] -= n
            state["reserve"] -= n
        ids[event["id"]] = event
    for state in assets.values():
        state["spendable"] = state["received"] - state["expense"] - state["reserve"]
        for key in ("earned", "receivable", "received", "expense", "reserve", "spendable"):
            state[key + "_atomic"] = str(state.pop(key))
    return {"assets": assets, "autonomous_spend_usd": "0", "permission": "All financial actions require explicit operator approval; spendable is accounting, not authority.", "evidence_basis": "operator-attested retained documents; not a chain verifier or fiat off-ramp", "entries": len(events)}


class Fund:
    def __init__(self, path=LEDGER):
        self.path = Path(path)

    def events(self):
        if not self.path.exists():
            return []
        events = []
        previous = "0" * 64
        with self.path.open() as source:
            for line in source:
                if not line.endswith("\n"):
                    raise ValueError("ledger has incomplete append; operator recovery required")
                event = json.loads(line)
                checksum = event.pop("hash")
                if event["previous_hash"] != previous or digest(event) != checksum:
                    raise ValueError("ledger chain mismatch")
                event["hash"] = checksum
                previous = checksum
                events.append(event)
        return events

    def record(self, event):
        required = {"id", "kind", "asset", "amount_atomic", "description", "evidence"}
        if not isinstance(event, dict) or not required.issubset(event) or set(event) - required - {"applies_to"}:
            raise ValueError("invalid event fields")
        if not isinstance(event["id"], str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,200}", event["id"]):
            raise ValueError("stable event id required")
        if not isinstance(event["description"], str) or not event["description"].strip():
            raise ValueError("description required")
        evidence_checked(event["evidence"])
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            events = self.events()
            for old in events:
                if old["id"] == event["id"]:
                    if {k: old[k] for k in event} != event:
                        raise ValueError("event id conflict")
                    return old
            balances(events + [event])
            event = dict(event, recorded_at=datetime.now(timezone.utc).isoformat(), previous_hash=events[-1]["hash"] if events else "0" * 64)
            event["hash"] = digest(event)
            with self.path.open("a") as destination:
                destination.write(canonical(event) + "\n")
                destination.flush()
                os.fsync(destination.fileno())
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return event


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("balance")
    sub.add_parser("history")
    record = sub.add_parser("record")
    record.add_argument("--file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        fund = Fund()
        if args.command == "record":
            result = fund.record(json.loads(args.file.read_text()))
        elif args.command == "history":
            result = fund.events()
        else:
            result = balances(fund.events())
        print(canonical(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(canonical({"error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
