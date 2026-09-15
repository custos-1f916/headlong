#!/usr/bin/env python3
"""Outside-guest fixed-destination inference gateway. No usage quota.

Custos is Johan's primary client. New calls wait for an idle backend snapshot;
other callers never preempt an admitted Custos stream. The operator pause,
per-request timeout, single in-flight call and bounded framing remain enforced.
Historical quota.sqlite3 is intentionally neither read nor modified.
"""
import argparse
import asyncio
import contextlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import time
from custos_images import inline_jpeg, MAX_IMAGES

UPSTREAM = ("johan.lan", 8080)
MODEL = "qwen3.8-27b"
MODEL_PATHS = {"/v1/models", "/v1/models/" + MODEL}
# Where this gateway may listen: on blink1 (historical) or in LXC 131 custos-brain (2026-09-11,
# Hal: "Nothing should live on Blink1 directly"). The client is always LXC 122.
BIND_HOSTS = ("192.168.86.44", "192.168.86.69")
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


def payload(raw, max_tokens, models=None):
    try:
        value = strict_json(raw)
        allowed = {"model", "messages", "stream", "stream_options", "max_tokens",
                   "max_completion_tokens", "temperature", "top_p", "top_k", "min_p",
                   "stop", "seed", "presence_penalty", "frequency_penalty", "tools",
                   "tool_choice", "parallel_tool_calls", "response_format", "reasoning_effort", "n"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("unsupported fields")
        if value.get("model") not in (models or {MODEL}):
            raise Denied(400, "unsupported_model")
        if value.get("n", 1) != 1:
            raise ValueError("unsupported sample count")
        if "stream" in value and type(value["stream"]) is not bool:
            raise ValueError("stream must be boolean")
        if "max_tokens" in value and "max_completion_tokens" in value:
            raise ValueError("ambiguous output limit")
        limit = value.pop("max_completion_tokens", value.get("max_tokens", max_tokens))
        if type(limit) is not int or not 1 <= limit <= max_tokens:
            raise ValueError("output limit outside policy")
        value["max_tokens"] = limit
        effort = value.setdefault("reasoning_effort", "xhigh")
        if effort not in ("medium", "xhigh"):
            raise Denied(400, "unsupported_reasoning_effort")
        messages = value.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
            raise ValueError("invalid messages")
        image_count = 0
        text_bytes = 0
        for message in messages:
            if not isinstance(message, dict) or set(message) - {"role", "content", "name", "tool_calls", "tool_call_id"}:
                raise ValueError("unsupported message")
            if message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
                raise ValueError("unsupported role")
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part,dict):
                        raise ValueError('invalid content part')
                    if set(part)=={'type','text'} and part['type']=='text' and isinstance(part['text'],str):
                        text_bytes += len(part['text'].encode())
                    elif (set(part)=={'type','image_url'} and part['type']=='image_url' and
                          message['role']=='user' and isinstance(part['image_url'],dict) and
                          set(part['image_url'])=={'url'}):
                        inline_jpeg(part['image_url']['url'])
                        image_count += 1
                        if image_count > MAX_IMAGES: raise ValueError('too many images')
                    else:
                        raise ValueError('only inline text and bounded embedded JPEG images are admitted')
            elif content is not None and not isinstance(content, str):
                raise ValueError("invalid content")
            elif isinstance(content,str):
                text_bytes += len(content.encode())
        # 256 KiB, not 128: the mind's context cap moved from 96 to 192 KiB on
        # 2026-09-09 (johan prefills ~6k tok/s, so a 49K-token call costs ~8 s).
        if text_bytes > 262144:
            raise ValueError('text exceeds admitted envelope')
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise Denied(400, "invalid_completion_request") from None


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
    def __init__(self, policy, busy=None, clock=time.time, upstream=UPSTREAM, brain=None):
        self.p = policy
        self.busy = busy or BusySignal(policy["busy_signal"], policy["busy_max_age_seconds"])
        self.clock, self.upstream = clock, upstream
        # The brain policy (custos_brain.Brain) decides between johan and a cloud tier; without
        # one this is the fixed-destination gateway it always was.
        self.brain = brain
        self.inflight = False
        # asyncio.Lock is FIFO: a fresh monolith call cannot overtake a waiting
        # responder. Hold only during one completion, never across agent tools.
        self.slot = asyncio.Lock()
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
                await asyncio.wait({eof}, timeout=min(0.1, max(0, monotonic_deadline - time.monotonic())))
        finally:
            eof.cancel()
            await asyncio.gather(eof, return_exceptions=True)

    async def proxy(self, writer, method, path, body, sent, upstream=None):
        upstream_writer = None
        upstream = upstream or self.upstream
        try:
            reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(*upstream, limit=self.p["max_header_bytes"]),
                self.p["connect_timeout_seconds"])
            upstream_writer.write((f"{method} {path} HTTP/1.1\r\nHost: {upstream[0]}:{upstream[1]}\r\n"
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

    async def admitted_proxy(self, writer, method, path, body, sent):
        async with self.slot:
            while True:
                self.check_pause()
                try:
                    self.busy.check(self.clock())
                    break
                except Denied as denial:
                    if denial.code != "backend_busy_or_unavailable":
                        raise
                    # The independent watcher bounds waiting and cancels it on
                    # disconnect/pause. Never poll the inference endpoint here.
                    await asyncio.sleep(0.2)
            self.inflight = True
            try:
                await self.proxy(writer, method, path, body, sent)
            finally:
                self.inflight = False

    def cloud_mode(self):
        return self.brain is not None and self.brain.mode(self.clock()) == "cloud"

    async def brain_completion(self, writer, chat, sent, complete):
        """One completion through a Brain backend, preserving Chat Completions framing."""
        stream = bool(chat.get("stream"))
        content_type = "text/event-stream" if stream else "application/json"

        def emit(data):
            if not sent[0]:
                writer.write((f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nTransfer-Encoding: chunked\r\n"
                              "Connection: close\r\nCache-Control: no-store\r\n\r\n").encode())
                sent[0] = True
            writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        status, error = await complete(chat, emit)
        if status != 200:
            code = "brain_upstream_error"
            if isinstance(error, dict):
                inner = error.get("error") if isinstance(error.get("error"), dict) else error
                code = str(inner.get("code") or inner.get("type") or code)[:60]
            raise Denied(status if status in (400, 401, 403, 404, 408, 413, 429, 500, 502, 503) else 502, code, 30)
        if not sent[0]:
            raise Denied(502, "empty_brain_response", 30)
        writer.write(b"0\r\n\r\n")
        await asyncio.wait_for(writer.drain(), self.p["io_timeout_seconds"])

    async def cloud_completion(self, writer, chat, sent):
        """One completion through the time-bounded Codex cloud tier."""
        await self.brain_completion(writer, chat, sent, self.brain.complete)

    async def backup_completion(self, writer, chat, sent):
        """One completion through OpenRouter after local failure is established."""
        await self.brain_completion(writer, chat, sent, self.brain.complete_backup)

    async def upstream_healthy(self, upstream):
        """Bounded local health probe used only to label/select the Tycho fallback."""
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(*upstream, limit=self.p["max_header_bytes"]),
                self.p["connect_timeout_seconds"])
            writer.write(("GET /health HTTP/1.1\r\nHost: %s:%s\r\nConnection: close\r\n\r\n" % upstream).encode())
            await asyncio.wait_for(writer.drain(), self.p["io_timeout_seconds"])
            line, headers = await asyncio.wait_for(
                read_headers(reader, self.p["max_header_bytes"]), self.p["connect_timeout_seconds"])
            parts = line.split(" ", 2)
            return (len(parts) >= 2 and parts[1] == "200" and
                    headers.get("content-type", "").split(";", 1)[0].lower() == "application/json")
        except (Denied, OSError, ValueError, asyncio.TimeoutError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return False
        finally:
            await close_writer(writer)

    async def local_route(self, now):
        """Return Johan, Tycho, or OpenRouter for a non-admitting snapshot."""
        try:
            self.busy.check(now, admission=False)
            return "johan"
        except Denied as denial:
            if denial.code != "busy_observation_unavailable" or self.brain is None:
                raise
        tycho = self.brain.tycho_upstream()
        if tycho and await self.upstream_healthy(tycho):
            return "tycho"
        if self.brain.backup_ready():
            return "openrouter"
        raise Denied(503, "local_fallbacks_unavailable", 30)

    async def fallback_completion(self, writer, method, path, body, sent, reason):
        """Try Tycho, then OpenRouter; never replay after any response byte."""
        tycho = self.brain.tycho_upstream()
        if tycho:
            try:
                await self.proxy(writer, method, path, body, sent, upstream=tycho)
                self.brain.note_local_route("tycho", reason)
                return
            except (Denied, OSError, ValueError, asyncio.TimeoutError,
                    asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                if sent[0]:
                    raise
        if not self.brain.backup_ready():
            raise Denied(503, "local_fallbacks_unavailable", 30)
        self.brain.note_local_route("openrouter", reason + "; tycho unavailable")
        await self.backup_completion(writer, json.loads(body), sent)

    async def local_fallback_completion(self, writer, method, path, body, sent):
        """Prefer Johan, wait through contention, then try Tycho and OpenRouter in order."""
        async with self.slot:
            while True:
                self.check_pause()
                try:
                    self.busy.check(self.clock())
                    break
                except Denied as denial:
                    if denial.code == "backend_busy_or_unavailable":
                        await asyncio.sleep(0.2)
                        continue
                    if (denial.code == "busy_observation_unavailable" and self.brain is not None and
                            (self.brain.tycho_upstream() or self.brain.backup_ready())):
                        await self.fallback_completion(writer, method, path, body, sent,
                                                       "johan observer unavailable")
                        return
                    raise
            self.inflight = True
            try:
                try:
                    await self.proxy(writer, method, path, body, sent)
                except (Denied, OSError, ValueError, asyncio.TimeoutError,
                        asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                    if (sent[0] or self.brain is None or
                            (not self.brain.tycho_upstream() and not self.brain.backup_ready())):
                        raise
                    await self.fallback_completion(writer, method, path, body, sent,
                                                   "johan failed before response")
                    return
                self.brain.note_local_route("johan", "johan available")
            finally:
                self.inflight = False

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
        sent, jobs = [False], []
        try:
            line, headers = await asyncio.wait_for(read_headers(reader, self.p["max_header_bytes"]), self.p["header_timeout_seconds"])
            parts = line.split(" ")
            if len(parts) != 3 or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
                raise Denied(400, "invalid_request_line")
            method, path, _ = parts
            if path not in MODEL_PATHS | {"/health", "/brain", "/v1/chat/completions"}:
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
            cloud = self.cloud_mode()
            if method == "POST":
                if headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                    raise Denied(400, "json_content_type_required")
                models = self.brain.accepted_models() if self.brain else None
                body = payload(body, self.p["max_tokens"], models)
                if len(body) > self.p["max_body_bytes"]:
                    raise Denied(413, "normalized_request_body_too_large")
            now = self.clock()
            self.check_pause()
            duration = self.p["request_seconds"]

            async def reply_json(document):
                response = json.dumps(document).encode()
                writer.write((f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(response)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n").encode() + response)
                sent[0] = True
                await asyncio.wait_for(writer.drain(), self.p["io_timeout_seconds"])
            if path == "/brain":
                if self.brain:
                    document = self.brain.status()
                    if document["mode"] == "local":
                        route = await self.local_route(now)
                        document["effective_model"] = ({"tycho": self.brain.tycho_label(),
                                                        "openrouter": self.brain.backup_model()}.get(
                                                            route, document["local_model"]))
                        document["local_route"] = route
                else:
                    document = {"mode": "local", "reason": "no brain policy", "effective_model": MODEL}
                await reply_json(document)
                return
            if path == "/health":
                if cloud:
                    await reply_json({"status": "ok", "scope": "brain_cloud_window",
                                      "brain": self.brain.status(), "usage_quota": None})
                    return
                if self.inflight:
                    raise Denied(429, "custos_request_in_flight", 30)
                route = await self.local_route(now) if self.brain else (self.busy.check(now, admission=False) or "johan")
                await reply_json({"status": "ok",
                                  "scope": ({"tycho": "tycho_fallback_ready",
                                             "openrouter": "openrouter_fallback_ready"}.get(
                                                 route, "gateway_admission_snapshot")),
                                  "effective_model": ({"tycho": self.brain.tycho_label(),
                                                       "openrouter": self.brain.backup_model()}.get(
                                                           route, MODEL) if self.brain else MODEL),
                                  "usage_quota": None})
                return
            if cloud and path in MODEL_PATHS:
                names = sorted(self.brain.accepted_models())
                await reply_json({"object": "list", "data": [{"id": name, "object": "model", "owned_by": "custos-brain"} for name in names]})
                return
            if not cloud and path in MODEL_PATHS and self.brain and await self.local_route(now) != "johan":
                names = sorted(self.brain.accepted_models())
                await reply_json({"object": "list", "data": [{"id": name, "object": "model", "owned_by": "custos-brain"} for name in names]})
                return
            started = time.monotonic()
            if cloud and method == "POST":
                # The cloud tier needs no johan admission; the same wall-time bound and the
                # disconnect/pause watcher still apply.
                chat = json.loads(body)
                jobs = [asyncio.create_task(self.cloud_completion(writer, chat, sent)),
                        asyncio.create_task(self.watch(reader, started + duration))]
                done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                if jobs[1] in done:
                    await jobs[1]
                await jobs[0]
                return
            if self.brain is not None and method == "POST":
                # Local mode: every name the brain accepts is served by johan as its one model.
                chat = json.loads(body)
                chat["model"] = MODEL
                body = json.dumps(chat, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            # Existing 600-second end-to-end bound includes admission waiting;
            # no change to client deadlines, output budget or backend concurrency.
            local_job = (self.local_fallback_completion(writer, method, path, body, sent)
                         if self.brain is not None and method == "POST"
                         else self.admitted_proxy(writer, method, path, body, sent))
            jobs = [asyncio.create_task(local_job),
                    asyncio.create_task(self.watch(reader, started + duration))]
            done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            if jobs[1] in done:
                await jobs[1]
            await jobs[0]
        except Denied as denial:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if not sent[0]:
                with contextlib.suppress(ConnectionError, OSError, asyncio.TimeoutError):
                    await self.error(writer, denial)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as failure:
            # Name the failure in the journal (type and a short message, never request content):
            # a silent 503 here cost an hour of guessing on 2026-09-11.
            print("custos-gateway: request failed: %s: %s" % (type(failure).__name__, str(failure)[:200]), flush=True)
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
            await close_writer(writer)
            self.connections -= 1
            self.tasks.discard(asyncio.current_task())


def load_policy(path):
    with open(path, "rb") as stream:
        policy = strict_json(stream.read(16385))
    required = {"bind_host", "bind_port", "allowed_clients", "busy_signal", "pause_file", "request_seconds", "max_tokens", "max_connections", "max_header_bytes",
                "max_body_bytes", "max_response_bytes", "header_timeout_seconds", "body_timeout_seconds",
                "connect_timeout_seconds", "io_timeout_seconds", "busy_max_age_seconds"}
    if not isinstance(policy, dict) or set(policy) != required:
        raise ValueError("policy keys do not match required schema")
    bounds = {"request_seconds": 600,
              "max_tokens": 65536, "max_connections": 16, "max_header_bytes": 16384,
              "max_body_bytes": 4194304, "max_response_bytes": 33554432, "header_timeout_seconds": 5,
              "body_timeout_seconds": 5, "connect_timeout_seconds": 3, "io_timeout_seconds": 5,
              "busy_max_age_seconds": 5}
    for key, maximum in bounds.items():
        if type(policy[key]) not in (int, float) or not 0 < policy[key] <= maximum:
            raise ValueError("unsafe policy limit: " + key)
    for key in ("max_tokens", "max_connections", "max_header_bytes", "max_body_bytes", "max_response_bytes"):
        if type(policy[key]) is not int:
            raise ValueError("integer policy limit required: " + key)
    if policy["bind_host"] not in BIND_HOSTS or policy["bind_port"] != 18080 or policy["allowed_clients"] != ["192.168.86.52"]:
        raise ValueError("production endpoint/client policy is fixed")
    for key in ("busy_signal", "pause_file"):
        if not isinstance(policy[key], str) or not os.path.isabs(policy[key]):
            raise ValueError("host-owned absolute policy path required")
    return policy


async def serve(policy, brain=None):
    gateway = Gateway(policy, brain=brain)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(gateway.accept, policy["bind_host"], policy["bind_port"],
                                        limit=policy["max_header_bytes"], backlog=policy["max_connections"])
    async with server:
        await stop.wait()
    for task in list(gateway.tasks):
        task.cancel()
    await asyncio.gather(*gateway.tasks, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--brain", default=None, help="brain policy JSON (custos_brain); absent = fixed johan gateway")
    parser.add_argument("--state-dir", default="/var/lib/custos-brain")
    args = parser.parse_args()
    os.umask(0o077)
    brain = None
    if args.brain and os.path.exists(args.brain):
        from custos_brain import Brain, load_brain_policy
        brain = Brain(load_brain_policy(args.brain), state_dir=args.state_dir)
        print("custos-brain: " + json.dumps(brain.status()), flush=True)
    asyncio.run(serve(load_policy(args.policy), brain))
