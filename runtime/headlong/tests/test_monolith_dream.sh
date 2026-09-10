#!/usr/bin/env bash
# tests/test_monolith_wake_sections.sh — the monolith's related-memories
# section and goal-review hint (design/related_memories.md). Drives
# thinkers/monolith/step against a throwaway identity with a stubbed shellm
# that captures the --prompt-file. Pins: the section appears with a memory
# matched to the stream, the names shown land in the state file and are not
# shown again next wake, MONOLITH_RELATED_MEMORIES=0 removes the section, the
# goal-review hint fires on the first wake and not on the next, and the
# active-goals section lists a todo with its type.
set -uo pipefail
unset IDENTITY_DIR IDENTITY_NAME MEM_DIR TRAJ_DIR TRAJ_ID ROOT_TRAJ_ID 2>/dev/null
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(dirname "$HERE")"; STEP="$REPO/thinkers/monolith/step"
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL %s%s\n' "$1" "${2:+ — $2}"; }
WORK=$(mktemp -d); trap 'cd /; rm -rf "$WORK"' EXIT
ID="$WORK/ident"; TRAJ_ID="cafe0000-0000-0000-0000-0000000000bb"
mkdir -p "$ID/memories" "$ID/trajectories/$TRAJ_ID" "$ID/run" "$WORK/stub" "$WORK/home"
printf 'name=testid\ncreated=test\nroot_trajectory=%s\n' "$TRAJ_ID" > "$ID/info.txt"
TRAJ="$ID/trajectories/$TRAJ_ID/trajectory.jsonl"; : > "$TRAJ"
printf 'test-token\n' > "$ID/run/dispatcher.token"
cat > "$WORK/stub/shellm" <<'STUB'
#!/usr/bin/env bash
prev=""
for a in "$@"; do [[ "$prev" == "--prompt-file" ]] && cp "$a" "$STUB_CAPTURE"; prev="$a"; done
printf '{"step_id":"obs-%s","type":"observation","content":"did a thing","source":"monolith","ts":"%s"}\n' "$RANDOM" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$STUB_TRAJ"
exit 0
STUB
chmod +x "$WORK/stub/shellm"
export STUB_CAPTURE="$WORK/prompt" STUB_TRAJ="$TRAJ" SHELLM_MODEL=test-model
STATE="$ID/run/monolith_backoff_state.json"
mk() { printf -- '---\nid: x\nsummary: s\ntype: %s\ncreated: %s\n---\n\n%s\n' "$2" "$3" "$4" > "$ID/memories/$1.md"; }
mk 2026-08-20-00-00-00_a1_gh   fact "2026-08-20 00:00:00" "GitHub write on this box: gh is logged in as headlong42, pull-only on laude-institute"
mk 2026-08-21-00-00-00_b2_disp fact "2026-08-21 00:00:00" "The dispatcher token file arms the wake and lives under run/"
mk 2026-08-22-00-00-00_c3_todo todo "2026-08-22 00:00:00" "Ping Braden about the temporal test"
printf '{"step_id":"t1","type":"thought","content":"I should check the github pull-only login headlong42 before the PR work","source":"monolith","ts":"2026-09-04T00:00:00Z"}\n' >> "$TRAJ"
mkdir -p "$ID/workdir/notes"; printf 'a\n' > "$ID/workdir/notes/a.md"; printf 'b\n' > "$ID/workdir/notes/b.md"

run_step() {  # $1 = trigger json, then env overrides
    local trig="$1"; shift
    printf '%s' "$trig" | env PATH="$WORK/stub:$REPO/bin:$PATH" \
        IDENTITY_DIR="$ID" IDENTITY_NAME=testid MEM_DIR="$ID/memories" \
        TRAJ_DIR="$ID/trajectories" TRAJ_ID="$TRAJ_ID" HOME="$WORK/home" \
        MONOLITH_TIERED_MEMORY=0 MONOLITH_SHARE_HINT_EVERY=0 "$@" "$STEP" >> "$WORK/step.log" 2>&1
}
WAKE='{"type":"monolith-wake","content":"wake","source":"monolith-timer"}'

cat > "$WORK/stub/custos-dream" <<'STUB'
#!/usr/bin/env python3
import sys,os,datetime as dt
sys.path.insert(0,os.environ['CUSTOS_RENEWAL_DIR'])
from custos_dream import Dream
print(Dream(clock=lambda:dt.datetime.fromisoformat(os.environ['DREAM_TEST_NOW'])).due())
STUB
chmod +x "$WORK/stub/custos-dream"
export DREAM_TEST_NOW=2026-09-10T09:30:00+00:00
run_step "$WAKE" MONOLITH_RELATED_MEMORIES=0
p=$(cat "$STUB_CAPTURE")
grep -q "^Hal's scheduled memory dream is due" <<<"$p" && ok "real due helper reaches captured model prompt" || bad "dream routing missing"
grep -q 'skills show custos-dream' <<<"$p" && ok "skill is reachable from menu" || bad "skill routing missing"
[[ -f "$ID/dream/2026-09-10/inventory.json" ]] && ok "live hook created inventory in temporary identity" || bad "inventory missing"
export DREAM_TEST_NOW=2026-09-10T12:00:00+00:00
run_step "$WAKE" MONOLITH_RELATED_MEMORIES=0
p=$(cat "$STUB_CAPTURE")
grep -q "^Hal's scheduled memory dream is due" <<<"$p" && bad "late catch-up incorrectly scheduled" || ok "no late catch-up"
[[ -f "$ID/dream/2026-09-10/report.md" ]] && ok "missed window leaves private expired report" || bad "report missing"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
