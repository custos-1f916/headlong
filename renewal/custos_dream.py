"""Scheduled memory review inside the existing mind. No inference or network here."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

import custos_memory as cm

UTC = dt.timezone.utc
DEFAULTS = {"enabled": True, "timezone": "America/Denver", "start": "03:30", "end": "05:00",
            "minutes": 20, "batch_size": 40, "max_edits": 20}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".dream-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(name): os.unlink(name)

def write_json(path, value):
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())

def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default

class Dream:
    def __init__(self, store=None, root=None, config=None, clock=None):
        self.store = store or cm.Store()
        self.root = Path(root or self.store.directory.parent / "dream")
        self.config = dict(DEFAULTS, **(config if config is not None else read_json(Path("/var/lib/custos/config/dream.json") if Path("/var/lib/custos/config/dream.json").exists() else Path(__file__).with_name("dream.json"), {})))
        self.clock = clock or (lambda: dt.datetime.now(UTC))
        self.zone = ZoneInfo(self.config["timezone"])
        self.start = dt.time.fromisoformat(self.config["start"])
        self.end = dt.time.fromisoformat(self.config["end"])
        if self.start >= self.end or not 1 <= self.config["minutes"] <= 60 or not 1 <= self.config["batch_size"] <= 100 or not 1 <= self.config["max_edits"] <= 40:
            raise ValueError("invalid dream schedule or bounds")

    def stamp(self): return self.clock().astimezone(UTC).isoformat()
    def day(self): return self.clock().astimezone(self.zone).date().isoformat()
    def window(self):
        local = self.clock().astimezone(self.zone)
        return self.config["enabled"] and self.start <= local.time().replace(tzinfo=None) < self.end
    def session_path(self): return self.root / self.day() / "session.json"

    def inventory(self):
        reviewed = read_json(self.root / "reviewed.json", {})
        rows = []
        proposals = {read_json(p).get("person_key") for p in (self.root / "person-proposals").glob("*.json")}
        for path, header, body, fields, record in self.store.files():
            key = fields["id"]; digest = sha(path.read_bytes()); flags = []
            if not fields.get("summary") or fields.get("summary") == "---": flags.append("bad-summary")
            if body.startswith("---\n"): flags.append("nested-frontmatter")
            if record and record.get("status") == "active" and record["origin"].get("ambient") and not cm.is_task(record) and not record.get("response") and not record.get("responder_attempt"):
                flags.append("unsettled-ambient")
            if cm.PERSON_MARKER in body:
                note = body.split(cm.PERSON_MARKER)[0]
                try:
                    person = json.JSONDecoder().raw_decode(body.split(cm.PERSON_MARKER, 1)[1].lstrip())[0]
                    if person.get("person_key") in proposals: flags.append("person-candidate")
                except (ValueError, TypeError): flags.append("invalid-person-metadata")
                if len(note) > cm.PERSON_NOTE_MAX + 100: flags.append("long-person-note")
            if re.search(r'(?m)^(?:STAGED|Left:|Next:)', body) and re.search(r'(?m)^(?:DONE|DELIVERED)', body): flags.append("historical-instructions-first")
            if record and record.get("status") == "completed" and cm.is_task(record):
                evidence = record.get("resolution", {}).get("evidence", "").lower()
                if "queued" in evidence or "readback pending" in evidence: flags.append("verify-completion-receipt")
            prior = reviewed.get(key, {})
            rows.append({"id": key, "type": fields.get("type"), "sha256": digest, "bytes": path.stat().st_size,
                         "summary": fields.get("summary", "")[:160], "status": record.get("status") if record else None,
                         "conversation": bool(record and not cm.is_task(record)), "flags": flags,
                         "reviewed_at": prior.get("at", "") if prior.get("sha256") == digest else ""})
        return rows

    def journals(self, s):
        paths = sorted((self.root / s["day"] / "changes").glob("*.json"))
        for path in paths:
            entry = read_json(path)
            if entry["status"] == "prepared":
                target = Path(entry["target"])
                if target.exists() and sha(target.read_bytes()) == entry["after_sha256"] and (entry["operation"] != "archive" or not Path(entry["source"]).exists()):
                    entry["status"] = "applied"; write_json(path, entry)
        s["edits"] = [str(p) for p in paths]  # prepared writes also consume budget
        return s

    def due(self):
        with self.store.lock("dream-schedule"):
            for prior in sorted(self.root.glob("????-??-??/session.json")):
                old = read_json(prior)
                if old["status"] in {"pending", "running"} and (old["day"] < self.day() or
                    (old.get("deadline") and self.stamp() >= old["deadline"]) or
                    (old["day"] == self.day() and self.clock().astimezone(self.zone).time().replace(tzinfo=None) >= self.end)):
                    self._finish(old, "expired", "Window or review budget elapsed; unreviewed entries carry forward.")
            if not self.window(): return ""
            path = self.session_path(); session = read_json(path)
            if session and session["status"] in {"complete", "partial", "expired"}: return ""
            if not session:
                rows = self.inventory()
                # Conversations are primarily historical event receipts. Review their
                # lifecycle flags; rotate the substantive store fairly by last review.
                candidates = [r for r in rows if not r["conversation"] or r["flags"]]
                candidates.sort(key=lambda r: (r["reviewed_at"], not bool(r["flags"]), r["type"] == "memory", r["id"]))
                selected = candidates[:self.config["batch_size"]]
                session = {"version": 1, "day": self.day(), "created": self.stamp(), "status": "pending",
                           "inventory_count": len(rows), "selected": selected, "reviews": {}, "edits": [],
                           "max_edits": self.config["max_edits"], "minutes": self.config["minutes"]}
                write_json(path.parent / "inventory.json", rows); write_json(path, session)
            if session.get("deadline") and self.stamp() >= session["deadline"]:
                self._finish(session, "expired", "Review time budget elapsed; unreviewed entries carry forward.")
                return ""
            if not session.get("announced"):
                if os.environ.get("TRAJ_ID"):
                    cm.append_step({"type": "observation", "source": "memory-dream",
                                    "content": "Scheduled memory review due for " + self.day() + "; load custos-dream. No edits have been implied by this schedule."})
                session["announced"] = True; write_json(path, session)
            return ("Hal's scheduled memory dream is due (" + self.day() + ", America/Denver). "
                    "Choose dream before discretionary explore/make; urgent directed work may take priority. "
                    "Run skills show custos-dream, then custos-dream begin. Review at most " + str(len(session["selected"])) +
                    " selected records, at most " + str(session["max_edits"]) + " edits, " + str(session["minutes"]) +
                    " minutes. Use existing serial inference. No messages, deployment, or new authority are part of this review.")

    def begin(self):
        if not self.window(): raise ValueError("outside early-morning dream window")
        self.due()
        with self.store.lock("dream-schedule"):
            s = read_json(self.session_path())
            if not s or s["status"] not in {"pending", "running"}: raise ValueError("dream already finished for this local day")
            if not s.get("deadline"):
                s["started"] = self.stamp()
                end = dt.datetime.combine(self.clock().astimezone(self.zone).date(), self.end, self.zone).astimezone(UTC)
                s["deadline"] = min(self.clock().astimezone(UTC) + dt.timedelta(minutes=s["minutes"]), end).isoformat()
                s["status"] = "running"; write_json(self.session_path(), s)
            return s

    def active(self):
        s = read_json(self.session_path())
        if not self.window() or not s or s["status"] != "running" or self.stamp() >= s["deadline"]:
            raise ValueError("no active dream budget; finish/report, do not continue editing")
        return self.journals(s)

    def revise(self, key, expected, body, evidence, archive=False, replacement=None, operator=False, memory_type=None):
        """CAS and preimage backups. Operator is for explicit one-time migrations,
        not a CLI switch available in the scheduled workflow."""
        if not evidence.strip(): raise ValueError("specific evidence is required")
        s = None if operator else self.active()
        if s and (key not in {r["id"] for r in s["selected"]} or len(s["edits"]) >= s["max_edits"]): raise ValueError("record or edit outside review budget")
        with self.store.lock():
            if s:
                s = self.active()
                if len(s["edits"]) >= s["max_edits"]: raise ValueError("edit budget exhausted")
            item = self.store.find(key); path, header, old, fields, record = item
            raw = path.read_bytes()
            if sha(raw) != expected: raise ValueError("memory changed since inspection; re-read before editing")
            if archive:
                if fields.get("type") in cm.GOAL_TYPES or fields.get("type") in {"value", "person"} or record:
                    raise ValueError("archive only ordinary superseded facts/notes/memories; preserve people, values, and request receipts")
                if not replacement or replacement == key: raise ValueError("canonical replacement ID required")
                self.store.find(replacement)
            else:
                if not body or len(body.encode()) > 131072: raise ValueError("invalid replacement body")
                if not operator:
                    if fields.get("type") == "value": raise ValueError("values need explicit operator-approved revision; record uncertainty")
                    if record:
                        if cm.MARKER not in body or cm.strict_json(body.split(cm.MARKER, 1)[1]) != record:
                            raise ValueError("preserve request origin, state, response, and evidence; annotate before its record")
                    elif cm.MARKER in body: raise ValueError("cannot manufacture request provenance")
                    if fields.get("type") == "person":
                        if cm.PERSON_MARKER not in body or cm.strict_json(body.rsplit(cm.PERSON_MARKER, 1)[1]) != cm.strict_json(old.rsplit(cm.PERSON_MARKER, 1)[1]):
                            raise ValueError("preserve trusted person identity and routes")
                        if body.startswith("---") or len(body.split(cm.PERSON_MARKER)[0]) > cm.PERSON_NOTE_MAX + 80:
                            raise ValueError("person body must be compact complete prose, not frontmatter")
                # Keep ID, created, type, expiry and custom fields. Summary is quoted
                # JSON (also valid YAML), so colons or '---' cannot corrupt headers.
                if memory_type:
                    if not operator: raise ValueError("category changes require explicit operator migration")
                    header = re.sub(r'(?m)^type:.*$', 'type: ' + memory_type, header)
                summary = json.dumps(body.splitlines()[0][:160], ensure_ascii=False)
                header = re.sub(r'(?m)^summary:.*$', lambda _: 'summary: ' + summary, header)
                candidate = ("---\n" + header + "\n---\n\n" + body.strip() + "\n").encode()
            journal_dir = self.root / ("operator-cleanup" if operator else self.day()) / "changes"
            journal_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            change_id = key + "-" + expected[:16]
            before = journal_dir / (change_id + ".before.md")
            journal = journal_dir / (change_id + ".json")
            target = self.store.archive_dir() / path.name if archive else path
            after_hash = expected if archive else sha(candidate)
            entry = {"id": key, "at": self.stamp(), "evidence": evidence, "before_sha256": expected,
                     "after_sha256": after_hash, "source": str(path), "target": str(target),
                     "backup": str(before), "operation": "archive" if archive else "revise",
                     "replacement": replacement, "status": "prepared"}
            if before.exists(): raise ValueError("prepared change exists; inspect journal before retrying")
            atomic(before, raw); write_json(journal, entry)
            if archive:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.exists(): raise ValueError("archive collision")
                os.replace(path, target); self.store.sync(target)
                fd = os.open(target.parent, os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
            else: atomic(path, candidate)
            if sha(target.read_bytes()) != after_hash: raise ValueError("memory readback failed; preserved preimage")
            entry["status"] = "applied"; write_json(journal, entry)
        if s:
            with self.store.lock("dream-schedule"):
                fresh = self.journals(read_json(self.session_path())); write_json(self.session_path(), fresh)
        return entry

    def review(self, key, expected, verdict, evidence):
        if verdict not in {"keep", "uncertain", "revised", "archived"} or not evidence.strip(): raise ValueError("verdict and evidence required")
        with self.store.lock("dream-schedule"):
            s = self.active()
            if key not in {r["id"] for r in s["selected"]}: raise ValueError("not selected for this dream")
            if verdict == "archived":
                entries = [read_json(Path(p)) for p in s["edits"]]
                matches = [e for e in entries if e["id"] == key and e["operation"] == "archive" and e["status"] == "applied"]
                if not matches: raise ValueError("no verified archive receipt")
                actual = sha(Path(matches[-1]["target"]).read_bytes())
            else: actual = sha(self.store.find(key)[0].read_bytes())
            if actual != expected: raise ValueError("reviewed bytes changed")
            if verdict == "revised" and not any(read_json(Path(p))["id"] == key and read_json(Path(p))["status"] == "applied" for p in s["edits"]): raise ValueError("no revision receipt")
            s["reviews"][key] = {"verdict": verdict, "sha256": actual, "evidence": evidence, "at": self.stamp()}
            write_json(self.session_path(), s)
            # Persist completed decisions even when interrupted before finish.
            seen = read_json(self.root / "reviewed.json", {}); seen[key] = {"sha256": actual, "at": self.stamp()}; write_json(self.root / "reviewed.json", seen)
        return s["reviews"][key]

    def _finish(self, s, status, note):
        self.journals(s)
        report_dir = self.root / s["day"]
        s["status"] = status; s["finished"] = self.stamp(); s["note"] = note
        write_json(report_dir / "session.json", s)
        missing = [r["id"] for r in s["selected"] if r["id"] not in s["reviews"]]
        lines = ["# Memory dream " + s["day"], "", "Status: " + status, "", note, "",
                 f"Inventory: {s['inventory_count']}; selected: {len(s['selected'])}; reviewed: {len(s['reviews'])}; edits: {len(s['edits'])}.", "",
                 "Change journals (prepared is not proof of application): " + ", ".join(s["edits"]), "",
                 "Unreviewed (carry forward): " + (", ".join(missing) or "none"), ""]
        for key, r in s["reviews"].items(): lines.append(f"- {key}: {r['verdict']} — {r['evidence']}")
        atomic(report_dir / "report.md", ("\n".join(lines) + "\n").encode())
        return {"day": s["day"], "status": status, "reviewed": len(s["reviews"]), "remaining": len(missing), "report": str(report_dir / "report.md")}

    def finish(self, note):
        with self.store.lock("dream-schedule"):
            s = read_json(self.session_path())
            if not s or s["status"] not in {"running", "pending"}: raise ValueError("no unfinished dream today")
            result = self._finish(s, "complete" if len(s["reviews"]) == len(s["selected"]) else "partial", note)
        if os.environ.get("TRAJ_ID"):
            cm.append_step({"type": "observation", "source": "memory-dream", "content": "Memory dream " + result["status"] + "; " + str(result["reviewed"]) + " records reviewed, " + str(result["remaining"]) + " carried forward. Private report: " + result["report"]})
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['due','begin','inventory','status','show','revise','archive','review','finish'])
    p.add_argument('id', nargs='?'); p.add_argument('--expected'); p.add_argument('--body-file'); p.add_argument('--evidence', default='')
    p.add_argument('--replacement'); p.add_argument('--verdict'); p.add_argument('--note', default='')
    a = p.parse_args(); d = Dream()
    try:
        if a.command == 'due': print(d.due()); return 0
        if a.command == 'begin': result=d.begin()
        elif a.command == 'inventory': result=d.inventory()
        elif a.command == 'show':
            if not re.fullmatch(r'[0-9a-f]{8}', a.id or ''): raise ValueError('full eight-hex ID required')
            matches = [p for directory in [d.store.directory, d.store.archive_dir()] for p in directory.glob('*.md')
                       if cm.split_memory(p.read_text())[2].get('id') == a.id]
            if len(matches) != 1: raise ValueError('missing or ambiguous memory')
            print(matches[0].read_text()); return 0
        elif a.command == 'status': result=read_json(d.session_path(), {'day':d.day(),'status':'not-scheduled','config':d.config})
        elif a.command in {'revise','archive'}:
            result=d.revise(a.id,a.expected,Path(a.body_file).read_text() if a.body_file else None,a.evidence,a.command=='archive',a.replacement)
        elif a.command == 'review': result=d.review(a.id,a.expected,a.verdict,a.evidence)
        else: result=d.finish(a.note or 'Bounded review finished; see individual evidence and unresolved items.')
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,cm.MemoryError,OSError,KeyError,TypeError) as e:
        print('custos-dream: '+str(e),file=__import__('sys').stderr);return 1

if __name__=='__main__':raise SystemExit(main())
