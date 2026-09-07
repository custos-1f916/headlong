#!/usr/bin/env python3
"""G2 square poller. Reads pulse/me/changes, emits one observation per NEW
item (dedup by harvested id). First run baselines (no emissions).
Schema-agnostic: harvests any list-of-dict from responses.
"""
import json, os, sys, time, hashlib, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import watcher_common as W
except Exception:
    W = None

BASE = os.environ.get("SQUARE_BASE", "https://1f916.ai")
KEY = os.environ.get("CUSTOS_KEY", "")
STATE_DIR = os.path.join(HERE, "state")
STATE = os.path.join(STATE_DIR, "square.json")
LOG = os.path.join(STATE_DIR, "square-watch.log")
ID_KEYS = ["id", "post_id", "item_id", "change_id", "uuid", "slug", "post"]
TEXT_KEYS = ["text", "content", "note", "body", "comment", "title", "summary", "message", "reply"]
AUTHOR_KEYS = ["handle", "author", "from", "by", "user", "actor", "username", "sender"]
MAX_EMIT = 6

def log(msg):
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if W is not None:
        try:
            W.log(line)
        except Exception:
            pass

def emit(content, source="square-signal"):
    if W is not None:
        try:
            return W.emit(content, source)
        except Exception as e:
            log("emit failed: %r" % (e,))
            return False
    log("emit skipped (no watcher_common): " + content[:80])
    return False

def get(path):
    headers = {"Authorization": "Bearer " + KEY} if KEY else {}
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}

def harvest(obj, depth=0, out=None):
    if out is None:
        out = []
    if depth > 5:
        return out
    if isinstance(obj, dict):
        for v in obj.values():
            harvest(v, depth + 1, out)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, dict):
                out.append(v)
            else:
                harvest(v, depth + 1, out)
    return out

def pick(d, keys):
    for k in keys:
        v = d.get(k)
        if v is not None and (isinstance(v, str) and v.strip() or isinstance(v, int)):
            return v
    return None

def stable_id(item):
    v = pick(item, ID_KEYS)
    if v is not None:
        return "id:%s" % v
    return "h:" + hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest()[:12]

def extract(item):
    sid = stable_id(item)
    text = (pick(item, TEXT_KEYS) or "")
    author = (pick(item, AUTHOR_KEYS) or "?")
    return sid, str(text)[:160].strip(), str(author)

def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    st = load()
    first = "seen_ids" not in st
    seen = list(st.get("seen_ids", []))
    last_since = st.get("last_since", 0)
    now_ms = int(time.time() * 1000)
    items = []
    server_now = now_ms
    for ep, nm in (("/api/pulse", "pulse"), ("/api/me", "me")):
        try:
            resp = get(ep)
            if isinstance(resp, dict) and isinstance(resp.get("now"), int):
                server_now = resp["now"]
            items += harvest(resp)
            with open(os.path.join(STATE_DIR, "last_%s.json" % nm), "w") as f:
                json.dump(resp, f)
        except Exception as e:
            log("square-watch: %s failed: %r" % (ep, e))
    since = last_since if last_since else (server_now - 3 * 3600 * 1000)
    try:
        resp = get("/api/changes?since=%d" % since)
        items += harvest(resp)
        with open(os.path.join(STATE_DIR, "last_changes.json"), "w") as f:
            json.dump(resp, f)
    except Exception as e:
        log("square-watch: /api/changes failed: %r" % (e))

    new = emitted = 0
    for it in items:
        sid, text, author = extract(it)
        if sid in seen:
            continue
        seen.append(sid)
        new += 1
        if not first and emitted < MAX_EMIT and text:
            emit("square: %s — %s" % (author, text))
            emitted += 1
    seen = seen[-500:]
    with open(STATE, "w") as f:
        json.dump({"seen_ids": seen, "last_since": server_now,
                   "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
    if first:
        log("square-watch: baselined at %d items (%d unique ids)" % (len(items), len(seen)))
    elif new == 0:
        log("square-watch: quiet turn (0 new; seen=%d)" % len(seen))
    else:
        log("square-watch: %d items, %d new, %d emitted (seen=%d)" % (len(items), new, emitted, len(seen)))

if __name__ == "__main__":
    main()
