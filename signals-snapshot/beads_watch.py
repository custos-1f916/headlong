#!/usr/bin/env python3
"""beads-watch — push new beads from the dolt tracker into custos's trajectory.

Spec agreed with custos (2026-08-29):
  scope   new beads only (created_at > cursor), no status-churn steps;
  format  [beads:<repo>] <id> <title> — status <status>  (title <= 120, total ~200)
  burst   max 5 bead lines per repo per poll + one overflow line
          `[beads:overflow] <repo> count=<n> last=<created_at>/<id>` (max 6 steps);
  filter  skip titles matching test|synthetic|fixture|dry-run|smoke|example|tmp
          (case-insensitive);
  cursor  per repo, advance ONLY after the observation lands; first run baselines
          silently (seed cursor, no observations);
  fail    no trajectory step on poll error — a local log line only.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, "/opt/custos/signals")
import watcher_common as W

STATE_FILE = "/opt/custos/signals/state/beads.json"
STATE_LOG_DIR = "/opt/custos/signals/state"
MYSQL_ARGS = ["mysql", "--defaults-extra-file=/opt/custos/signals/beads.cnf",
              "-N", "-B", "-h", "192.168.86.66"]
REPOS = ["voidle", "bonsai_game"]  # custos's tracked repos (bonsai DB is the old board)
BURST = 5
RE_SKIP = re.compile(r"\btest\b|\bsynthetic\b|\bfixture\b|dry-run|\bsmoke\b|\bexample\b|\btmp\b", re.IGNORECASE)


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"cursors": {}}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def sql(db: str, query: str) -> list:
    proc = subprocess.run(MYSQL_ARGS + [db, "-e", query],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:200])
    rows = []
    for line in proc.stdout.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        cols = line.split("\t")
        rows.append(cols)
    return rows


def fmt_observation(repo: str, bead_id: str, title: str, status: str) -> str:
    title = " ".join(title.split())[:120]
    content = f"[beads:{repo}] {bead_id} {title} — status {status}"
    return content[:200]


def poll_repo(repo: str, cursor: str) -> None:
    """Emit the new beads for one repo; returns nothing, advances the caller's
    cursor dict in place via the ref list."""
    results.append((repo, cursor))


def main() -> None:
    os.makedirs(STATE_LOG_DIR, exist_ok=True)
    state = load_state()
    cursors = state.setdefault("cursors", {})

    for repo in REPOS:
        cursor = cursors.get(repo, "")
        try:
            rows = sql(repo,
                       "SELECT id, title, status, created_at FROM issues "
                       "ORDER BY created_at, id")
        except Exception as exc:
            W.log(STATE_LOG_DIR, "beads-watch", f"{repo} poll failed: {exc}")
            continue

        pre_skip = [r for r in rows if len(r) >= 4 and (r[3] or "") > cursor]
        new_rows = [r for r in pre_skip if not RE_SKIP.search(r[1] or "")]
        skipped = [r for r in pre_skip if RE_SKIP.search(r[1] or "")]
        if skipped:
            W.log(STATE_LOG_DIR, "beads-watch",
                  f"{repo}: RE_SKIP dropped {len(skipped)}: "
                  + ", ".join(r[0] for r in skipped))
        if not new_rows:
            continue

        if not cursor:
            # first run for this repo: baseline silently — seed to the max seen
            cursors[repo] = max(r[3] for r in new_rows if r[3])
            save_state(state)
            W.log(STATE_LOG_DIR, "beads-watch", f"{repo} baselined at {cursors[repo]}")
            continue

        burst = new_rows[:BURST]
        emitted = 0
        for r in burst:
            content = fmt_observation(repo, r[0], r[1], r[2])
            if W.emit(content, "beads-signal"):
                emitted += 1
                cursors[repo] = max(cursors[repo], r[3])
                save_state(state)
            else:
                W.log(STATE_LOG_DIR, "beads-watch",
                      f"append failed for {repo}/{r[0]} — cursor not advanced")
        leftovers = len(new_rows) - len(burst)
        if leftovers > 0:
            last_r = new_rows[BURST - 1] if len(new_rows) > BURST else burst[-1]
            overflow = (f"[beads:overflow] {repo} count={len(new_rows)} "
                        f"last={last_r[3]}/{last_r[0]}")
            if W.emit(overflow, "beads-signal"):
                # overflow counts the rest as seen only up to the burst window;
                # advance the cursor to the last burst row so we keep the deltas
                cursors[repo] = max(cursors[repo], last_r[3])
                save_state(state)
        if len(new_rows) >= BURST:
            W.log(STATE_LOG_DIR, "beads-watch",
                  f"{repo}: {len(new_rows)} new, emitted {emitted} + overflow")
        else:
            W.log(STATE_LOG_DIR, "beads-watch",
                  f"{repo}: {len(new_rows)} new, emitted {emitted}")


results = []
if __name__ == "__main__":
    main()