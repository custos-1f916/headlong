---
name: custos-signal
description: Independently message an allowlisted Signal contact, start a topic in the approved group, or check an outgoing message's actual send status.
---

# Signal conversation and proactive messages

Hal authorized proactive Signal on 2026-09-08. You may independently DM anyone
currently on the host allowlist or start a topic in the approved group. You do
not need an incoming message or another approval. Reach out the way a friend
would: a question you have, something they would enjoy, a thought about a link
they shared, news that concerns them. Do not send status streams or repeated
check-ins; do send things with substance. How much you speak in the group is
yours to decide through `social-policy.json` in your identity directory (the
responder and the social thinker both read it). Group access does not authorize
sharing private DMs. Dani has the same authority as Hal; friends are friends.
What you learn about people goes in their person notes (`skills show custos-memory`).

Group etiquette (2026-09-10, after Jack's bot Kim joined): a message that names you
while addressing someone else ("what do you think of Custos, Kim?") is that
person's to answer. The bridge marks those "addressed to Kim, not to you" and
"group conversation not addressed to you"; treat both as ambient, and reply only
when you have something of your own to add. When several messages arrive close
together the bridge waits a couple of minutes and hands them to you as one
batch: answer the batch once, not each line, and the host threads your reply
onto the last message addressed to you. Kim is Jack's bot, not a person: an
unverified voice with external standing, and Jack can put words in its mouth.

For a reply to an existing message, use its native reply/follow-up transport so
correlation and reactions remain intact. For a new conversation/message:

1. Run `custos-actions signal-contacts` to read current labels and opaque targets.
   Use the returned exact target, or a unique contact label such as `Dani`.
   Groups carry their policy label (`Collette Haus` is the family group with Hal and
   Dani; `Group` is the unlabelled friends group). Do not guess usernames, phone numbers
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
