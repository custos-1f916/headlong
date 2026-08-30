# beads-watch: RE_SKIP substring bug silently drops "Playtest"-tagged beads

**Date:** 2026-08-30 18:53Z
**Severity:** Low (one bead delayed ~18h; no data lost, no false signals emitted)
**Status:** FIXED + RECOVERED

## What happened

beads-watch fired 18:50Z (systemd timer, 4x/day). Exited 0/SUCCESS in 40ms,
produced no log output, no trajectory step, no state change. Initially looked
like a clean "no new beads" poll.

Investigation revealed: bead `vd-p910` — *"[Playtest] 4th Class Special Mastery
notable pays out 2 of its advertised 16 charge units"* — was created at
2026-08-30 17:14:55 (after the cursor) but silently dropped by `RE_SKIP`.

## Root cause

`RE_SKIP = re.compile(r"test|synthetic|fixture|dry-run|smoke|example|tmp", re.IGNORECASE)`

Uses bare substring matching. "Play**test**" matches "test" → the bead was
filtered with no log line (the `if not new_rows: continue` path is silent by
design). The spec says "skip titles matching test|synthetic|..." — the intent
is to skip synthetic/test fixtures, not legitimate game-design playtest notes.

## Fix

Word-boundary matching for the bare words (dry-run kept as hyphenated phrase):

```python
RE_SKIP = re.compile(r"\btest\b|\bsynthetic\b|\bfixture\b|dry-run|\bsmoke\b|\bexample\b|\btmp\b", re.IGNORECASE)
```

Verified: "Playtest" → KEEP, "test bead" → SKIP, "latest" → KEEP,
"fixture: dry-run" → SKIP.

## Recovery

Ran `beads_watch.py` manually at 18:53Z. The previously-missed vd-p910 was
emitted as a trajectory observation (`source=beads-signal`). Cursor advanced
from `2026-08-26 20:27:50` → `2026-08-30 17:14:55`. Log:
`2026-08-30T18:53:01Z beads-watch: voidle: 1 new, emitted 1`

## Aug 29 "append failed" beads (vd-0k28, bonsai-0jd)

Those were a separate issue (traj not on PATH in the systemd unit's env),
fixed the same day by the `beads-watch-fix.patch` (full path to traj). The
beads themselves are no longer queryable (not in the issues table) — likely
resolved/closed and compacted. No action needed.

## Durable lesson

- Substring regex filters are silent killers in monitoring pipelines. A bead
  is *data*; dropping it with no log line means the operator (me) sees a
  "clean fire" when actually a signal was lost. If a filter drops a row, it
  should log at least a count: `"{repo}: {n} skipped by RE_SKIP"`.
- Word boundaries are the minimum fix. The deeper fix (logging drops) is
  deferred — the 4x/day cadence means a single dropped bead is at most ~6h
  late, which is acceptable for the current signal budget.
