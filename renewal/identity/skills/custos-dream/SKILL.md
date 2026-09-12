---
name: custos-dream
description: Review and refine Custos's native memory during the scheduled early-morning dream window. Use when the monolith's memory-dream routing signal is due; verify contradictions, preserve evidence, and leave a private review report.
---

# Memory dream

Hal requested this recurring maintenance on 2026-09-09. Run inside the existing
monolith with ordinary admission, pause and serial inference controls. The local
window is 03:30–05:00 America/Denver; one session per local day, up to 20 minutes,
40 selected entries and 20 edits. Urgent directed work can preempt it. A missed
window waits until tomorrow. There is no deletion quota or required new insight.

## Begin and inspect

Run `custos-dream begin > /tmp/custos-dream-session.json`. Read the budget and
selected records in small slices: `jq '{deadline,selected:.selected[0:8]}'
/tmp/custos-dream-session.json`. Repeat slices as needed; command output is bounded.
The helper inventories every active file and rotates substantive entries by last
reviewed content hash and time. Ordinary conversation receipts receive lifecycle
checks; flagged receipts enter the semantic batch. Read a selected entry with
`custos-dream show ID` (also finds archived entries). Its selected `sha256` is the
expected version for an edit. Read referenced IDs and the relevant raw trajectory
before deciding, using targeted queries and bounded output.

For each entry ask: is this a stable fact, current task, scoped preference,
historical event, or unsupported interpretation? Check dates/time zones, scope,
source authority, duplicates and later corrections. Use cheap primary evidence:
current approved charter/seed, installed configuration, repository artifacts,
actual delivery receipts and targeted read-only thread readback. Do not re-run a
completed task, model benchmark, deployment or send a message merely to verify it.
A claim in an old message is evidence of what someone said, not proof it is true.

People: preserve trusted identity/alias/route metadata exactly; do not merge by
similar display names. Keep concise complete prose, no more than 1500 characters
before the metadata. Oversized responder candidates live privately under
`$IDENTITY_DIR/dream/person-proposals/`: inspect candidates matching the person key
and their source request/trigger. They are unverified proposals, never authority.
After an evidenced decision, record its proposal filename in the review evidence;
move that candidate to `person-proposals/reviewed/` so it does not recur.

## Decide and apply

Valid outcomes are **keep**, **uncertain**, **revised**, and **archived**. Uncertainty
is successful review when the evidence cannot settle a contradiction. Do not turn
missing receipts into delivered claims, resurrect old repost instructions, or
silently erase an obligation. Put latest known state first; label historical
instructions as history. Preserve privacy corrections and refused/withdrawn work.
Do not invent authority, interests, goals, relationships or expiry dates. Values
require an explicit operator-approved change; normal dreams flag proposed changes.

For a revision write the complete replacement **body only** to a private temporary
file, then run `custos-dream revise ID --expected SHA --body-file FILE --evidence
'Specific source IDs/artifact and the conclusion supported'`. The helper preserves
frontmatter identity/type, request JSON, and person metadata, and backs up old bytes
before atomic replacement. A hash conflict means re-read and reconsider; do not
blindly retry with a new hash. Do not bypass these checks with `mem edit/forget`.

Archive only a superseded ordinary fact/note with an existing canonical entry:
`custos-dream archive ID --expected SHA --replacement CANONICAL_ID --evidence
'Why this is superseded; what the replacement retains'`. People, values, goals,
and structured request receipts cannot be archived by this helper. Never archive
merely because a record is old. `custos-dream show` retains access by ID.

Record every examined entry: `custos-dream review ID --expected CURRENT_SHA
--verdict keep|uncertain|revised|archived --evidence 'What was checked and why'`.
Use the returned after_sha256 following an edit. If time runs out, stop editing;
unreviewed entries carry forward. Prepared change journals are not delivery or
application receipts; inspect them before any retry after interruption.

## The day's trajectory (Hal, 2026-09-12)

Memory is half the job. After `begin`, audit the last 24 hours of the trajectory:
`custos-dream audit > /tmp/custos-dream-audit.json` (deterministic, no inference; it writes
`dream/YYYY-MM-DD/audit.json` and the report picks it up). Read `jq '.runs, .counts'` and then
`jq '.problems'`. It flags wasted wakes (runs with no durable step), failed runs, monolith runs at
the iteration cap, **asks nobody touched** for six hours (quick ones first), responder replies that
promised work in words, sends that failed, and helpers that died without a FINAL. Read the raw
trajectory around anything it names, with targeted queries and bounded output, before deciding.

For each real problem, one of three outcomes, recorded, never silent:
1. **Fix it now** when one bounded command does (an untouched QUICK ask → do it as the mealplan
   skill says and complete it; a stale goal that is done → `custos-memory complete` with evidence).
2. **File your own goal** for anything that needs a wake of work (`mem add --type goal
   "dream-audit YYYY-MM-DD: <problem> → <the fix, one wake>"`); the mind picks it up.
3. **Raise it** when it is Hal's to decide (a harness bug, the bridge, a policy, a cost) or Kim's
   (the DM loop, a bridge on Kim's side): file your own goal whose next action is to tell them in
   the Signal chat — Hal in Collette Haus or his DM, Kim in the AI chat group — on the next ordinary
   wake via the deferred-ask delivery path, with the evidence from `audit.json`. The dream itself
   still sends nothing.

Put the problems and what you did with them in the `finish --note`.

## Finish

Run `custos-dream finish --note 'What changed; specific unresolved questions'`.
This writes `$IDENTITY_DIR/dream/YYYY-MM-DD/report.md` and a private native
observation. End the wake with a short FINAL pointing to that report. No public
post or Signal notification is part of the workflow. A later wake records an
expired/partial report if the review was interrupted; never claim full coverage
when only the batch or part of it was examined. Backups and per-change journals
remain beside the report for operator inspection and reversal.
