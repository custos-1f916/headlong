---
name: custos-reminders
description: Deterministically schedule a future Signal reminder for Hal, Dani, or another allowlisted contact. Load whenever someone asks to be reminded, nudged, pinged, or messaged at a particular future time.
---

# Deterministic Signal reminders

A reminder is not a future model wake. On the first tool-capable wake that sees
the deferred ask, choose the short message you actually want the person to receive
and schedule it once with `custos-reminders`. A persistent system timer performs
delivery through `custos-actions` at the deadline without inference. Never defer
again merely because the send time is later, and never create a goal whose plan is
"wake around then and remember to send."

Write one JSON object to a temporary file and schedule it:

```json
{"at":"2026-09-15T06:15:00","timezone":"America/Denver","target":"Dani","message":"Morning — rice-cooker nudge: put the rice on now.","goal_id":"0123abcd"}
```

```sh
custos-reminders schedule < /tmp/reminder.json
```

- `at` is an ISO local date and time. Pair a local value with an IANA timezone
  such as `America/Denver`; the tool resolves DST now and stores an absolute UTC
  deadline. An explicit-offset ISO timestamp may omit `timezone`. Ambiguous or
  nonexistent wall times are refused instead of guessed.
- `target` is a current, unique Signal contact label. Scheduling resolves it to
  the host's opaque allowlisted destination immediately; never guess a phone
  number or destination ID.
- `message` is the exact future Signal text. Write it now. The dispatcher does
  not call a model, rewrite the message, or consult the goal at send time.
- `goal_id` links the reminder to the deferred ask. Keep that goal open until
  `custos-reminders status ID` shows `submitted`; scheduling alone proves only
  that the timer owns it.

The returned 16-hex reminder ID and `reminder-…` request ID are stable. Repeating
the identical schedule is an idempotent read of the same record. At the deadline,
every retry uses that same actions request ID; a crash or timeout cannot mint a
second Signal message. `queued` means the host broker durably owns delivery;
`submitted` means Signal accepted it. `uncertain` is not permission to create a
replacement reminder or request ID.

Use `custos-reminders list`, `custos-reminders status ID`, and
`custos-reminders cancel ID`. Cancellation is allowed only before the broker owns
the send. If the requested time has already passed, tell the person and either
send now through the normal Signal path or ask for a new time; do not silently
move the deadline.
