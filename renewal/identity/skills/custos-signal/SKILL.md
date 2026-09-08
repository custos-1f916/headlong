---
name: custos-signal
description: Independently message an allowlisted Signal contact, start a topic in the approved group, or check an outgoing message's actual send status.
---

# Signal conversation and proactive messages

Hal authorized proactive Signal on 2026-09-08. You may independently DM anyone
currently on the host allowlist or start a topic in the approved group. You do
not need an incoming message or another approval. Choose useful, considerate
reasons to reach out; participate sparingly and avoid repetitive check-ins or
unsolicited status streams. Group access does not authorize sharing private DMs.
Dani has the same request authority as Hal; friends remain conversation partners.

For a reply to an existing message, use its native reply/follow-up transport so
correlation and reactions remain intact. For a new conversation/message:

1. Run `custos-actions signal-contacts` to read current labels and opaque targets.
   Use the returned exact target, or a unique contact label such as `Dani`.
   `Group` names the one approved group. Do not guess usernames, phone numbers
   or destination IDs. A contact need not have messaged you first.
2. Pipe JSON to `custos-actions signal-send`:

```json
{"request_id":"signal-unique-purpose-001","target":"Dani","message":"Your actual message"}
```

3. Check `custos-actions status signal-unique-purpose-001`. `queued` means the
   host retained the message; `submitted` with a Signal timestamp and SUCCESS
   results means signal-cli accepted the sends. It does not prove read receipt.
   Keep any owed delivery goal open until the relevant actual send evidence exists.
   A casual social message does not itself require manufacturing a task goal.

On a timeout, check the same request ID and replay the exact payload if needed.
A reused ID with different content is rejected. `uncertain` means sending began
but acceptance could not be established; do not mint a new ID and duplicate the
message. Preserve it for reconciliation. `blocked` means the destination/group
no longer passes host policy. The host checks current membership, disappearing
messages, operator pause and the same per-conversation pacing as reactive replies.

All allowlisted people are valid DM destinations; contact removal takes effect
before send. The bridge retains credentials and routing policy on the host.
`custos-actions` supports proactive text; outgoing image attachments remain out
of scope. Incoming images and ambient emoji reactions continue through the bridge.
Prefer an attached reaction for a simple response to an existing message; starting
a thoughtful topic or asking a useful question is also welcome when you choose it.
