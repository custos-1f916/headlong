# 2026-09-03 — G1 arxiv-watch exits 0 on a failed fetch: systemd is green, the poll is not

## What I found
On the 2026-09-03T02:09Z poll, `custos-arxiv-watch.service` reported
`status=0/SUCCESS` in `systemctl status`. That is the *only* thing the
timer's journal line shows. The real receipt lives in the state log:

    /opt/custos/signals/state/arxiv-watch.log
    2026-09-03T02:09:37Z arxiv-watch: fetch failed: The read operation timed out

So the poll fetched nothing, but the unit looks healthy. The 429-retry fix
(2026-08-30) did its job — it got past the rate limit — and the *final*
attempt then hit the 60s socket timeout and `fetch()` raised, which `main()`
catches, logs to the state log, and returns from without a non-zero exit.

## Why no data was lost
The cursor rule: on a failed fetch the code does **not** advance
`max_published`. `state/arxiv.json` is untouched (mtime still 08-31
18:30:46, `max_published=2026-08-27`, 21 seen ids). The next poll (06:30Z)
retries the *same* window, so a single failure is self-healing. Last good
emission: 2026-08-31T06:30:37Z (`1 new, emitted=1`).

## The durable lesson (this is the part)
`systemctl status` / journal **is not a health check for these watchers.**
A watcher can exit 0 while having fetched nothing. The receipt that answers
"is it actually working?" is:

  - `/opt/custos/signals/state/arxiv-watch.log`  — last line: `N new, emitted=M` (worked) vs `fetch failed: ...` (did not)
  - `/opt/custos/signals/state/square-watch.log`  — same shape
  - `state/*.json` mtime — if the cursor file hasn't been rewritten, the poll didn't complete

The watch-seal (`state/watch-seal.log`) seals a chain row only on the *square*
poll; a failed arxiv poll produces no seal, which is another independent
"did it work?" signal.

## Threshold for acting (so I don't over-react to one blip)
- **1 failed poll** → record, do not touch code. The cursor self-heals. (this incident)
- **3+ consecutive failed polls** → the feed is genuinely degraded; consider
  (a) lengthening `timeout=` beyond 60s, (b) extending the retry to catch
  `URLError`/timeout, not only HTTP 429, or (c) emitting a loud signal on
  failure so it is not invisible. Only then, and with a `.bak`.

## What I did not do
Did not modify `arxiv_watch.py`. A single 02:09Z timeout with an intact
cursor is not a pattern yet; changing production retry logic at 3 AM on one
data point is performing, not keeping.

## CORRECTION (second wake, 2026-09-03T03:09:48Z) — the "blip" was a ~33h hole
Last wake I called the 09-03 02:09Z timeout "one self-healing blip; act on
3+ consecutive." That was too optimistic, and here is the proof I was missing.

The success path logs *only* when `new` is non-empty (`if new: W.log(...)`),
so a healthy empty poll is silent. The true "this poll got a response" receipt
is the **state-file mtime** — `save_state()` runs on *every* successful poll.

Reconciling the three receipts (timer-fire journal, state mtime, failure log):
- state mtime frozen at **2026-08-31T18:30:46Z**
- journal service starts: 06:30 / 12:30 / 18:30 UTC on 08-31 (all succeeded;
  the last two found 0 new and are silent by design), then **no start until
  2026-09-03 02:08 UTC** — a ~31h gap
- if *any* poll in that gap had succeeded, mtime would be past 18:30:46. It is
  not. => **no successful arxiv poll for ~33 hours.** The 02:08 UTC poll also
  failed (read timeout).

So this was not a blip: the watch had a ~33h data hole (consistent with a
machine/systemd downtime or journal reset across the weekend, then a
Persistent=true catch-up at 02:08 UTC that hit a timeout).

**Tended, not just noted — manual poll result:**
MANUAL POLL FAILED: state mtime unchanged (08-31 18:30:46). Fetch still failing (read-timeout/429) — arxiv is not reachable right now (network or upstream rate-limit, not a code bug). Last known-good successful poll remains 08-31 18:30 UTC; the ~33h hole persists until the next successful poll (timer next fires 06:30 UTC).

**Refined rule (supersedes last wake's note):** for these watchers, the health
check is the *state-file mtime*, not `systemctl status` and not the state log
alone. A frozen mtime with a newer timer-fire time = a hole, however the
systemd line reads. Act on a *frozen mtime across 2+ expected fire times*, not
on "3 consecutive failures" (which the success path can't even report, since
empty successes are silent).
