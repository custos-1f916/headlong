#!/usr/bin/env bash
# tests/test_shellm_maintenance_warn.sh — the harness-maintenance yield
#
# Usage: tests/test_shellm_maintenance_warn.sh
#
# When the maintenance flag appears, the loop yields between steps. With
# SHELLM_MAINTENANCE_WARN=1 (the monolith sets it) the run first gets ONE more
# block and a feedback step telling it to record its state; without it the
# yield is immediate, as before. A live nested sub-run is stopped at the
# yield and named in the handoff step. Same llm stub as the beacon test.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"; pkill -9 -f "maint-sub-task" 2>/dev/null' EXIT
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL %s%s\n' "$1" "${2:+ — $2}"; }

mkdir -p "$WORK/script" "$WORK/home"
cp -R "$REPO/bin" "$WORK/toolbin"
cat > "$WORK/toolbin/llm" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do [[ "$a" == "--thinking" ]] && main_loop=1; done
if [[ "${main_loop:-0}" -ne 1 ]]; then printf '{}\n'; exit 0; fi
n=$(( $(cat "$LLM_COUNT" 2>/dev/null || echo 0) + 1 ))
printf '%s' "$n" > "$LLM_COUNT"
[[ -f "$LLM_SCRIPT/$n.sleep" ]] && sleep "$(cat "$LLM_SCRIPT/$n.sleep")"
if [[ -f "$LLM_SCRIPT/$n" ]]; then cat "$LLM_SCRIPT/$n"; else cat "$LLM_SCRIPT/last"; fi
EOF
chmod +x "$WORK/toolbin/llm"
export PATH="$WORK/toolbin:$PATH" LLM_COUNT="$WORK/count" LLM_SCRIPT="$WORK/script"
export HOME="$WORK/home" HEADLONG_HOME="$WORK/home/.headlong" ANTHROPIC_API_KEY="test-key"
export SHELLM_MODEL="test-model" SHELLM_ENV=local SHELLM_BEACON_INTERVAL=1
export IDENTITY_NAME=custos SHELLM_MAINTENANCE_FLAG="$WORK/maintenance"
fence() { printf '```bash\n%s\n```\n' "$1"; }
run_shellm() {
    : > "$LLM_COUNT"; rm -rf "$WORK/wd"; mkdir -p "$WORK/wd"; rm -f "$WORK/maintenance"
    ( cd "$WORK/wd" && "$WORK/toolbin/shellm" --workdir "$WORK/wd" --max-iterations 6 "$@" ) > "$WORK/out" 2> "$WORK/err" < /dev/null
}
traj_of() { cat "$HEADLONG_HOME/trajectories"/*"$1"/trajectory.jsonl 2>/dev/null; }

# --- no warning configured: the flag yields at the next step boundary --------
fence "touch '$WORK/maintenance'; echo step1" > "$WORK/script/1"
fence 'echo step2' > "$WORK/script/2"
fence 'FINAL=done' > "$WORK/script/last"
SHELLM_MAINTENANCE_WARN=0 run_shellm "plain yield"
[[ "$(cat "$LLM_COUNT")" == "1" ]] && ok "without WARN the run yields right after the block that raised the flag" || bad "plain yield count" "$(cat "$LLM_COUNT")"
traj_of plain-yield | grep -q '"harness-handoff"' && ok "handoff step recorded" || bad "handoff step"
traj_of plain-yield | grep -q '"feedback"' && bad "no feedback step without WARN" || ok "no feedback step without WARN"

# --- WARN=1: one feedback step, one more block, then the yield ---------------
fence "touch '$WORK/maintenance'; echo step1" > "$WORK/script/1"
fence "echo recording-state" > "$WORK/script/2"
fence 'echo must-not-run' > "$WORK/script/3"
SHELLM_MAINTENANCE_WARN=1 run_shellm "warned yield"
[[ "$(cat "$LLM_COUNT")" == "2" ]] && ok "with WARN the run gets exactly one more block" || bad "warned yield count" "$(cat "$LLM_COUNT")"
traj_of warned-yield | grep -q 'harness maintenance\] A harness release is waiting' && ok "the extra block is preceded by a feedback step that says what it is for" || bad "warning feedback"
traj_of warned-yield | grep -q 'recording-state' && ok "the extra block ran" || bad "extra block ran"
traj_of warned-yield | grep -q 'must-not-run' && bad "no block after the extra one" || ok "no block after the extra one"
traj_of warned-yield | grep -q '"harness-handoff"' && ok "handoff recorded after the extra block" || bad "handoff after warning"

# --- a flag raised before the first block yields at once, no warning --------
touch "$WORK/maintenance"
: > "$LLM_COUNT"; rm -rf "$WORK/wd"; mkdir -p "$WORK/wd"
( cd "$WORK/wd" && SHELLM_MAINTENANCE_WARN=1 "$WORK/toolbin/shellm" --workdir "$WORK/wd" --max-iterations 3 "early flag" ) > "$WORK/out" 2> "$WORK/err" < /dev/null
early=$(cat "$LLM_COUNT")
[[ -z "$early" || "$early" == "0" ]] && ok "a flag already set at start yields before any block" || bad "early flag" "llm calls: $early"

# --- a live sub-run is stopped at the yield and named --------------------------
fence "touch '$WORK/maintenance'; subrun --wait --max-iterations 1 'maint-sub-task' > sub.txt 2>&1 & echo started" > "$WORK/script/1"
printf '25' > "$WORK/script/2.sleep"        # the sub-run's own model call stalls
fence 'FINAL=sub-answer' > "$WORK/script/2"
SHELLM_MAINTENANCE_WARN=0 run_shellm "subrun yield"
sleep 2
if pgrep -f "maint-sub-task" >/dev/null 2>&1; then
    bad "live sub-run is stopped at the yield" "$(pgrep -af maint-sub-task | head -2)"
else
    ok "live sub-run is stopped at the yield"
fi
traj_of subrun-yield | grep -q 'Live sub-run process(es) stopped with it' && ok "handoff names the stopped sub-run" || bad "handoff names the stopped sub-run" "$(traj_of subrun-yield | grep -o '"harness-handoff"[^}]*' | head -c 300)"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
