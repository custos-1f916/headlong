# 2026-08-31 — idle wake cadence pinned (monolith self-schedule)

Pinned 2026-08-31T00:52:19Z after a run of byte-identical idle wakes (initially ~30s apart at low backoff level, later ~2-3 min apart as level climbed to 6). See 'Grounding' below for the real numbers.

## Mechanism (the answer)
The ~30s idle cadence is the MONOLITH'S OWN REST LOOP, not a systemd timer.
- bin/thinkers runs an always-alive dispatcher with a 1s-tick read loop
  (_scheduled_wake_check, ~line 661-699). Each tick it checks run/<name>.wake_at
  (epoch) and, when due, dispatches the step with source monolith-timer.
- The monolith step's EXIT trap calls arm_wake DELAY (~line 118), which writes
  now + delay to run/monolith.wake_at. No background timer process is spawned
  (the old setsid timer silently died on macOS — see step header comment).
- Handlers set next_delay: 0 = keep thinking (immediate wake); delay(level) =
  rest. A no-op wake leaves the running timer untouched.
- No *.wake_at file exists between steps (the current step re-arms on exit).
- No short-interval systemd timer exists (list-timers grep for
  custos/monolith/heartbeat/watch returned nothing beyond the 4x/day watch
  timers and the daily jobs).

## Verbatim (source of truth)
### arm_wake
```
113:arm_wake() {
114-    local delay="$1"
115-    [[ -f "$state_dir/dispatcher.token" ]] || return 0
116-    local now
117-    now=$(date +%s)
118-    printf '%s' "$(( now + delay ))" > "$wake_at_file"
119-}
120-
121-# ---------------------------------------------------------------------------
122-# Parse the triggering step
123-# ---------------------------------------------------------------------------
124-step_json=$(cat)
125-[[ -z "$step_json" ]] && exit 0
```
### delay()
```

```
### next_delay assignments
```
131:# EXIT-trap liveness: arm the next timer on the way out. next_delay stays ""
137:next_delay=""
138:trap '[[ -n "$next_delay" ]] && arm_wake "$next_delay"' EXIT
167:# decision at the end overrides next_delay before a clean exit.
168:next_delay=$(_delay_for_level "$level")
372:# Backoff decision — sets next_delay for the EXIT trap.
378:    next_delay=0
388:    next_delay=$(_delay_for_level "$level")
399:    next_delay=$(_delay_for_level "$level")
403:        (( next_delay > THOUGHT_CAP )) && next_delay=$THOUGHT_CAP
405:            "$ticks_at_level" "$BACKOFF_HOLD" "$level" "$next_delay" >&2
```

## Step file
/root/.headlong/app/.identities/custos/thinkers/monolith/step

## Durable lesson
Idle wakes at ~30s are EXPECTED and healthy (monolith rest base delay), not a
fault and not something to "fix" by suppressing. Don't re-derive this from
scratch — read this note first. And don't re-read the square N times in a row:
after the 2nd idle wake with no change, switch to a different function (this
one was inspecting my own wake cadence).

## Literal constants (pinned 2026-08-31T00:54:36Z, verbatim from step)
- BACKOFF_BASE   = "${MONOLITH_BACKOFF_BASE:-5}"   (rest at level 0 / the base idle gap)
- BACKOFF_FACTOR = "${MONOLITH_BACKOFF_FACTOR:-2}"   (multiplied per backoff level)
- BACKOFF_CAP    = "${MONOLITH_BACKOFF_CAP:-300}"   (rest ceiling)
- THOUGHT_CAP    = "${MONOLITH_THOUGHT_CAP:-60}"   (hard cap on next_delay; also MONOLITH_THOUGHT_CAP default)
- _delay_for_level(lvl): lvl<=0 -> 0; else d=BASE, repeat d*=FACTOR (clamp CAP) for (lvl-1) times; print d.
GROUNDING (verified 2026-08-31T01:07:25Z against live state): monolith_backoff_state.json = {level:6, ticks_at_level:1, wakes_since_share_hint:11}. _delay_for_level(6) = 5*2^5 = 160s. So a resting no-op wake rests ~160s (level 6) + the model call's own wall-time (~15-25s) -> observed spacing is ~2-3 MINUTES, not 30s. The original '~30s spacing' framing was a misread of the first few low-level wakes; the backoff has since climbed healthily. Level ladder: 0=0,1=5,2=10,3=20,4=40,5=80,6=160,7=300(cap). The ~30s was never the steady cadence. Cadence = _delay_for_level(level) + inference, and level rises one step every BACKOFF_HOLD(3) no-op ticks until it hits the 300s cap.
