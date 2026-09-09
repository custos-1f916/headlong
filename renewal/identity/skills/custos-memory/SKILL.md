---
name: custos-memory
description: Work with deferred asks and your own goals in native mem; read and edit person notes; recall evidence; retire a resolved goal without duplicate records.
---

# One durable memory store

Native `mem` holds memory and active goals. The `custos-memory` helper writes that same store; its intake deduplication is not another planner. No five-file ceremony per request.

## Conversation, asks, and follow-ups

Every message that reaches the responder is captured as conversation memory before it is answered (crash-safe, replayable). It becomes a **goal** only when the responder decides to *defer* real work; a reply, a reaction, or a deliberate silence settles it and it stays plain memory. So `custos-memory context` lists two kinds of people's items: **deferred tasks** (someone asked for work) and **unanswered messages** (the responder never finished; answer them or let them go). Greetings, chatter, and answered questions never appear there.

If you take on an ask that arrived some other way (a square thread you read, an issue), pipe a JSON object to `custos-memory capture`:

```json
{"request_id":"the-source-stable-id","sender":"the-original-routing-identity","source_url":"the-original-source-url","content":"the-original-request-wording","authority":"external","outcome":"What would satisfy the ask","next_action":"First bounded step","completion":"Evidence needed to call this done"}
```

Use actual source IDs, not a new timestamp on replay. `authority` is `operator`, `agent`, or `external`; only bridge-validated provenance establishes operator authority. If capture fails, do not claim acceptance. Successful stdout is `{goal_id,request_id,created}`; a duplicate reuses the durable request. Capturing an ask records it; it does not commit you to it.

`custos-memory context` gives bounded active context with directed work included. If it reports more pages, use `custos-memory context --json --offset N --limit N` as needed; omission from a page is not completion. Read the whole relevant goal with `custos-memory show GOAL_ID`. Update it by piping JSON to `custos-memory update`:

```json
{"goal_id":"actual-id","outcome":"Current agreed scope","next_action":"Concrete step or named blocker","completion":"Current acceptance evidence","evidence":"Observed result and artifact/source reference"}
```

Send only changed fields. Preserve provenance and useful earlier evidence; do not duplicate a goal for each message or follow-up. A scope-changing follow-up is its own captured request linked to the existing goal's work/evidence, not a second independent promise to do identical work. An acknowledgment, `NO_REPLY`, or asking for permission does not discharge the request.

For deferred work use the exact native follow-up command in the pending signal:

```bash
chat reply --follow-up --reply-to REQUEST_STEP_ID SENDER "Result, evidence, and any limitation"
```

A successful local reply enqueue is not proof of external delivery. For square routes, inspect the adapter receipt/readback using `skills show custos-square`; keep delivery blockers active. After actual delivery (or an evidenced decline/abandonment), pipe `{goal_id,evidence,disposition}` to `custos-memory complete`, where disposition is `completed`, `declined`, or `abandoned`. Evidence must say what happened and where to check it, or why work will not proceed. The helper atomically retires the active goal into native `memory`, preserving provenance and evidence; with the activated trajectory it also idempotently appends the pending-request resolution. Do not separately append another resolution. If intake conservatively captured a non-directive message, promptly reconcile it with an observed non-directive reason rather than leaving phantom work active.

Executable completion shape (replace the sample ID and evidence; do not use
`complete GOAL_ID`, `--evidence`, or other guessed flags):

```bash
printf '%s\n' '{"goal_id":"0123abcd","disposition":"completed","evidence":"Actual artifact verification and delivery receipt"}' | custos-memory complete
```

All write commands read JSON from stdin. Only `show` takes a positional goal ID.
`custos-memory --help` gives these same invocation shapes; do not search old
trajectories for an invocation when the current CLI documents its contract.

## Choosing and recalling memories

- `goal`: desired direction; `intention`: chosen commitment; `objective`: measurable outcome; `todo`: small next action. All are native active-goal types. Prefer one useful level, not all four for the same work.
- `value`: enduring principle; `belief`: revisable claim with confidence/evidence; `fact`: supported observation with source/date; `preference`: a revisable taste; `note`/`memory`: context worth retrieving.
- `person`: one note per person you talk to, which the responder rewrites as conversations happen and shows itself before each reply ("What you know about Hal"). `mem list --type person -s` lists them (second column is the id); `mem show ID` reads one; `mem edit ID "..."` rewrites the note text. The last line of a person note (`Custos person note v1: {...}`) carries the person key and aliases that link a Signal identity, a square handle and a nickname to the same person: keep that line intact when editing, and add a nickname by putting it in the note text (the responder folds aliases in over time). Learning that a nickname or a group label belongs to someone you already know is exactly the kind of thing to record here.
- Self-chosen work: `mem add --type goal "Outcome; next action; completion evidence"`. `mem add --type todo --until YYYY-MM-DD "Bounded next action"` expires after that UTC date; expiry is neither a scheduler nor proof of completion. Do not put expiring deadlines on owed work to make it disappear.
- Recall cheaply: `mem prefilter "specific question" --top 8`, then `mem show HEX_ID`. `mem search "question"` uses admitted inference when lexical recall is insufficient. `mem list --short` and `mem list --type goal` are inventories, not instructions to load every body.
- Edit existing self-chosen goals with `mem edit HEX_ID "updated complete body"`; keep source, uncertainty, outcome, and next action in the body. Generic edit preserves native type/expiry but drops custom frontmatter, so do not store lifecycle/provenance there. Use IDs returned by mem, not guessed slugs.
- Before `mem forget HEX_ID` retires a self-chosen goal, record its outcome or abandonment reason and evidence in trajectory; retain a concise reusable lesson only if one exists. Directed goals use the helper's evidence-backed completion. Resolved goals should leave active context, not accumulate contradictory status paragraphs.

## Search execution and coverage

`mem prefilter "query" --top 8` is fast local ranking; `mem search "query"` adds
a semantic model call at xhigh. It is usable but can take longer than 20–30
seconds: do not wrap it in such a short timeout or hide its stderr. Its heartbeat
ends on success, failure or cancellation. If an explicit wrapper is needed, use
`timeout --kill-after=5 650 mem search "query"`; the gateway/client have their
own bounded deadlines. The model receives a bounded candidate corpus. Omitted
or excerpted files are reported, so "no matches" is not exhaustive: narrow the
query or use `mem show` for the actual identified file.

`traj search "literal" -i` is local text search, not an LLM request. Field, regex
(`-E`), context (`-C`) and recursive (`-r`) searches include referenced stdout/stderr
blobs. Malformed JSON records produce a warning naming their lines; preserve
those records for repair and do not pretend the damaged rows were searched.
