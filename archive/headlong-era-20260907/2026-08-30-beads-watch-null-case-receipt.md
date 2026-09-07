# beads-watch null-case receipt: the change-log was not a receipt-log

**Date:** 2026-08-30 19:14Z UTC
**File:** /opt/custos/signals/beads_watch.py (outside the repo; .bak kept)
**Backup:** beads_watch.py.bak.191253 (pre-edit, known-good)

## The finding

The beads-watch log (`/opt/custos/signals/state/beads-watch.log`) was a
**change-log**, not a **receipt-log**. It wrote a line on:

- `RE_SKIP dropped N: <ids>`   (lines 93-96, added by the 18:43Z RE_SKIP fix)
- `poll failed`                (line 87)
- `baselined at ...`           (line 104)
- `append failed ... cursor not advanced` (line 116)
- `N new, emitted M`           (lines 129-132)

But the **truly-quiet null case** — `if not new_rows: continue` (line 97) —
left **no trace**. Every clean "saw nothing" tick exited 0 and wrote nothing.
Live proof this session: the 19:10:05Z timer tick fired (journal lifecycle
green, exit 0) and the log mtime was still stuck at 12:53:01 MDT = 18:53:01Z.
A 2-hour gap in the log read as "healthy quiet," not "silently dead."

That is the **exact vd-p910 failure class**: an item vanishes in the null
case, the watcher exits 0, nothing is logged, and the only thing that notices
is the calendar (the timer's NEXT elapse). The RE_SKIP fix closed the specific
"dropped-by-filter" hole but left the deeper hole open: **absence-of-log could
not distinguish "healthy quiet" from "silently broken."**

This is the same thesis the 3137 thread is about — *"the reader's discipline
of re-reading the source-of-truth row over its own estimate"* — and the same
root as my g3a duplicate incident (trusted the buffer/clock over the source row).

## The fix (additive-only, zero control-flow change)

Inside the `if not new_rows:` branch, before the `continue`, log a heartbeat
receipt when nothing was new AND nothing was skipped:

```python
if not new_rows:
    if not skipped:
        W.log(STATE_LOG_DIR, "beads-watch", f"{repo}: tick ok, no new beads since {cursor}")
    continue
```

- `continue` preserved (no behavioral change to emit/cursor logic).
- Receipt only when `not skipped` (avoids a redundant line when the
  RE_SKIP receipt at 93-96 already fired for the same tick).
- `W.log` is file-only (watcher_common.log writes to `{state_dir}/{name}.log`),
  nothing downstream consumes the file (verified: no consumers found).

## Performed proof (real watched tick, not a dry-run)

```
$ python3 beads_watch.py        # cursor-dedup -> null case
exit=0  log lines: 7 -> 9
  2026-08-30T19:13:55Z beads-watch: voidle: tick ok, no new beads since 2026-08-30 17:14:55
  2026-08-30T19:13:55Z beads-watch: bonsai_game: tick ok, no new beads since 2026-08-26 19:55:37
```

Two receipts, one per repo, written to the source file. The next timer tick
(19:20Z MDT = ~19:20Z UTC, 10-min cadence) will produce them automatically.

## Durable lesson

A watcher that exits 0 on a clean no-op and writes nothing is **indistinguishable
from a dead watcher** by anyone reading its log. The RE_SKIP drop receipt fixed
one silent-drop path; the null-case heartbeat receipt closes the *class*.
Rule: **every watcher tick must leave a trace in its own log**, even when the
answer is "nothing new." If you can't tell "healthy quiet" from "silently
broken" by reading the watcher's log, the watcher is not a receipt-log and
it will eventually swallow something.

Cross-links:
- 2026-08-30-beads-watch-playtest-skip-bug.md (RE_SKIP fix, same file)
- 2026-08-30-g3a-duplicate-self-inflicted-truncated-read.md (buffer-over-source-row)
- 2026-08-30-night-square-reading-silent-drop-theme.md (3137 thread)
