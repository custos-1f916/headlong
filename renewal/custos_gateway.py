#!/usr/bin/env python3
"""Outside-guest, fixed-destination inference admission. Python 3.11+, no dependencies.

Busy signal is host-produced, never guest-controlled: atomically replace JSON with
observed_at (Unix seconds), foreign_busy (bool), backend_idle (bool). Missing,
stale, future-dated or malformed observations fail closed. This is conservative
snapshot admission, NOT atomic arbitration or GPU preemption. All competing
callers must share a host arbiter to obtain hard interactive priority.

SQLite FULL transactions reserve the entire possible upstream walltime BEFORE
connecting. Only a fully delivered response refunds unused time. Crash, client
cancellation and uncertain upstream failures retain the reservation and prevent
another request until its deadline. Never delete/reset this host-owned database.
"""
import argparse
import asyncio
import contextlib
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import stat
import time
from zoneinfo import ZoneInfo

UPSTREAM = ("192.168.86.117", 8080)
MODEL = "qwen3.8-27b"
DENVER = ZoneInfo("America/Denver")
MODEL_PATHS = {"/v1/models", "/v1/models/" + MODEL}
REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 408: "Request Timeout", 413: "Content Too Large",
           429: "Too Many Requests", 431: "Request Header Fields Too Large",
           500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable"}


class Denied(Exception):
    def __init__(self, status, code, retry=0):
        self.status, self.code, self.retry = status, code, max(0, math.ceil(retry))
        super().__init__(code)


def strict_json(raw, depth_limit=64):
    # Bound nesting before the recursive stdlib decoder touches attacker input.
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > depth_limit:
                raise ValueError("JSON nesting limit")
        elif byte in (93, 125):
            depth -= 1
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def payload(raw, max_tokens):
    try:
        value = strict_json(raw)
        allowed = {"model", "messages", "stream", "stream_options", "max_tokens",
                   "max_completion_tokens", "temperature", "top_p", "top_k", "min_p",
                   "stop", "seed", "presence_penalty", "frequency_penalty", "tools",
                   "tool_choice", "parallel_tool_calls", "response_format", "reasoning_effort", "n"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("unsupported fields")
        if value.get("model") != MODEL or value.get("n", 1) != 1:
            raise ValueError("unsupported model or sample count")
        if "stream" in value and type(value["stream"]) is not bool:
            raise ValueError("stream must be boolean")
        if "max_tokens" in value and "max_completion_tokens" in value:
            raise ValueError("ambiguous output limit")
        limit = value.pop("max_completion_tokens", value.get("max_tokens", max_tokens))
        if type(limit) is not int or not 1 <= limit <= max_tokens:
            raise ValueError("output limit outside policy")
        value["max_tokens"] = limit
        effort = value.setdefault("reasoning_effort", "medium")
        if effort not in ("medium", "xhigh"):
            raise ValueError("only medium or explicit xhigh effort is admitted")
        messages = value.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
            raise ValueError("invalid messages")
        for message in messages:
            if not isinstance(message, dict) or set(message) - {"role", "content", "name", "tool_calls", "tool_call_id"}:
                raise ValueError("unsupported message")
            if message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
                raise ValueError("unsupported role")
            content = message.get("content")
            if isinstance(content, list):
                # Deliberately text-only: upstream URL-bearing vision/audio inputs
                # would turn even a fixed-host proxy into a server-side fetch surface.
                if not all(isinstance(part, dict) and set(part) == {"type", "text"}
                           and part["type"] == "text" and isinstance(part["text"], str)
                           for part in content):
                    raise ValueError("only inline text content is admitted")
            elif content is not None and not isinstance(content, str):
                raise ValueError("invalid content")
        return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise Denied(400, "invalid_completion_request") from None


class Ledger:
    def __init__(self, path, daily_seconds, rolling_seconds):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(path) + ".lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY, day TEXT NOT NULL, started REAL NOT NULL,
                deadline REAL NOT NULL, charged REAL NOT NULL, uncertain INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS clock (id INTEGER PRIMARY KEY CHECK(id=1), latest REAL NOT NULL);
        """)
        self.daily, self.rolling = daily_seconds, rolling_seconds

    def check(self, now, duration):
        """Read-only admission check shared by health and durable reservation."""
        latest = self.db.execute("SELECT latest FROM clock WHERE id=1").fetchone()
        if latest and now < latest[0] - 1:
            raise Denied(503, "clock_moved_backwards", latest[0] - now)
        day = dt.datetime.fromtimestamp(now, DENVER).date().isoformat()
        uncertain = self.db.execute("SELECT MAX(deadline) FROM reservations WHERE uncertain=1").fetchone()[0]
        if uncertain and uncertain > now:
            raise Denied(429, "previous_request_unsettled", uncertain - now)
        daily = self.db.execute("SELECT COALESCE(SUM(charged),0) FROM reservations WHERE day=?", (day,)).fetchone()[0]
        # Whole overlapping reservations conservatively cover interrupted work.
        rolling = self.db.execute("SELECT COALESCE(SUM(charged),0) FROM reservations WHERE deadline>?", (now - 86400,)).fetchone()[0]
        if daily + duration > self.daily:
            tomorrow = dt.datetime.fromtimestamp(now, DENVER).date() + dt.timedelta(days=1)
            retry = dt.datetime.combine(tomorrow, dt.time.min, DENVER).timestamp() - now
            raise Denied(429, "daily_quota", retry)
        if rolling + duration > self.rolling:
            available = rolling
            rows = self.db.execute("SELECT deadline, charged FROM reservations WHERE deadline>? ORDER BY deadline", (now - 86400,)).fetchall()
            for deadline, charge in rows:
                available -= charge
                if available + duration <= self.rolling:
                    raise Denied(429, "rolling_quota", deadline + 86400 - now)
            raise Denied(429, "rolling_quota", 86400)
        return {"day": day, "remaining_daily_seconds": self.daily - daily,
                "remaining_rolling_seconds": self.rolling - rolling}

    def reserve(self, now, duration):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.check(now, duration)
            self.db.execute("INSERT OR REPLACE INTO clock VALUES (1, ?)", (now,))
            cursor = self.db.execute("INSERT INTO reservations(day,started,deadline,charged,uncertain) VALUES(?,?,?,?,1)",
                                     (state["day"], now, now + duration, duration))
            self.db.execute("DELETE FROM reservations WHERE deadline<?", (now - 3 * 86400,))
            self.db.execute("COMMIT")
            return cursor.lastrowid
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, reservation, elapsed):
        # Single fully durable update. Failure leaves the conservative reservation.
        self.db.execute("UPDATE reservations SET charged=MIN(charged,?), uncertain=0 WHERE id=?",
                        (max(0.001, elapsed), reservation))

    def close(self):
        self.db.close()
        self.lock.close()


class BusySignal:
    def __init__(self, path, max_age):
        self.path, self.max_age = path, max_age

    def check(self, now, admission=True):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
                    raise ValueError("untrusted signal file mode")
                if metadata.st_uid != 0:
                    raise ValueError("busy signal must be root-owned")
                value = strict_json(stream.read(4097))
            if set(value) != {"observed_at", "foreign_busy", "backend_idle"}:
                raise ValueError("invalid busy schema")
            observed = value["observed_at"]
            if type(observed) not in (float, int) or not math.isfinite(observed) or not 0 <= now - observed <= self.max_age:
                raise ValueError("stale busy observation")
            if type(value["foreign_busy"]) is not bool or type(value["backend_idle"]) is not bool:
                raise ValueError("invalid busy observation")
        except (OSError, ValueError, TypeError, RecursionError):
            raise Denied(503, "busy_observation_unavailable", 30) from None
        if value["foreign_busy"]:
            raise Denied(503, "backend_reserved_for_other_users", 30)
        if admission and not value["backend_idle"]:
            raise Denied(503, "backend_busy_or_unavailable", 30)


async def read_headers(reader, limit):
    try:
        raw = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
        raise Denied(431, "invalid_or_oversized_headers") from None
    if len(raw) > limit:
        raise Denied(431, "oversized_headers")
    try:
        lines = raw[:-4].decode("ascii").split("\r\n")
        if len(lines) > 65:
            raise ValueError()
        headers = {}
        for line in lines[1:]:
            key, value = line.split(":", 1)
            if not key or any(c not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in key):
                raise ValueError()
            if any(ord(c) < 32 and c != "\t" for c in value) or "\x7f" in value:
                raise ValueError()
            key = key.lower()
            if key in headers:
                raise ValueError()
            headers[key] = value.strip()
        return lines[0], headers
    except (ValueError, UnicodeError):
        raise Denied(400, "invalid_headers") from None


async def close_writer(writer):
    if writer is not None:
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), 1)


class Gateway:
    def __init__(self, policy, ledger=None, busy=None, clock=time.time, upstream=UPSTREAM):
        self.p = policy
        self.ledger = ledger or Ledger(policy["state_db"], policy["daily_seconds"], policy["rolling_24h_seconds"])
        self.busy = busy or BusySignal(policy["busy_signal"], policy["busy_max_age_seconds"])
        self.clock, self.upstream = clock, upstream
        self.inflight = False
        self.connections = 0
        self.tasks = set()

    def check_pause(self):
        if Path(self.p["pause_file"]).exists():
            raise Denied(503, "operator_paused", 300)

    async def error(self, writer, denial):
        body = json.dumps({"error": {"type": "admission_error", "code": denial.code,
                                   "message": denial.code, "retry_after_seconds": denial.retry}}).encode()
        retry = f"Retry-After: {denial.retry}\r\n" if denial.retry else ""
        writer.write((f"HTTP/1.1 {denial.status} {REASONS.get(denial.status, 'Error')}\r\n"
                      f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                      f"Connection: close\r\nCache-Control: no-store\r\n{retry}\r\n").encode() + body)
        await asyncio.wait_for(writer.drain(), self.p["io_timeout_seconds"])

    async def watch(self, reader, monotonic_deadline):
        eof = asyncio.create_task(reader.read(1))
        try:
            while True:
                if eof.done():
                    raise Denied(408, "client_disconnected_or_pipelined")
                now = self.clock()
                self.check_pause()
                if time.monotonic() >= monotonic_deadline:
                    raise Denied(503, "request_walltime_limit", 30)
                self.busy.check(now, admission=False)
                await asyncio.wait({eof}, timeout=min(0.1, max(0, monotonic_deadline - time.monotonic())))
        finally:
            eof.cancel()
            await asyncio.gather(eof, return_exceptions=True)

    async def proxy(self, writer, method, path, body, sent):
        upstream_writer = None
        try:
            reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(*self.upstream, limit=self.p["max_header_bytes"]),
                self.p["connect_timeout_seconds"])
            upstream_writer.write((f"{method} {path} HTTP/1.1\r\nHost: {UPSTREAM[0]}:{UPSTREAM[1]}\r\n"
                                   "Connection: close\r\nContent-Type: application/json\r\n"
                                   f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
            await asyncio.wait_for(upstream_writer.drain(), self.p["io_timeout_seconds"])
            line, headers = await asyncio.wait_for(read_headers(reader, self.p["max_header_bytes"]),
                                                   self.p["request_seconds"])
            parts = line.split(" ", 2)
            if len(parts) < 2 or parts[0] not in {"HTTP/1.0", "HTTP/1.1"} or not parts[1].isdigit():
                raise Denied(502, "invalid_upstream_response", 30)
            status = int(parts[1])
            if not 200 <= status < 600 or 300 <= status < 400:
                raise Denied(502, "upstream_redirect_or_invalid_status", 30)
            content_type = headers.get("content-type", "")
            if content_type.split(";", 1)[0].lower() not in {"application/json", "text/event-stream"}:
                raise Denied(502, "unsupported_upstream_content", 30)
            if headers.get("content-encoding", "identity") != "identity":
                raise Denied(502, "unsupported_upstream_encoding", 30)
            transfer = headers.get("transfer-encoding")
            length = headers.get("content-length")
            if transfer not in (None, "chunked") or (transfer and length is not None):
                raise Denied(502, "invalid_upstream_framing", 30)
            if length is not None and (not length.isdigit() or len(length) > 10):
                raise Denied(502, "invalid_upstream_length", 30)
            remaining = int(length) if length is not None else None
            if remaining is not None and remaining > self.p["max_response_bytes"]:
                raise Denied(502, "upstream_response_too_large", 30)
            retry = ""
            if status in {429, 503}:
                value = headers.get("retry-after", "30")
                retry = f"Retry-After: {value if value.isdigit() and len(value) <= 6 else '30'}\r\n"
            writer.write((f"HTTP/1.1 {status} {REASONS.get(status, 'Upstream Response')}\r\n"
                          f"Content-Type: {content_type}\r\nTransfer-Encoding: chunked\r\n"
                          f"Connection: close\r\nCache-Control: no-store\r\n{retry}\r\n").encode())
            sent[0] = True
            await asyncio.wait_for(writer.drain(), self.p["io_timeout_seconds"])
            total = 0
            while True:
                if transfer:
                    line = await reader.readuntil(b"\r\n")
                    size = line[:-2]
                    if len(size) > 16 or not size or any(c not in b"0123456789abcdefABCDEF" for c in size):
                        raise Denied(502, "invalid_upstream_chunk")
                    chunk_remaining = int(size, 16)
                    if chunk_remaining == 0:
                        # No trailers accepted; avoid unbounded parsing.
                        if await reader.readexactly(2) != b"\r\n":
                            raise Denied(502, "upstream_trailers_not_supported")
                        break
                    if total + chunk_remaining > self.p["max_response_bytes"]:
                        raise Denied(502, "upstream_response_too_large")
                else:
                    if remaining == 0:
                        break
                    chunk_remaining = min(16384, remaining) if remaining is not None else 16384
                while chunk_remaining:
                    chunk = await reader.read(min(16384, chunk_remaining))
                    if not chunk:
                        if transfer or remaining is not None:
                            raise Denied(502, "truncated_upstream_response")
                        chunk_remaining = 0
                        remaining = 0
                        break
                    chunk_remaining -= len(chunk)
                    total += len(chunk)
                    if total > self.p["max_response_bytes"]:
                        raise Denied(502, "upstream_response_too_large")
                    if remaining is not None:
                        remaining -= len(chunk)
                    writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    await asyncio.wait_for(writer.drain(), self.p["io_timeout_seconds"])
                    if not transfer:
                        break
                if transfer and await reader.readexactly(2) != b"\r\n":
                    raise Denied(502, "invalid_upstream_chunk_ending")
            writer.write(b"0\r\n\r\n")
            await asyncio.wait_for(writer.drain(), self.p["io_timeout_seconds"])
        finally:
            await close_writer(upstream_writer)

    def accept(self, reader, writer):
        # Synchronous admission bounds task creation as well as active handlers.
        peer = writer.get_extra_info("peername")
        if not peer or peer[0] not in self.p["allowed_clients"]:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            writer.close()
            return
        if self.connections >= self.p["max_connections"]:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 30\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            writer.close()
            return
        self.connections += 1
        task = asyncio.create_task(self.handle(reader, writer))
        self.tasks.add(task)

    async def handle(self, reader, writer):
        sent, owned, jobs = [False], False, []
        try:
            line, headers = await asyncio.wait_for(read_headers(reader, self.p["max_header_bytes"]), self.p["header_timeout_seconds"])
            parts = line.split(" ")
            if len(parts) != 3 or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
                raise Denied(400, "invalid_request_line")
            method, path, _ = parts
            if path not in MODEL_PATHS | {"/health", "/v1/chat/completions"}:
                raise Denied(404, "path_not_allowed")
            if method != ("POST" if path == "/v1/chat/completions" else "GET"):
                raise Denied(405, "method_not_allowed")
            if any(key in headers for key in ("transfer-encoding", "expect", "content-encoding", "upgrade")):
                raise Denied(400, "unsupported_request_framing")
            length = headers.get("content-length", "0")
            if not length.isdigit() or len(length) > 10:
                raise Denied(400, "invalid_content_length")
            length = int(length)
            if length > self.p["max_body_bytes"]:
                raise Denied(413, "request_body_too_large")
            if method == "GET" and length:
                raise Denied(400, "get_body_not_allowed")
            body = await asyncio.wait_for(reader.readexactly(length), self.p["body_timeout_seconds"])
            if method == "POST":
                if headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                    raise Denied(400, "json_content_type_required")
                body = payload(body, self.p["max_tokens"])
                if len(body) > self.p["max_body_bytes"]:
                    raise Denied(413, "normalized_request_body_too_large")
            now = self.clock()
            self.check_pause()
            self.busy.check(now)
            if self.inflight:
                raise Denied(429, "custos_request_in_flight", 30)
            duration = self.p["request_seconds"]
            if path == "/health":
                allowance = self.ledger.check(now, duration)
                response = json.dumps({"status": "ok", "scope": "gateway_admission_snapshot",
                                       **allowance}).encode()
                writer.write((f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(response)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n").encode() + response)
                sent[0] = True
                await asyncio.wait_for(writer.drain(), self.p["io_timeout_seconds"])
                return
            self.inflight, owned = True, True
            reservation = self.ledger.reserve(now, duration)
            started = time.monotonic()
            jobs = [asyncio.create_task(self.proxy(writer, method, path, body, sent)),
                    asyncio.create_task(self.watch(reader, started + duration))]
            done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            if jobs[1] in done:
                await jobs[1]
            await jobs[0]
            self.ledger.finish(reservation, time.monotonic() - started)
        except Denied as denial:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if not sent[0]:
                with contextlib.suppress(ConnectionError, OSError, asyncio.TimeoutError):
                    await self.error(writer, denial)
        except (OSError, ValueError, sqlite3.Error, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if not sent[0]:
                with contextlib.suppress(ConnectionError, OSError, asyncio.TimeoutError):
                    await self.error(writer, Denied(503, "upstream_or_admission_unavailable", 30))
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if owned:
                self.inflight = False
            await close_writer(writer)
            self.connections -= 1
            self.tasks.discard(asyncio.current_task())


def load_policy(path):
    with open(path, "rb") as stream:
        policy = strict_json(stream.read(16385))
    required = {"bind_host", "bind_port", "allowed_clients", "state_db", "busy_signal", "pause_file", "daily_seconds",
                "rolling_24h_seconds", "request_seconds", "max_tokens", "max_connections", "max_header_bytes",
                "max_body_bytes", "max_response_bytes", "header_timeout_seconds", "body_timeout_seconds",
                "connect_timeout_seconds", "io_timeout_seconds", "busy_max_age_seconds"}
    if not isinstance(policy, dict) or set(policy) != required:
        raise ValueError("policy keys do not match required schema")
    bounds = {"daily_seconds": 7200, "rolling_24h_seconds": 7200, "request_seconds": 180,
              "max_tokens": 32768, "max_connections": 16, "max_header_bytes": 16384,
              "max_body_bytes": 131072, "max_response_bytes": 8388608, "header_timeout_seconds": 5,
              "body_timeout_seconds": 5, "connect_timeout_seconds": 3, "io_timeout_seconds": 5,
              "busy_max_age_seconds": 5}
    for key, maximum in bounds.items():
        if type(policy[key]) not in (int, float) or not 0 < policy[key] <= maximum:
            raise ValueError("unsafe policy limit: " + key)
    for key in ("max_tokens", "max_connections", "max_header_bytes", "max_body_bytes", "max_response_bytes"):
        if type(policy[key]) is not int:
            raise ValueError("integer policy limit required: " + key)
    if policy["bind_host"] != "192.168.86.44" or policy["bind_port"] != 18080 or policy["allowed_clients"] != ["192.168.86.52"]:
        raise ValueError("production endpoint/client policy is fixed")
    for key in ("state_db", "busy_signal", "pause_file"):
        if not isinstance(policy[key], str) or not os.path.isabs(policy[key]):
            raise ValueError("host-owned absolute policy path required")
    return policy


async def serve(policy):
    gateway = Gateway(policy)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(gateway.accept, policy["bind_host"], policy["bind_port"],
                                        limit=policy["max_header_bytes"], backlog=policy["max_connections"])
    try:
        async with server:
            await stop.wait()
        for task in list(gateway.tasks):
            task.cancel()
        await asyncio.gather(*gateway.tasks, return_exceptions=True)
    finally:
        gateway.ledger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    asyncio.run(serve(load_policy(args.policy)))
