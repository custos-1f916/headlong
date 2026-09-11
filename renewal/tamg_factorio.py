#!/usr/bin/env python3
"""Custos's small, persistent TAMG MCP client (stdlib only)."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
import uuid

ENDPOINT = "https://mcp.theagentmustgrow.com/mcp"
STATE = Path("/var/lib/custos-harness/identities/custos/workdir/tamg/state")
PUBLIC = {"briefing", "get_capabilities"}
READS = PUBLIC | {"session_status", "observe", "scan_machines", "scan_resources",
    "scan_hazards", "survey_resources", "find_machines", "world_map", "observe_water",
    "check_placement", "technology", "recipe", "plan_craft", "actions", "action_status",
    "roster", "read_messages"}
MUTATIONS = {"walk_to", "gather", "place", "fuel", "deposit", "withdraw", "clear_cursor",
    "craft", "research", "set_recipe", "log", "send_message"}
SECRET_FIELDS = {"session_key", "recovery_key", "authorization", "mcp-session-id"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")


class Failure(Exception):
    pass


def atomic(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        d = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def decode(raw, request_id):
    """Read a JSON response or the matching JSON-RPC event in an SSE response."""
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    for event in raw.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
        if data and data != "[DONE]":
            result = json.loads(data)
            if result.get("id") == request_id:
                return result
    raise Failure("MCP response had no matching result")


class MCP:
    def __init__(self):
        self.headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                        "User-Agent": "Custos-TAMG/1.0"}
        init = self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                         "clientInfo": {"name": "custos-tamg", "version": "1.0"}})
        self.headers["MCP-Protocol-Version"] = init["protocolVersion"]
        self.rpc("notifications/initialized", {}, notification=True)

    def rpc(self, method, params, notification=False):
        rid = uuid.uuid4().hex
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = rid
        req = urllib.request.Request(ENDPOINT, json.dumps(body).encode(), self.headers)
        # No automatic retries: a timeout can follow a committed game mutation.
        with urllib.request.urlopen(req, timeout=25) as response:
            session = response.headers.get("Mcp-Session-Id")
            if session:
                self.headers["Mcp-Session-Id"] = session
            if notification:
                return None
            if "text/event-stream" in response.headers.get("Content-Type", ""):
                event = []
                for line in response:
                    event.append(line.decode())
                    if not line.strip():
                        try:
                            result = decode("".join(event), rid)
                            break
                        except Failure:
                            event = []
                else:
                    raise Failure("MCP stream ended without a matching result")
            else:
                result = decode(response.read().decode(), rid)
        if result.get("id") != rid or result.get("error"):
            raise Failure("MCP error: " + json.dumps(result.get("error", "response ID mismatch")))
        return result["result"]

    def tool(self, name, arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise Failure("Tool error: " + json.dumps(result))
        if "structuredContent" in result:
            return result["structuredContent"]
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except ValueError:
                    return {"text": block["text"]}
        return result


class Client:
    def __init__(self, state=STATE, factory=MCP):
        self.state = Path(state)
        self.factory = factory
        self._remote = None

    @contextlib.contextmanager
    def lock(self):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)
        with (self.state / "client.lock").open("a+") as f:
            os.chmod(f.name, 0o600)
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure("Another TAMG client command is running; wait for it")
            yield

    def key(self):
        p = self.state / "session_key.txt"
        if not p.exists() or not p.read_text().strip():
            raise Failure("Saved TAMG session key missing; recover the existing identity, do not register another")
        p.chmod(0o600)
        return p.read_text().strip()

    def secrets(self):
        values = []
        for name in ("session_key.txt", "recovery_key.txt"):
            p = self.state / name
            if p.exists():
                values.append(p.read_text().strip())
        reserved = read_json(self.state / "reserve_raw.json", {})
        if reserved.get("recovery_key"):
            values.append(reserved["recovery_key"])
        return [x for x in values if x]

    def clean(self, value):
        if isinstance(value, dict):
            return {k: "<redacted>" if k.lower() in SECRET_FIELDS else self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets():
                value = value.replace(secret, "<redacted>")
        return value

    def save(self, path, value):
        atomic(path, json.dumps(self.clean(value), indent=2) + "\n")

    def local(self):
        return read_json(self.state / "visit.json", {})

    def visit(self, name):
        value = self.local()
        if not name or value.get("id") != name or value.get("ended_at"):
            raise Failure("Use the active visit ID from local status, or begin a new visit")
        return value

    def remote(self, name, arguments):
        if self._remote is None:
            self._remote = self.factory()
        args = dict(arguments)
        if name not in PUBLIC and name != "resume_identity":
            args["session_key"] = self.key()
        result = self._remote.tool(name, args)
        # Null on rejoin is expected and MUST NOT erase the existing key.
        if name in {"join", "resume_identity"} and result.get("session_key"):
            atomic(self.state / "session_key.txt", result["session_key"] + "\n")
        return result

    def record(self, kind, result):
        path = self.state / ("receipt-" + str(time.time_ns()) + "-" + kind + ".json")
        self.save(path, {"at": time.time(), "visit": self.local().get("id"), "tool": kind, "result": result})
        return {"receipt": str(path), "result": self.clean(result)}

    def begin(self, name, adopt=False):
        if not name or not ID.fullmatch(name):
            raise Failure("Choose a visit ID of 1–96 letters, digits, dots, underscores, colons or hyphens")
        previous = self.local()
        if previous and not previous.get("ended_at"):
            if previous["id"] != name:
                raise Failure("An unfinished visit exists: " + previous["id"] + "; reconcile it before starting another")
            status = self.remote("session_status", {})
            if status.get("state", status.get("status")) == "left":
                return self.record("join", self.remote("join", {}))
            return self.record("session_status", status)
        status = self.remote("session_status", {})
        if status.get("state", status.get("status")) != "left" and not adopt:
            raise Failure("Existing server session is not left; inspect session_status, then begin --adopt only after confirming no other controller")
        self.record("previous_visit", previous)
        self.save(self.state / "visit.json", {"id": name, "started_at": time.time()})
        return self.record("join", self.remote("join", {}))

    def call(self, name, args, visit=None):
        if not isinstance(args, dict) or any(k.lower() in SECRET_FIELDS for k in args):
            raise Failure("Pass a JSON object without credentials; the client supplies them")
        if name not in READS | MUTATIONS | {"cancel_action", "stop"}:
            raise Failure("Unsupported tool; inspect schema NAME. Use begin/end/recover for identity operations")
        changing = name not in READS
        if changing:
            self.visit(visit)
        if name in MUTATIONS:
            rid = args.get("request_id")
            if not isinstance(rid, str) or not ID.fullmatch(rid):
                raise Failure("Mutation needs an explicit request_id; reuse the exact ID and arguments after uncertain transport")
            path = self.state / ("intent-" + rid + ".json")
            intent = {"tool": name, "arguments": args}
            old = read_json(path)
            if old is not None and old != intent:
                raise Failure("request_id already belongs to a different intent; inspect its saved receipt")
            self.save(path, intent)  # Before the RPC, including when its outcome is lost.
        pace = read_json(self.state / "last_rpc.json", {})
        delay = (3 if changing else 1) - (time.time() - pace.get("at", 0))
        if delay > 0:
            time.sleep(min(delay, 3))
        self.save(self.state / "last_rpc.json", {"at": time.time()})
        try:
            result = self.remote(name, args)
        except Exception as exc:
            self.record(name, {"error": str(exc), "request_id": args.get("request_id"),
                               "outcome": "unconfirmed; inspect actions and fresh observations before retrying"})
            raise
        return self.record(name, result)

    def end(self, name, note):
        visit = self.visit(name)
        result = self.remote("leave", {})
        if result.get("state", result.get("status")) == "left":
            visit.update(ended_at=time.time(), note=note)
            # Keep history in receipts, without nesting every previous visit forever.
            visit.pop("previous", None)
            self.save(self.state / "visit.json", visit)
        return self.record("leave", result)

    def recover(self, name):
        if not name or not ID.fullmatch(name):
            raise Failure("Choose a valid visit ID")
        previous = self.local()
        if previous and not previous.get("ended_at"):
            self.visit(name)
        else:
            self.record("previous_visit", previous)
            self.save(self.state / "visit.json", {"id": name, "started_at": time.time()})
        p = self.state / "recovery_key.txt"
        key = p.read_text().strip() if p.exists() else read_json(self.state / "reserve_raw.json", {}).get("recovery_key")
        if not key:
            raise Failure("Existing identity recovery key missing; do not register a replacement identity")
        atomic(p, key + "\n")
        return self.record("resume_identity", self.remote("resume_identity", {"recovery_key": key}))

    def schema(self, name):
        if self._remote is None:
            self._remote = self.factory()
        tools = self._remote.rpc("tools/list", {}).get("tools", [])
        return [t for t in tools if not name or t["name"] == name]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Local last/active visit; no network")
    s = sub.add_parser("schema"); s.add_argument("tool", nargs="?")
    s = sub.add_parser("begin"); s.add_argument("visit"); s.add_argument("--adopt", action="store_true")
    s = sub.add_parser("call"); s.add_argument("tool"); s.add_argument("arguments", nargs="?", default="{}"); s.add_argument("--visit")
    s = sub.add_parser("end"); s.add_argument("visit"); s.add_argument("--note", required=True)
    s = sub.add_parser("recover", help="Explicit identity recovery; rotates session and fences previous controller")
    s.add_argument("visit")
    a = p.parse_args()
    client = Client()
    try:
        with client.lock():
            if a.command == "status": result = client.local()
            elif a.command == "schema": result = client.schema(a.tool)
            elif a.command == "begin": result = client.begin(a.visit, a.adopt)
            elif a.command == "call": result = client.call(a.tool, json.loads(a.arguments), a.visit)
            elif a.command == "end": result = client.end(a.visit, a.note)
            else: result = client.recover(a.visit)
            print(json.dumps(client.clean(result), indent=2))
    except Exception as exc:
        print(json.dumps(client.clean({"error": str(exc)})), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
