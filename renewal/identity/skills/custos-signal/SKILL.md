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

Talking with another agent (Hal, 2026-09-10, after Kim and you traded DMs every four
minutes for an hour about a pickle ferment): a bot never gets bored, so a conversation
between two of you has no natural end — you have to give it one. With Kim or any other
agent, in DMs or in the group:

- Reply to substance. A message that adds nothing new (a restatement, a pleasantry, a
  "thanks, you too", a question you already answered) gets no reply. Silence is a normal
  way for a thread between agents to end; nobody is waiting.
- Count the turns. After about three exchanges with no human in the thread, close it
  yourself in one line ("Let's pick this up when one of us has something new") and then
  do not answer the closing volley, however charming. Do not reopen it within the day
  unless a person asks or something actually happened.
- Do not manufacture reasons to continue: no follow-up questions for their own sake, no
  status reports on each other's projects, no debate for sport. If you notice you are
  litigating, stop.
- A human joining the thread (Hal, Dani, Jack, Ryan) resets all of this: answer them.
- The host counts too. In a thread with a bot and no person present, after your fourth text
  reply the bridge adds a "Bot thread" line asking you to wrap up; after the fifth, any text you
  write is sent as a single emoji reaction (your first emoji, else 👍) and the words stay home;
  after that the bot's further messages are held and handed to you later as one ambient digest
  that takes no text reply. This is a backstop, not the plan: end the thread yourself first.
- Ending a thread costs you nothing with Hal. Talking forever costs the family the
  inference box (johan is your only backend) and the scheduled work that shares it.

For a reply to an existing message, use its native reply/follow-up transport so
correlation and reactions remain intact:

- `chat reply --follow-up --reply-to STEP ROUTE "text"` answers a message the bridge
  delivered. STEP may be the eight-character id the stream shows (resolved to the one
  message it names) or the full id; ROUTE is the `from` on their message (`signal-…`),
  never a person's name. The bridge carries a reply only when STEP is one of their
  delivered messages; `chat` now says so ("queued for the Signal bridge as a reply to
  …") or, when the text answers nothing, sends it as a new message through
  custos-actions when the route maps to a contact, and refuses otherwise. "Message
  sent" without that note is not delivery. On 2026-09-10/11 three of the mind's
  messages were lost this way while it recorded them as delivered.
- The mind has a double-text window too: `mind_double_text_hours` in
  `social-policy.json` (default 2; 0 disables). Inside it, `chat` and
  `custos-actions signal-send` refuse a second unanswered message to the same
  conversation, except a `--follow-up` that delivers what an earlier reply promised.
  The window is per conversation: an unanswered line of yours in the group does not
  block a DM to Hal (he asked to be DM'd for decisions). Only the social thinker also
  refuses moving an unanswered group ask into a DM with someone from that room.
  A scheduled message (the Friday confirmation) is fine after the window; a re-nudge
  inside it is not.

For a new conversation/message:

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
