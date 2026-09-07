#!/usr/bin/env python3
"""Outside-guest Voidle work channel; Python 3.11+, no third-party dependencies.

Install matching bd at /usr/local/libexec/custos-bd and provision only Voidle
metadata in /var/lib/custos-work/repo/.beads (no routes, hooks, or source tree).
The root-owned systemd EnvironmentFile supplies BEADS_DOLT_SERVER_USER and
BEADS_DOLT_PASSWORD; NEVER ROOT_PASSWORD. The DB account is scoped to voidle.
Keep receipts.sqlite across upgrades/restores: deleting it loses replay protection.

The guest's evidence is retained, digest-checked, commit-addressable attestation,
not an independent test run or proof of correctness. Native --claim arbitrates
ownership atomically. Older bd has no compare-and-set release/close: our serial
lock, prechecks and postchecks cannot prevent an external administrator changing
ownership between commands. Stop the broker before administrative reassignment.
"""
import argparse
import contextlib
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import sqlite3
import subprocess
import tempfile
import time

BD = "/usr/local/libexec/custos-bd"
WORKSPACE = Path("/var/lib/custos-work/repo")
STATE = Path("/var/lib/custos-work")
DB = WORKSPACE / ".beads/dolt"
MAX_INPUT = 32768
MAX_OUTPUT = 1048576
ISSUE = re.compile(r"vd-[a-z0-9][a-z0-9.-]{0,100}\Z")
WRITES = {"create", "claim", "comment", "release", "close"}
FIELDS = {
    "ready": ({"action"}, {"limit"}),
    "show": ({"action", "issue_id"}, set()),
    "create": ({"action", "request_id", "goal_id", "title", "description", "type", "priority"}, set()),
    "claim": ({"action", "request_id", "goal_id", "issue_id", "branch"}, set()),
    "comment": ({"action", "request_id", "goal_id", "issue_id", "text"}, set()),
    "release": ({"action", "request_id", "goal_id", "issue_id", "reason"}, set()),
    "close": ({"action", "request_id", "goal_id", "issue_id", "summary", "evidence"}, set()),
}


class WorkError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum or "\x00" in value:
        raise WorkError("invalid_request", "Invalid " + name, 400)
    return value


def keys(value, required, optional=frozenset()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise WorkError("invalid_request", "Missing or unsupported fields", 400)


def validate(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str) or payload["action"] not in FIELDS:
        raise WorkError("invalid_action", "Only ready/show/create/claim/comment/release/close are allowed", 400)
    action = payload["action"]
    keys(payload, *FIELDS[action])
    if "issue_id" in payload and not ISSUE.fullmatch(text(payload["issue_id"], "issue_id", 104)):
        raise WorkError("namespace", "A complete vd-* issue ID is required", 400)
    if action == "ready":
        limit = payload.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise WorkError("invalid_request", "limit must be 1–50", 400)
    if action in WRITES:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}", text(payload["request_id"], "request_id", 128)):
            raise WorkError("invalid_request", "request_id must be 8–128 safe characters", 400)
        if not re.fullmatch(r"[0-9a-f]{8}", text(payload["goal_id"], "goal_id", 8)):
            raise WorkError("invalid_request", "goal_id must be a full native eight-hex ID", 400)
    if action == "create":
        text(payload["title"], "title", 240)
        text(payload["description"], "description", 8192)
        if payload["type"] not in ("bug", "task") or type(payload["priority"]) is not int or not 0 <= payload["priority"] <= 4:
            raise WorkError("invalid_request", "type must be bug/task and priority 0–4", 400)
    if action == "claim":
        branch = text(payload["branch"], "branch", 160)
        if not re.fullmatch(r"custos/[a-z0-9][a-z0-9/_-]{2,150}", branch) or "//" in branch or branch.endswith("/"):
            raise WorkError("invalid_request", "Use a scoped custos/<name> branch", 400)
    for field, maximum in (("text", 8192), ("reason", 4096), ("summary", 4096)):
        if field in payload:
            text(payload[field], field, maximum)
    if action == "close":
        evidence = payload["evidence"]
        keys(evidence, {"commit", "source_refs", "verification"})
        if not re.fullmatch(r"[0-9a-f]{40}", text(evidence["commit"], "commit", 40)):
            raise WorkError("invalid_evidence", "A full 40-hex source commit is required", 400)
        refs = evidence["source_refs"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
            raise WorkError("invalid_evidence", "Provide 1–20 repository-relative source paths", 400)
        for ref in refs:
            text(ref, "source reference", 300)
            path = PurePosixPath(ref)
            if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in ref or ":" in ref:
                raise WorkError("invalid_evidence", "Source references must be repository-relative paths", 400)
        verification = evidence["verification"]
        keys(verification, {"command", "output", "sha256", "exit_code"})
        text(verification["command"], "verification command", 1024)
        output = text(verification["output"], "verification output", 12000)
        if type(verification["exit_code"]) is not int or verification["exit_code"] != 0:
            raise WorkError("invalid_evidence", "Completion needs successful verification", 400)
        if verification["sha256"] != hashlib.sha256(output.encode("utf-8")).hexdigest():
            raise WorkError("invalid_evidence", "Verification output SHA256 mismatch", 400)
    return action


class Beads:
    """Fixed executable/metadata/environment; callers never supply arbitrary argv."""
    def __init__(self, timeout=25):
        self.timeout = timeout
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(STATE), "USER": "custos", "BEADS_ACTOR": "custos",
                    "BEADS_DIR": str(WORKSPACE / ".beads"), "BD_LAST_TOUCHED_FALLBACK": "0",
                    "BEADS_DOLT_SERVER_HOST": "192.168.86.66", "BEADS_DOLT_SERVER_PORT": "3306"}
        for name in ("BEADS_DOLT_SERVER_USER", "BEADS_DOLT_PASSWORD"):
            if not os.environ.get(name):
                raise RuntimeError("Missing server-only Beads client credentials")
            self.env[name] = os.environ[name]

    def run(self, args, body=None, file_flag=None):
        # A private temporary file avoids both argv bodies and CLI stdin quirks.
        with contextlib.ExitStack() as stack:
            if body is not None:
                handle = stack.enter_context(tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8"))
                handle.write(body)
                handle.flush()
                args = [*args, file_flag, ("@" if file_flag == "--metadata" else "") + handle.name]
            argv = [BD, "--db", str(DB), "--actor", "custos", "--json", "--sandbox", *args]
            try:
                process = subprocess.Popen(argv, cwd=WORKSPACE, env=self.env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                raise WorkError("tracker_unavailable", "Tracker command could not start", 503) from None
            output = bytearray()
            deadline = time.monotonic() + self.timeout
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise WorkError("tracker_uncertain", "Tracker command timed out", 503)
                        for key, _ in selector.select(min(remaining, 0.2)):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            output.extend(chunk)
                            if len(output) > MAX_OUTPUT:
                                raise WorkError("tracker_uncertain", "Tracker output exceeded limit", 503)
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
                if process.returncode:
                    raise WorkError("tracker_rejected", "Tracker rejected the command; no internal diagnostics are exposed", 409)
                try:
                    return json.loads(output)
                except (ValueError, UnicodeError):
                    raise WorkError("tracker_uncertain", "Tracker returned invalid JSON", 503) from None
            except subprocess.TimeoutExpired:
                raise WorkError("tracker_uncertain", "Tracker command timed out", 503) from None
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                process.stdout.close()


def rows(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise WorkError("tracker_shape", "Unsupported tracker response", 503)
    return value


def public_issue(issue):
    # Never forward arbitrary tracker JSON (routing, metadata, integrations).
    allowed = {"id", "title", "description", "acceptance_criteria", "status", "priority", "issue_type",
               "assignee", "created_at", "updated_at", "closed_at", "close_reason"}
    result = {key: value for key, value in issue.items() if key in allowed}
    link = issue.get("metadata", {}).get("custos_work") if isinstance(issue.get("metadata"), dict) else None
    if isinstance(link, dict):
        result["custos_work"] = {key: link[key] for key in ("goal_id", "branch") if key in link}
    return result


class WorkChannel:
    def __init__(self, state, beads, max_active=1):
        self.state, self.beads, self.max_active = Path(state), beads, max_active
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.receipts = self.state / "receipts.sqlite"
        with self.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS receipts (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT)")

    @contextlib.contextmanager
    def connect(self):
        connection = sqlite3.connect(self.receipts)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def show(self, identity):
        result = rows(self.beads.run(["show", identity]))
        if len(result) != 1 or result[0].get("id") != identity:
            raise WorkError("namespace", "Tracker did not return the exact requested Voidle issue", 409)
        return result[0]

    def active(self):
        issues = rows(self.beads.run(["list", "--assignee", "custos", "--limit", "0", "--all"]))
        return [item for item in issues if item.get("status") != "closed"]

    def comments(self, identity):
        return rows(self.beads.run(["comments", identity]))

    @staticmethod
    def marker(payload):
        return "custos-work-op:" + hashlib.sha256(payload["request_id"].encode()).hexdigest()

    @staticmethod
    def identity(payload):
        return payload.get("issue_id") or "vd-custos-" + hashlib.sha256(payload["request_id"].encode()).hexdigest()[:24]

    @staticmethod
    def link(issue):
        metadata = issue.get("metadata")
        value = metadata.get("custos_work") if isinstance(metadata, dict) else None
        return value if isinstance(value, dict) else {}

    def guard(self, payload):
        action = payload["action"]
        if action == "create":
            return None
        issue = self.show(payload["issue_id"])
        metadata = issue.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise WorkError("tracker_shape", "Issue metadata is not an object; operator review required")
        if isinstance(metadata, dict) and "custos_work" in metadata and not isinstance(metadata["custos_work"], dict):
            raise WorkError("tracker_shape", "Existing work link is malformed; operator review required")
        owner = issue.get("assignee") or ""
        if action == "claim":
            if owner not in ("", "custos"):
                raise WorkError("owner_conflict", "Issue belongs to another assignee")
            if issue.get("status") not in ("open", "in_progress"):
                raise WorkError("status_conflict", "Only open/in_progress work may be claimed")
            active = self.active()
            if any(not ISSUE.fullmatch(str(item.get("id", ""))) for item in active):
                raise WorkError("namespace", "Unexpected non-Voidle assignment; operator review required")
            if len([item for item in active if item["id"] != issue["id"]]) >= self.max_active:
                raise WorkError("active_limit", "Finish or release the existing Custos assignment first")
            if owner == "custos" and self.link(issue) and self.link(issue).get("goal_id") != payload["goal_id"]:
                raise WorkError("goal_conflict", "Existing claim belongs to a different native goal")
        else:
            if owner != "custos" or issue.get("status") != "in_progress":
                raise WorkError("owner_conflict", "Only Custos-owned in_progress work may be changed")
            if self.link(issue).get("goal_id") != payload["goal_id"]:
                raise WorkError("goal_conflict", "Use the native goal linked by claim")
            if not self.link(issue).get("branch"):
                raise WorkError("branch_required", "Claim on a scoped branch before changing work")
        return issue

    def mutate(self, payload, issue):
        action, identity, marker = payload["action"], self.identity(payload), self.marker(payload)
        link = {"goal_id": payload["goal_id"], "operation": marker}
        if action == "create":
            body = payload["description"] + "\n\n" + marker + "\nNative goal: " + payload["goal_id"]
            return self.beads.run(["create", "--id", identity, "--title", payload["title"], "--type", payload["type"],
                                   "--priority", str(payload["priority"]), "--metadata", encoded({"custos_work": link})],
                                  body, "--body-file")
        if action == "claim":
            link["branch"] = payload["branch"]
            # bd --set-metadata stores string values, not nested JSON objects.
            # Submit the native JSON document while retaining existing fields.
            metadata = {**(issue.get("metadata") or {}), "custos_work": link}
            return self.beads.run(["update", identity, "--claim"], encoded(metadata), "--metadata")
        if action == "comment":
            body = payload["text"] + "\n\n" + marker + "\nNative goal: " + payload["goal_id"]
            return self.beads.run(["comments", "add", identity, "--author", "custos"], body, "--file")
        if action == "release":
            link.update(branch=self.link(issue)["branch"], release_reason=payload["reason"])
            metadata = {**issue["metadata"], "custos_work": link}
            return self.beads.run(["update", identity, "--assignee", "", "--status", "open"],
                                  encoded(metadata), "--metadata")
        reason = encoded({"summary": payload["summary"], "evidence": payload["evidence"], "operation": marker,
                          "goal_id": payload["goal_id"], "branch": self.link(issue)["branch"],
                          "assurance": "Guest-reported verification; digest checked, not independently executed or correctness-certified"})
        return self.beads.run(["close", identity], reason, "--reason-file")

    def reconcile(self, payload):
        """Positive observation only. Missing markers NEVER authorize a repeat write."""
        identity, marker, action = self.identity(payload), self.marker(payload), payload["action"]
        issue = self.show(identity)
        link = self.link(issue)
        matched = False
        if action == "create":
            expected = payload["description"] + "\n\n" + marker + "\nNative goal: " + payload["goal_id"]
            matched = (issue.get("description") == expected and issue.get("created_by") == "custos"
                       and issue.get("title") == payload["title"] and issue.get("issue_type") == payload["type"]
                       and issue.get("priority") == payload["priority"] and link.get("goal_id") == payload["goal_id"])
        elif action == "comment":
            expected = payload["text"] + "\n\n" + marker + "\nNative goal: " + payload["goal_id"]
            matched = any(comment.get("author") == "custos" and comment.get("text") == expected
                          for comment in self.comments(identity))
        elif action == "claim":
            matched = link.get("operation") == marker and link.get("goal_id") == payload["goal_id"] and link.get("branch") == payload["branch"] and issue.get("assignee") == "custos" and issue.get("status") == "in_progress"
        elif action == "release":
            matched = (link.get("operation") == marker and link.get("goal_id") == payload["goal_id"]
                       and link.get("release_reason") == payload["reason"] and not issue.get("assignee")
                       and issue.get("status") == "open")
        elif action == "close":
            try:
                reason = json.loads(issue.get("close_reason", ""))
            except (ValueError, TypeError):
                reason = {}
            matched = (isinstance(reason, dict) and reason.get("operation") == marker
                       and reason.get("evidence") == payload["evidence"] and reason.get("goal_id") == payload["goal_id"]
                       and reason.get("summary") == payload["summary"]
                       and issue.get("status") == "closed" and issue.get("assignee") == "custos")
        if not matched:
            return None
        result = {"ok": True, "action": action, "request_id": payload["request_id"], "issue": public_issue(issue)}
        if action == "claim" and len(self.active()) > self.max_active:
            result["warning"] = "External assignment race: active limit exceeded; stop new work and ask operator to reconcile"
        return result

    def handle(self, payload):
        action = validate(payload)
        # Separate processes sharing this receipt store also serialize admission.
        with (self.state / "work.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if action == "ready":
                issues = rows(self.beads.run(["ready", "--unassigned", "--limit", str(payload.get("limit", 20))]))
                issues = [public_issue(item) for item in issues if ISSUE.fullmatch(str(item.get("id", ""))) and not item.get("assignee")]
                return {"ok": True, "issues": issues, "active": [public_issue(item) for item in self.active() if ISSUE.fullmatch(str(item.get("id", "")))]}
            if action == "show":
                issue = self.show(payload["issue_id"])
                comments = [{key: item[key] for key in ("id", "author", "text", "created_at") if key in item}
                            for item in self.comments(payload["issue_id"])]
                return {"ok": True, "issue": public_issue(issue), "comments": comments}
            digest = hashlib.sha256(encoded(payload).encode()).hexdigest()
            with self.connect() as connection:
                receipt = connection.execute("SELECT digest,state,result FROM receipts WHERE request_id=?", (payload["request_id"],)).fetchone()
                if receipt:
                    if receipt[0] != digest:
                        raise WorkError("request_conflict", "request_id was already used with a different payload")
                    if receipt[1] == "done":
                        return {**json.loads(receipt[2]), "replayed": True}
                    try:
                        result = self.reconcile(payload)
                    except WorkError:
                        result = None
                    if result:
                        connection.execute("UPDATE receipts SET state='done',result=? WHERE request_id=?", (encoded(result), payload["request_id"]))
                        return {**result, "replayed": True, "reconciled": True}
                    raise WorkError("pending", "Write outcome unresolved. Retry the SAME request/payload to reconcile; do not use a new ID. Operator review may be required")
                # Any uncertain write blocks subsequent writes, not reads/reconciliation.
                # In particular a lost claim response must not admit a second issue.
                if connection.execute("SELECT 1 FROM receipts WHERE state='pending' LIMIT 1").fetchone():
                    raise WorkError("pending", "An earlier write is unresolved; reconcile its original request before new writes")
            issue = self.guard(payload)
            with self.connect() as connection:
                connection.execute("INSERT INTO receipts VALUES (?,?,?,'pending',NULL)", (payload["request_id"], digest, encoded(payload)))
            # From this durable boundary onward every error is uncertain, even an
            # apparent rejection: some bd versions write before reporting errors.
            try:
                self.mutate(payload, issue)
            except WorkError:
                pass
            try:
                result = self.reconcile(payload)
            except WorkError:
                result = None
            if result is None:
                raise WorkError("pending", "Write outcome unresolved. Retry the SAME request/payload; no automatic duplicate write will occur")
            with self.connect() as connection:
                connection.execute("UPDATE receipts SET state='done',result=? WHERE request_id=?", (encoded(result), payload["request_id"]))
            return {**result, "replayed": False}


class Handler(BaseHTTPRequestHandler):
    server_version = "CustosWork"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # No request bodies, query strings or tracker diagnostics in logs.

    def reply(self, status, value):
        body = encoded(value).encode("utf-8")
        if len(body) > MAX_OUTPUT:
            status, body = 503, b'{"ok":false,"error":"response_limit"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_POST(self):
        try:
            if self.client_address[0] != self.server.allowed_client:
                raise WorkError("forbidden", "Client not allowed", 403)
            if self.path != "/v1/work":
                raise WorkError("not_found", "Only /v1/work is available", 404)
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0]):
                raise WorkError("invalid_request", "Exactly one bounded Content-Length is required", 400)
            length = int(lengths[0])
            if not 0 < length <= MAX_INPUT:
                raise WorkError("request_limit", "Request body too large or empty", 413)
            if self.headers.get_content_type() != "application/json":
                raise WorkError("invalid_request", "application/json is required", 415)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise WorkError("invalid_request", "Incomplete body", 400)
            payload = json.loads(raw)
            self.reply(200, self.server.channel.handle(payload))
        except WorkError as error:
            self.reply(error.status, {"ok": False, "error": error.code, "message": str(error)})
        except (ValueError, UnicodeError, RecursionError):
            self.reply(400, {"ok": False, "error": "invalid_json"})
        except (BrokenPipeError, ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.reply(503, {"ok": False, "error": "unavailable", "message": "Host failure; any submitted write may be pending. Reuse its request_id"})

    def do_GET(self):
        self.reply(405, {"ok": False, "error": "method_not_allowed"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default="/etc/custos-work/work-policy.json")
    args = parser.parse_args()
    policy = json.loads(Path(args.policy).read_text())
    keys(policy, {"listen", "port", "allowed_client", "max_active", "command_timeout_seconds"})
    if (policy["listen"], policy["port"], policy["allowed_client"]) != ("192.168.86.44", 18081, "192.168.86.52"):
        raise RuntimeError("Work endpoint must remain on the approved host and client")
    if type(policy["max_active"]) is not int or policy["max_active"] != 1:
        raise RuntimeError("This work channel admits one active issue")
    if type(policy["command_timeout_seconds"]) is not int or not 5 <= policy["command_timeout_seconds"] <= 30:
        raise RuntimeError("Invalid command timeout")
    for name in ("metadata.json", "config.yaml"):
        if not (WORKSPACE / ".beads" / name).is_file():
            raise RuntimeError("Provision the fixed Voidle metadata before starting; broker never bootstraps it")
    if (WORKSPACE / ".git").exists() or any((WORKSPACE / ".beads" / name).exists() for name in ("routes.jsonl", "hooks")):
        raise RuntimeError("Work metadata must not contain repository hooks or cross-project routes")
    metadata = json.loads((WORKSPACE / ".beads/metadata.json").read_text())
    expected = {"database": "dolt", "backend": "dolt", "dolt_mode": "server", "dolt_database": "voidle"}
    if not isinstance(metadata, dict) or any(metadata.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Metadata must select the existing Voidle server database")
    channel = WorkChannel(STATE, Beads(policy["command_timeout_seconds"]), policy["max_active"])
    # Deliberately single-threaded: bounded queue and no thread/process fan-out.
    with HTTPServer((policy["listen"], policy["port"]), Handler) as server:
        server.allowed_client = policy["allowed_client"]
        server.channel = channel
        server.serve_forever()


if __name__ == "__main__":
    main()
