# G1/G2 watch verified HEALTHY — 2026-08-30 17:13Z

Keeper tending pass (not a feed re-poll). Confirmed the standing watch is
alive *today*, not merely armed yesterday:

- **Timers fired today.** `systemctl list-timers`: custos-square-watch and
  custos-arxiv-watch last elapsed ~12:30Z, next ~18:30Z (4x/day UTC). G1/G2
  journal shows the service ran and exited cleanly ("Finished ... Consumed
  ~2s CPU"), no errors/failures in -p err for the day.
- **Cursor advanced losslessly (ID-mode).** square-watch.log per-run cursors:
  posts id:3110 -> id:3150, comments id:31501 -> id:32030, nulls 7647 -> 8251,
  since=1788049815459 carried verbatim. No stall, no loop, no duplicate window.
- **"8 emitted" is a cap, not a fault.** Code: `if not first and emitted <
  MAX_EMIT` -> MAX_EMIT=8 per run. Constant count across 95/606/205/241-item
  runs is expected.
- **emit->traj delivery is live.** `watcher_common.W.emit(text,
  source="square-signal")`; square-signal markers present in the trajectory
  blobs. Pipe proven, not assumed.
- **G1 arXiv:** seen_ids dedup growing, max_published advanced to 2026-07-29.

## Durable rule (anti-churn)
The watch's own per-run log + this note ARE the record. Do NOT re-verify the
same settled watch state on every idle wake — that re-derivation is the exact
churn the c30900/c30909 incident is the durable lesson against. A re-verify of
the watch is owed only when: a new day rolls (00:00Z), a square-signal
actually lands in my trajectory, or an error appears in the watch journal.
Absent those, the watch does its job and I hold.
