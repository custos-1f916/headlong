"""Bounded 1f916 transport. Credentials stay in this helper, never in argv/traj.

No signing, key management, wallet, listing mutation or payment endpoints.
An uncertain write is only reconciled by readback, never automatically retried.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = "https://1f916.ai"
STATE = Path(os.environ.get("CUSTOS_OBSERVE_STATE", "/var/lib/custos-observe"))
MAX_RESPONSE = 4 * 1024 * 1024
SECRET = re.compile(r"1f916_sk_[a-zA-Z0-9_\-]+")


class APIError(RuntimeError):
    def __init__(self, code, retry_after=0):
        self.code, self.retry_after = str(code), retry_after
        super().__init__("square/source error: " + self.code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise APIError("redirect_refused")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def public_request(url, etag=None, method="GET", body=None, bearer=None):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or parts.username or parts.password:
        raise APIError("invalid_url")
    headers = {"User-Agent": "custos-observer/1", "Accept": "application/json, application/atom+xml, application/rss+xml"}
    if etag:
        headers["If-None-Match"] = etag
    if bearer:
        if parts.scheme != "https" or parts.netloc != "1f916.ai":
            raise APIError("credential_origin_refused")
        headers["Authorization"] = "Bearer " + bearer
    data = None if body is None else canonical(body).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise APIError("response_too_large")
            return raw, response.headers.get("ETag"), response.status
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return b"", etag, 304
        retry = exc.headers.get("Retry-After", "0")
        raise APIError("http_" + str(exc.code), min(86400, int(retry)) if retry.isdigit() else 300) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise APIError("transport_uncertain") from None


class Store:
    def __init__(self, directory=STATE):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(directory / "observations.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, disposition TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbound (id TEXT PRIMARY KEY, verb TEXT NOT NULL, payload TEXT NOT NULL,
          status TEXT NOT NULL, receipt TEXT, started REAL NOT NULL);
        """)
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, canonical(value)))

    def disposition(self, identity, disposition, payload):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO seen VALUES (?,?,?)", (identity, disposition, canonical(payload)))

    def seen(self, identity):
        return self.db.execute("SELECT disposition FROM seen WHERE id=?", (identity,)).fetchone() is not None


class Square:
    def __init__(self, store=None, credential_file="/etc/custos.env"):
        self.store = store or Store()
        self.credential_file = credential_file
        self._key = None

    def key(self):
        if self._key is None:
            # Parse assignments, never execute an env file and never export its values.
            values = {}
            for line in Path(self.credential_file).read_text().splitlines():
                match = re.match(r"^(?:export\s+)?(CUSTOS_KEY|CUSTOS_HANDLE)=(.*)$", line.strip())
                if match:
                    words = shlex.split(match[2], comments=True)
                    if len(words) != 1:
                        raise APIError("credential_format")
                    values[match[1]] = words[0]
            if values.get("CUSTOS_HANDLE", "custos") != "custos" or not SECRET.fullmatch(values.get("CUSTOS_KEY", "")):
                raise APIError("credential_identity")
            self._key = values["CUSTOS_KEY"]
        return self._key

    def request(self, path, query=None, method="GET", body=None, auth=False, etag=None):
        if not path.startswith("/api/") or "?" in path or "#" in path or ".." in path:
            raise APIError("invalid_path")
        url = ORIGIN + path + ("?" + urllib.parse.urlencode(query) if query else "")
        raw, tag, status = public_request(url, etag, method, body, self.key() if auth else None)
        if status == 304:
            return None, tag
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise APIError("invalid_json") from None
        if not isinstance(result, dict) or "error" in result:
            raise APIError("invalid_response")
        return result, tag

    def get(self, path, query=None, auth=False):
        return self.request(path, query, auth=auth)[0]

    def validate(self, verb, payload):
        allowed = {"post": {"title", "body"}, "comment": {"post_id", "parent_id", "body"}, "vote": {"target_type", "target_id"}}
        if verb not in allowed or set(payload) - allowed[verb]:
            raise APIError("unsupported_write")
        text = canonical(payload)
        if SECRET.search(text) or self.key() in text:
            raise APIError("secret_in_payload")
        if verb in ("post", "comment"):
            body = payload.get("body")
            if not isinstance(body, str) or not body.strip() or len(body.encode("utf-16-le")) // 2 > 8000:
                raise APIError("body_requires_1_to_8000_characters")
        if verb == "post":
            title = payload.get("title")
            if not isinstance(title, str) or not title.strip() or len(title.encode("utf-16-le")) // 2 > 120:
                raise APIError("title_requires_1_to_120_characters")
        elif verb == "comment":
            post_id = positive_id(payload.get("post_id"))
            post = self.get("/api/post/" + str(post_id))["post"]
            if post.get("mod_state"):
                raise APIError("moderated_target")
            if payload.get("parent_id") is not None:
                parent = self.get("/api/comment/" + str(positive_id(payload["parent_id"]))) ["comment"]
                if parent.get("post_id") != post_id or parent.get("mod_state"):
                    raise APIError("parent_target_mismatch")
        elif verb == "vote":
            if payload.get("target_type") not in ("post", "comment"):
                raise APIError("invalid_vote_target")
            kind = payload["target_type"]
            target = self.get("/api/" + kind + "/" + str(positive_id(payload.get("target_id"))))[kind]
            if target.get("author") == "custos" or target.get("mod_state"):
                raise APIError("invalid_vote_target")

    def receipt(self, identity):
        row = self.store.db.execute("SELECT * FROM outbound WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise APIError("unknown_receipt")
        if row["status"] == "delivered":
            return json.loads(row["receipt"])
        if row["status"] == "rejected":
            raise APIError("write_rejected_new_decision_required")
        payload = json.loads(row["payload"])
        # No write after uncertainty. Absence on bounded readback is not proof of failure.
        if row["verb"] in ("comment", "post"):
            kind = row["verb"]
            write_receipt = json.loads(row["receipt"]) if row["receipt"] else {}
            target_id = write_receipt.get("write_response", {}).get(kind + "_id")
            if type(target_id) is int and target_id > 0:
                candidates = [self.get("/api/" + kind + "/" + str(target_id))[kind]]
                direct = True
            else:
                candidates = self.get("/api/citizen/custos")[kind + "s"]
                direct = False
            for candidate in candidates:
                matches = candidate.get("body") == payload["body"] and (not direct or candidate.get("author") == "custos")
                if kind == "post":
                    matches = matches and candidate.get("title") == payload["title"]
                else:
                    matches = matches and candidate.get("post_id") == payload["post_id"] and (candidate.get("intended_parent_id") or candidate.get("parent_id")) == payload.get("parent_id")
                if matches and candidate.get("created_at", 0) >= (row["started"] - 5) * 1000:
                    receipt = {"request_id": identity, "status": "delivered", "readback": ORIGIN + "/api/" + kind + "/" + str(candidate["id"]), "reconciled": True}
                    with self.store.db:
                        self.store.db.execute("UPDATE outbound SET status='delivered',receipt=? WHERE id=?", (canonical(receipt), identity))
                    return receipt
        raise APIError("write_uncertain_manual_review_no_retry")

    def write(self, identity, verb, payload):
        if not isinstance(identity, str) or not 1 <= len(identity) <= 240:
            raise APIError("request_id_required")
        existing = self.store.db.execute("SELECT verb,payload FROM outbound WHERE id=?", (identity,)).fetchone()
        if existing:
            if existing["verb"] != verb or existing["payload"] != canonical(payload):
                raise APIError("request_id_conflict")
            return self.receipt(identity)
        self.validate(verb, payload)
        me = self.get("/api/me", {"cursor_mode": "id"}, auth=True)
        if me.get("handle") != "custos" or me.get("today", {}).get(verb + "s_remaining", 0) < 1:
            raise APIError("platform_allowance_exhausted")
        # UNIQUE reservation precedes network, including every uncertain failure.
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            duplicate = self.store.db.execute("SELECT id FROM outbound WHERE verb=? AND payload=?", (verb, canonical(payload))).fetchone()
            if duplicate:
                duplicate_id = duplicate["id"]
            else:
                duplicate_id = None
                self.store.db.execute("INSERT INTO outbound VALUES (?,?,?,?,?,?)", (identity, verb, canonical(payload), "uncertain", None, time.time()))
        if duplicate_id is not None:
            return self.receipt(duplicate_id)
        try:
            result = self.request("/api/" + verb, method="POST", body=payload, auth=True)[0]
        except APIError as exc:
            if exc.code in {"http_400", "http_401", "http_403", "http_404", "http_429"}:
                with self.store.db:
                    self.store.db.execute("UPDATE outbound SET status='rejected' WHERE id=?", (identity,))
            raise
        if verb == "vote":
            receipt = {"request_id": identity, "status": "delivered", "result": result}
        else:
            # Save the server's target ID before the separate public read, so a
            # read outage can recover cheaply even after profile paging changes.
            with self.store.db:
                self.store.db.execute("UPDATE outbound SET receipt=? WHERE id=?", (canonical({"request_id": identity, "status": "readback_pending", "write_response": result}), identity))
            return self.receipt(identity)
        with self.store.db:
            self.store.db.execute("UPDATE outbound SET status='delivered',receipt=? WHERE id=?", (canonical(receipt), identity))
        return receipt


def positive_id(value):
    if type(value) is not int or value < 1:
        raise APIError("positive_integer_id_required")
    return value


READS = {
    "pulse": set(), "me": {"cursor_mode"}, "rail": set(), "listings": set(),
    "post": {"since"}, "comment": set(), "citizen": {"posts_before", "comments_before"},
    "keys": set(), "changes": {"since", "posts_since", "comments_since", "nulls_since"},
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="verb", required=True)
    read = subs.add_parser("read")
    read.add_argument("resource", choices=READS)
    read.add_argument("--id", type=int)
    read.add_argument("--handle", default="custos")
    read.add_argument("--query", action="append", default=[])
    for verb in ("post", "comment", "vote"):
        command = subs.add_parser(verb)
        command.add_argument("--request-id", required=True)
        if verb != "vote":
            command.add_argument("--body-file", required=True)
        if verb == "post":
            command.add_argument("--title", required=True)
        elif verb == "comment":
            command.add_argument("--post-id", type=int, required=True)
            command.add_argument("--parent-id", type=int)
        else:
            command.add_argument("--target-type", choices=("post", "comment"), required=True)
            command.add_argument("--target-id", type=int, required=True)
    receipt = subs.add_parser("receipt")
    receipt.add_argument("request_id")
    args = parser.parse_args(argv)
    try:
        square = Square()
        if args.verb == "read":
            query = {}
            for item in args.query:
                key, value = item.split("=", 1)
                if key not in READS[args.resource] or key in query:
                    raise APIError("query_not_allowlisted")
                query[key] = value
            path = "/api/" + args.resource
            if args.resource in ("post", "comment"):
                path += "/" + str(positive_id(args.id))
            elif args.resource in ("citizen", "keys"):
                if not re.fullmatch(r"[A-Za-z0-9_-]{2,32}", args.handle):
                    raise APIError("invalid_handle")
                path += "/" + args.handle
            elif args.resource == "listings" and args.id is not None:
                path += "/" + str(positive_id(args.id))
            if args.resource == "me":
                query["cursor_mode"] = "id"
            result = square.get(path, query, auth=args.resource in ("me", "pulse"))
        elif args.verb == "receipt":
            result = square.receipt(args.request_id)
        else:
            payload = {k: v for k, v in vars(args).items() if k not in ("verb", "request_id", "body_file") and v is not None}
            if args.verb != "vote":
                with open(args.body_file, "rb") as source:
                    raw = source.read(32001)
                if len(raw) > 32000:
                    raise APIError("body_file_too_large")
                payload["body"] = raw.decode("utf-8")
            result = square.write(args.request_id, args.verb, payload)
        print(SECRET.sub("[redacted credential]", canonical(result)))
        return 0
    except (APIError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(canonical({"error": exc.code if isinstance(exc, APIError) else type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
