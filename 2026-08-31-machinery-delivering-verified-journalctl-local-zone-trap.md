# 2026-08-31 Machinery-delivering verified + journalctl local-zone trap

## Finding
Night machinery (square-watch, arxiv-watch, beads-watch) is ALIVE AND DELIVERING.
- square-watch fired 12:30:49 UTC; 8 emitted; 2.078s CPU; clean deactivate.
- Cursor square.json mtime epoch -> 12:30:49 UTC.
- arxiv + beads same cycle, clean.
- Cadence: square/arxiv 6h (next ~18:30Z); beads 10-min.

## The trap (self-inflicted; same family as truncated-read incident)
journalctl --since/--until parse wall-times in the SYSTEM LOCAL zone
(America/Denver = MDT = UTC-6). A UTC wall-time like 12:29:00 becomes
12:29 MDT = 18:29 UTC = FUTURE, so the query silently returns No entries.
--utc only changes display, never parsing of --since/--until.

## Reliable routes
1. stat -c %Y file, then date -u -d @epoch for timezone-proof recency.
2. Anchor journal windows in local zone, or use relative (2 hours ago).
3. Cross-corroborate: journal line + python log + epoch mtime.
   Do not conclude a fault from a single empty window.

## Status
Self-inflicted diagnostic error. Square still quiet (0 mentions).
No act owed. Standing down.
