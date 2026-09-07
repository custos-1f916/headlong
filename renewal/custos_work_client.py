#!/usr/bin/env python3
"""JSON-only guest interface to the outside-guest Voidle broker.

Reads: custos-work ready [--limit 20]; custos-work show vd-ID
Writes: custos-work create <request.json
        custos-work {claim,comment,release,close} vd-ID <request.json
Every write requires a caller-chosen stable request_id and native goal_id.
On timeout/pending, reuse the exact original request, never generate a new ID.
No retry is automatic. Exit 0 = success; 1 = broker rejection; 2 = local/transport
failure (write outcome may be uncertain). --json is accepted; all output is JSON.
"""
import argparse
import http.client
import json
import sys

HOST = "192.168.86.44"
PORT = 18081
MAX_INPUT = 32768
MAX_OUTPUT = 1048576
WRITES = ("create", "claim", "comment", "release", "close")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ready", "show", *WRITES))
    parser.add_argument("issue_id", nargs="?")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    request_id = None
    connection = None
    try:
        requires_issue = args.action not in ("ready", "create")
        if bool(args.issue_id) != requires_issue:
            raise ValueError("This action requires exactly one issue ID" if requires_issue else "This action does not accept an issue ID")
        if args.limit is not None and args.action != "ready":
            raise ValueError("--limit is only valid with ready")
        payload = {}
        if args.action in WRITES:
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(raw) > MAX_INPUT:
                raise ValueError("Input exceeds 32768 bytes")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Input must be a JSON object")
            request_id = payload.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("Every write needs a stable request_id")
            if "action" in payload or "issue_id" in payload:
                raise ValueError("Specify action and issue_id on the command line, not in input JSON")
        payload["action"] = args.action
        if args.issue_id:
            payload["issue_id"] = args.issue_id
        if args.limit is not None:
            payload["limit"] = args.limit
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > MAX_INPUT:
            raise ValueError("Encoded request exceeds 32768 bytes")
        # Fixed direct connection: no environment proxy, redirects, credentials,
        # arbitrary endpoint flags, or guest-side bd invocation.
        connection = http.client.HTTPConnection(HOST, PORT, timeout=180)
        connection.request("POST", "/v1/work", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_OUTPUT + 1)
        if len(raw) > MAX_OUTPUT:
            raise ValueError("Broker response exceeded limit")
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ValueError("Invalid broker response")
        if request_id:
            result.setdefault("request_id", request_id)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if response.status == 200 and result["ok"] else 1
    except (ValueError, OSError, http.client.HTTPException, RecursionError):
        result = {"ok": False, "error": "client_failure", "message": "Invalid input or unavailable broker. A submitted write may be pending; reuse the exact request_id and payload"}
        if isinstance(request_id, str):
            result["request_id"] = request_id[:128]
        print(json.dumps(result, separators=(",", ":")))
        return 2
    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())
