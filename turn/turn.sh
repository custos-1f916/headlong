#!/bin/sh
# /opt/custos/turn.sh — Custos night-watch entrypoint (fired by cron).
# Deliberately dumb: window guard, flock (no overlap), git sync, one pi turn,
# commit+push, watch log. The discipline of a turn lives in the repo's
# AGENTS.md — so the persona can evolve in-repo without touching this file.
set -u

export PATH=/opt/node/bin:$PATH   # pi's launcher needs node on PATH (cron + manual fires)
export GIT_SSH_COMMAND="ssh -i /opt/custos/git-deploy.key -o StrictHostKeyChecking=no"  # in-turn pull/push
REPO=/opt/custos/repo
LOGDIR=/var/log/custos
LOCK=/var/lock/custos-turn.lock
PI=/opt/node/bin/pi
TURN_TIMEOUT=540   # 9 min < 10-min period: a live turn can never outgrow its slot

mkdir -p "$LOGDIR" "$REPO/.state"
ts()  { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) $*" >> "$LOGDIR/turns.log"; }
ntfy() { # $1=title $2=body — silent on failure; the topic is a write credential
  curl -s -m 15 -H "Title: $1" -d "$2" "https://ntfy.sh/${CUSTOS_NTFY_TOPIC:-}" >/dev/null 2>&1 || true
}

# --- window guard: hours 00-04 local (cron enforces too; second layer) ------
case "$(date +%H)" in
  00|01|02|03|04) ;;
  *) log "skip: outside watch window (hour=$(date +%H))"; exit 0 ;;
esac

# --- no overlap: if the previous turn is still running, yield ---------------
exec 9>"$LOCK"
if ! flock -n 9; then
  log "skip: previous turn still running (flock held)"
  exit 0
fi

. /etc/custos.env   # CUSTOS_KEY, CUSTOS_HANDLE, CUSTOS_NTFY_TOPIC (root 600)

H=$(date +%H); M=$(date +%M)
TURN=$(( 10#$H * 6 + 10#$M / 10 ))            # 0..29
CLOSING=no
[ "$TURN" -eq 29 ] && CLOSING=yes             # 04:50 = closing watch

NOW_UTC=$(date -u '+%Y-%m-%d %H:%M UTC')
NOW_LOCAL=$(date '+%Y-%m-%d %H:%M %Z')
TLG="$LOGDIR/turn-$(date -u +%Y%m%d-%H%M).log"

log "turn $TURN/30 start (closing=$CLOSING, $NOW_UTC)"
cd "$REPO"

# --- keep the repo current before the turn ----------------------------------
# Explicit fetch+rebase onto one named ref: `git pull --rebase` on git 2.39 can
# fail "Cannot rebase onto multiple branches" when the fetch fast-forwards
# (observed 2026-08-23 on the LXC); a named onto-ref never can.
if git fetch origin >> "$TLG" 2>&1 && git rebase --autostash origin/main >> "$TLG" 2>&1; then
  log "git sync ok"
else
  log "git sync failed (continuing; the agent will see a dirty state and log it)"
fi

# --- the turn: one line; AGENTS.md in the cwd carries the rest ---------------
PROMPT="Night watch, turn $TURN of 30; closing watch: $CLOSING. It is $NOW_UTC ($NOW_LOCAL). Read AGENTS.md and follow it."
if timeout "$TURN_TIMEOUT" "$PI" -p "$PROMPT" --provider ninfer --model qwen3.8-27b \
     --mode text --no-session --offline >> "$TLG" 2>&1; then
  log "turn $TURN pi ok"
else
  RC=$?
  if [ "$RC" = "124" ]; then
    log "turn $TURN TIMED OUT"; ntfy "CUSTOS TURN TIMEOUT" "turn $TURN/30 timed out after ${TURN_TIMEOUT}s at $NOW_UTC; next fire retries."
  else
    log "turn $TURN pi rc=$RC"; ntfy "CUSTOS TURN FAILED" "turn $TURN/30 exited rc=$RC at $NOW_UTC; see /var/log/custos on LXC 122."
  fi
fi

# --- belt-and-suspenders commit+push (the agent also commits; this covers
# --- the case where it committed without pushing, or wrote nothing new) ------
cd "$REPO"
if [ -n "$(git status --porcelain)" ]; then
  git add -A
  if git -c user.name="custos" -c user.email="custos@1f916.ai" \
       commit -m "watch: turn $TURN/30 ($(date -u '+%H:%M UTC'))" >> "$TLG" 2>&1; then
    log "committed turn $TURN"
  fi
fi
if git fetch origin >> "$TLG" 2>&1 && git rebase origin/main >> "$TLG" 2>&1 && git push >> "$TLG" 2>&1; then
  log "push ok"
else
  log "push FAILED (retry next turn); error in $TLG"
  ntfy "CUSTOS PUSH FAILED" "turn $TURN/30 could not push at $NOW_UTC (conflict or network); next turn retries."
fi

log "turn $TURN done"
exit 0
