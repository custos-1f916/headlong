# Night-cluster timezone — RESOLVED finding (18:43Z)

Surfaced by a machinery (producer-side) check, not a settled-state re-read.
Verdict up front: **cosmetic, not functional.** The budget that governs
posting is server-side at 00:00 UTC; the local night cluster fires at 06:00 UTC
by design and only anchors/houses books, so no posting-boundary is wrong.

## Facts (evidence)
- System TZ: **America/Denver (MDT, UTC-6)** — /etc/timezone + /etc/localtime.
- Seal purpose (unit Description): "G3 seal: hash books into the public chain
  (closing watch)". Runs /opt/custos/scripts/seal_books.sh.
- Fire times:
    seal.timer          OnCalendar=*-*-* 00:00:00          (NO tz -> system TZ -> 00:00 MDT = 06:00 UTC)
    dream.timer         OnCalendar=*-*-* 00:00:00 America/Denver  (-> 06:00 UTC)
    nightly-pass.timer  OnCalendar=*-*-* 00:05:00 America/Denver  (-> 06:05 UTC)
    arxiv-watch.timer   00:30/06:30/12:30/18:30 UTC        (signal producers — correct)
    square-watch.timer  00:30/06:30/12:30/18:30 UTC        (signal producers — correct)
- seal_books.sh timestamps with `date -u` and POSTs /api/seal + /api/diary.
  It hashes the **cumulative book point-in-time**; it does NOT reset the budget.
- Budget boundary is **server-side 00:00 UTC** (08-26 journal: "reset is
  00:00Z, local 18:00"). Independent of the local seal, which fires 6h after.

## Why it is not a functional bug
The seal at 06:00 UTC anchors "the book as of 06:00 UTC" — a cumulative snapshot
that already includes the prior UTC day's entries. A point-in-time chain anchor
does not need to align to the budget boundary; the budget is what gates posting
and it is server-controlled. No data loss, no wrong boundary, no missed signal.

## Residuals (operator calls, not changed by me)
1. **Label correction:** prior notes called this "the 00:00Z seal". It is in fact
   **00:00 MDT = 06:00 UTC**. Forward-facing references should use 06:00 UTC.
2. **Robustness nit:** seal.timer is the ONLY night-cluster unit lacking an
   explicit timezone — dream/nightly-pass pin America/Denver, seal tracks system
   TZ implicitly. If the machine TZ ever changes, seal silently drifts. Low risk,
   but a one-line hardening (add " America/Denver") if the operator wants it.
   NOT applied here — config change 10h before fire is an operator decision.

## Disposition
- Nothing to fix tonight; no timer touched.
- At 06:00 UTC the seal/dream/nightly-pass will fire and anchor/house books as
  designed; at 00:00 UTC the budget rolls server-side and the pre-staged walter
  draft (queue/2026-08-31_reset_0000Z_walter_draft.md) becomes postable.
- If the operator wants the night cluster on the UTC boundary, that is a
  deliberate timer edit + a note; flag then, not churned now.
