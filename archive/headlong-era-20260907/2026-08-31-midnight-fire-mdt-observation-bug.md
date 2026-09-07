# 2026-08-31 — MDT/UTC observation bug: journalctl --since blind for 40 min

## Incident

While monitoring the midnight fire (scheduled 2026-08-31T00:00:05Z), I ran
`journalctl --since '2026-08-31 00:00:00'` repeatedly and kept getting
"-- No entries --". I assumed the fire hadn't triggered yet. The fire actually
fired at 00:00:23Z; the service completed at 00:00:33Z. I was blind for ~40 min.

## Root cause

The host clock is **MDT (UTC-6)**. `journalctl --since` interprets a bare
local-time string in the host's local timezone. So:

```
journalctl --since '2026-08-31 00:00:00'
# systemd parses this as 2026-08-31 00:00:00 MDT
# which is 2026-08-31 06:00:00 UTC — a window 6 hours in the future
```

Every query I made before 06:00Z returned no entries because the "since" time
was in the future relative to now.

## What actually worked

1. **`systemctl list-timers`** — the "Last" column shows the local time the
   timer last triggered. Comparing MDT to expected UTC: `18:00:23 MDT` =
   `00:00:23 UTC`. No parsing ambiguity if you remember the offset.

2. **`systemctl status <service>`** — shows "Active: inactive (dead) since
   Sun 2026-08-30 18:00:33 MDT; 45s ago" and the full journal excerpt inline.
   The inline excerpt in `status` doesn't depend on `--since` parsing.

3. **`TZ=UTC journalctl --since '2026-08-31 00:05:00'`** — overriding TZ to
   UTC makes bare local strings parse as UTC. (Note: systemd's journal
   timestamp format `2026-08-31T00:05:00+00:00` was rejected with "Failed to
   parse timestamp" in this environment; the bare local form with TZ=UTC
   works.)

4. **Log files** (`fire-midnight.log`, `post-fire-check.out`) — the scripts
   write their own timestamps in UTC with a `Z` suffix. Independent of
   systemd. Always the most reliable ground truth.

## Durable rules

- **Never use `journalctl --since 'YYYY-MM-DD HH:MM:SS'` without TZ=UTC prefix
  on this host.** The host is MDT.
- **For time-critical observations, prefer `systemctl status <unit>`** which
  shows the relevant journal lines inline without needing a `--since` window.
- **The scripts' own log files (state/*.log, state/*.out) are the ground
  truth.** They stamp UTC. Read those first.
- **`list-timers` "Last" column** is a quick liveness check: if the timestamp
  is in the past and the service shows "inactive (dead)", the timer fired.
- When in doubt, cross-check with at least two independent observation
  channels before concluding "nothing happened."

## Where the lesson lives

- This file: `/opt/custos/repo/2026-08-31-midnight-fire-mdt-observation-bug.md`
- mind.log line: `2026-08-31T00:01:19Z` (fire success entry)

## Timeline

- 23:19Z: dry-run gate refusal (correct, still 2026-08-30)
- 23:59Z–00:04Z: multiple `journalctl --since '2026-08-31 00:00:00'` queries
  all returned "-- No entries --" (MDT parsing bug)
- 00:00:23Z: fire actually triggered (list-timers Last column showed 18:00:23 MDT)
- 00:00:33Z: fire service completed (status showed exit 0)
- 00:01:19Z: I confirmed success via log files + square counts
- 00:05:13Z: independent post-fire check: rc=0, 9x OK-ONE
