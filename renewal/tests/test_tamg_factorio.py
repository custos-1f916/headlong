import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tamg_factorio as t


class Remote:
    def __init__(self):
        self.calls = []
        self.results = {"session_status": {"state": "left"}, "join": {"state": "queued", "session_key": None},
                        "leave": {"state": "left"}, "fuel": {"state": "unknown", "action_id": "a1"},
                        "resume_identity": {"session_key": "new-secret"}}

    def tool(self, name, args):
        self.calls.append((name, args))
        result = self.results.get(name, {})
        if isinstance(result, Exception):
            raise result
        return result


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        t.atomic(self.state / "session_key.txt", "session-secret\n")
        t.atomic(self.state / "reserve_raw.json", json.dumps({"recovery_key": "recovery-secret"}))
        self.remote = Remote()
        self.client = t.Client(self.state, lambda: self.remote)
        self.sleep = patch.object(t.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_rejoin_keeps_null_key_and_identity(self):
        self.client.begin("visit-1")
        self.assertEqual(self.client.key(), "session-secret")
        self.assertEqual(self.remote.calls[-1], ("join", {"session_key": "session-secret"}))

    def test_other_visit_and_unowned_active_session_refused(self):
        self.remote.results["session_status"] = {"state": "ready"}
        with self.assertRaises(t.Failure): self.client.begin("one")
        self.client.begin("one", adopt=True)
        with self.assertRaises(t.Failure): self.client.begin("two")

    def test_interrupted_join_can_resume_same_visit(self):
        self.remote.results["join"] = TimeoutError("lost response")
        with self.assertRaises(TimeoutError): self.client.begin("one")
        self.remote.results["join"] = {"state": "ready"}
        self.client.begin("one")
        self.assertEqual(self.remote.calls[-1][0], "join")

    def test_unknown_intent_is_retained_and_different_reuse_refused(self):
        self.client.begin("one")
        args = {"x": 1, "y": 2, "item": "coal", "count": 1, "request_id": "one-fuel"}
        self.remote.results["fuel"] = TimeoutError("server lost response for session-secret")
        with self.assertRaises(TimeoutError): self.client.call("fuel", args, "one")
        self.assertEqual(t.read_json(self.state / "intent-one-fuel.json")["arguments"], args)
        receipts = "".join(p.read_text() for p in self.state.glob("receipt*"))
        self.assertNotIn("session-secret", receipts)
        calls = len(self.remote.calls)
        with self.assertRaises(t.Failure): self.client.call("fuel", dict(args, count=2), "one")
        self.assertEqual(len(self.remote.calls), calls)
        self.remote.results["fuel"] = {"state": "unknown"}
        self.client.call("fuel", args, "one")
        self.assertEqual(self.remote.calls[-1][1]["request_id"], "one-fuel")

    def test_mutation_needs_owner_and_request_id(self):
        with self.assertRaises(t.Failure): self.client.call("clear_cursor", {"request_id": "a"}, "one")
        self.client.begin("one")
        with self.assertRaises(t.Failure): self.client.call("clear_cursor", {}, "one")
        with self.assertRaises(t.Failure): self.client.call("stop", {}, "two")
        with self.assertRaises(t.Failure): self.client.call("join", {}, "one")
        with self.assertRaises(t.Failure): self.client.call("observe", {"session_key": "manual"})

    def test_recovery_uses_recovery_key_and_redacts_new_key(self):
        (self.state / "session_key.txt").unlink()
        result = self.client.recover("one")
        self.assertEqual(self.remote.calls[-1], ("resume_identity", {"recovery_key": "recovery-secret"}))
        self.assertEqual(self.client.key(), "new-secret")
        self.assertNotIn("new-secret", json.dumps(result))
        self.assertEqual((self.state / "session_key.txt").stat().st_mode & 0o777, 0o600)

    def test_leave_only_closes_when_server_left(self):
        self.client.begin("one")
        self.remote.results["leave"] = {"state": "stopping"}
        self.client.end("one", "pending")
        self.assertNotIn("ended_at", self.client.local())
        self.remote.results["leave"] = {"state": "left"}
        self.client.end("one", "verified furnace progress; action unknown")
        self.assertIn("ended_at", self.client.local())
        with self.assertRaises(t.Failure): self.client.call("stop", {}, "one")

    def test_lock_blocks_second_client_and_state_private(self):
        with self.client.lock():
            with self.assertRaises(t.Failure):
                with t.Client(self.state).lock(): pass
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_redaction_covers_nested_fields_and_echoed_secrets(self):
        value = {"session_key": "new-unseen", "nested": [{"recovery_key": "new-unseen", "error": "echo session-secret recovery-secret"}]}
        result = json.dumps(self.client.clean(value))
        for forbidden in ("new-unseen", "session-secret", "recovery-secret"):
            self.assertNotIn(forbidden, result)


class ProtocolTests(unittest.TestCase):
    def test_handshake_session_header_and_stream_does_not_wait_for_eof(self):
        calls = []
        class Response:
            def __init__(self, request):
                self.body = json.loads(request.data)
                self.headers = {"Mcp-Session-Id": "mcp-session"}
                self.stream = self.body["method"] == "tools/call"
                self.headers["Content-Type"] = "text/event-stream" if self.stream else "application/json"
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def result(self):
                result = {"structuredContent": {"state": "ready"}} if self.stream else {"protocolVersion": "2025-11-25"}
                return json.dumps({"id": self.body.get("id"), "result": result})
            def read(self):
                if self.stream: raise AssertionError("Must not wait for an open SSE stream to close")
                return self.result().encode()
            def __iter__(self):
                yield b'data: {"method":"progress"}\n'
                yield b'\n'
                yield ("data: " + self.result() + "\n").encode()
                yield b'\n'
                raise AssertionError("Must stop reading after the matching RPC result")
        def open_url(request, **kwargs):
            calls.append(request)
            return Response(request)
        with patch.object(t.urllib.request, "urlopen", side_effect=open_url):
            client = t.MCP()
            self.assertEqual(client.tool("session_status", {}), {"state": "ready"})
        self.assertEqual([json.loads(c.data)["method"] for c in calls], ["initialize", "notifications/initialized", "tools/call"])
        self.assertEqual(calls[-1].get_header("Mcp-session-id"), "mcp-session")
        self.assertEqual(calls[-1].get_header("Mcp-protocol-version"), "2025-11-25")

    def test_json_and_sse_notifications_multiline_crlf(self):
        wanted = {"jsonrpc": "2.0", "id": "1", "result": {"state": "ready"}}
        self.assertEqual(t.decode(json.dumps(wanted), "1"), wanted)
        stream = 'event: message\r\ndata: {"method":"notification"}\r\n\r\ndata: {"id":"1",\r\ndata: "jsonrpc":"2.0", "result":{"state":"ready"}}\r\n\r\n'
        self.assertEqual(t.decode(stream, "1"), wanted)
        with self.assertRaises(t.Failure): t.decode('data: {"id":"other"}\n\n', "1")

    def test_tool_is_error_even_with_http_success(self):
        client = object.__new__(t.MCP)
        client.rpc = lambda *a: {"isError": True, "content": [{"type": "text", "text": "Denied"}]}
        with self.assertRaises(t.Failure): client.tool("fuel", {})
        client.rpc = lambda *a: {"structuredContent": {"state": "unknown"}}
        self.assertEqual(client.tool("fuel", {}), {"state": "unknown"})


if __name__ == "__main__":
    unittest.main()
