# Incident: CUSTOS_KEY echoed into a run trajectory by the DRY fire board
Date: 2026-08-30 ~23:0xZ (contained this wake)
Class: self-inflicted secret exposure (local trajectory), contained. No POSTs made.

## What happened
While pre-staging the 00:00Z fire sequence I wrote /opt/custos/signals/fire_sequence.sh
and ran its DRY board. Step [4] printed the full curl commands with the Bearer header
EXPANDED, so the live CUSTOS_KEY (1f916_sk_..., redacted) was written into this run's
trajectory in plaintext. The free reads (/api/me, /api/post/3156) were fine; the only
problem was the key echo. The board's [0] budget gate also returned `utc_date: None`
from /api/me, so it could not actually detect the budget roll.

## Containment (done this wake)
1. fire_sequence.sh rewritten: CUSTOS_KEY is loaded from /etc/custos-systemd.env at
   runtime ONLY and the Authorization header is NEVER printed. [4] now prints a
   no-key fire plan (post_ids + frozen-file names), not expanded curls.
2. A `redact` guard (sed s/1f916_sk_[0-9A-Za-z]{12,}/1f916_sk_<REDACTED>/g) is applied
   to every API-output print, as belt-and-suspenders.
3. The [0] budget gate is now wall-clock (date -u == 2026-08-31): no API, no key,
   deterministic - fixes the utc_date=None problem.
4. No copy of the key was written to repo, mind.log, queue, or any other file.
   Verified: fire_sequence.sh no longer matches the token pattern (post-fix grep).

## Follow-up / flag to operator (hal)
- The CUSTOS_KEY is now recoverable from this machine's run trajectory. Trajectory is
  local, but the value is exposed. **Consider rotating CUSTOS_KEY.** I cannot rotate it
  (operator authority); flagging here.
- Lesson: a "DRY" board that prints fire commands must print them with the credential
  redacted, not expanded. Any script that echoes a curl with -H "Authorization: Bearer
  $K" is a leak vector - print post_ids + payload file names, never the header.
