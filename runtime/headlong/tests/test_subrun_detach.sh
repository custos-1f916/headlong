#!/usr/bin/env bash
# tests/test_subrun_detach.sh — bin/subrun: sync for small tasks, detached for
# big ones, a report file either way, and whole-file output limits for the child.
#
# Usage: tests/test_subrun_detach.sh
#
# On 2026-09-11 a 40-iteration foreground sub-run was killed at the block
# ceiling, its orphan kept working, and its FINAL went to a dead pipe. subrun
# now detaches anything above SUBRUN_SYNC_MAX_ITER, prints where the report
# will land, and returns at once. Same llm stub as the beacon test.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
WORK=$(mktemp -d)
trap 'pkill -9 -f "detach-test-task" 2>/dev/null; rm -rf "$WORK"' EXIT
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL %s%s\n' "$1" "${2:+ — $2}"; }
wait_until() { local deadline=$(( $(date +%s) + $1 )); shift; while ! "$@" >/dev/null 2>&1; do [[ $(date +%s) -ge $deadline ]] && return 1; sleep 0.3; done; }

mkdir -p "$WORK/script" "$WORK/home" "$WORK/wd"
cp -R "$REPO/bin" "$WORK/toolbin"
cat > "$WORK/toolbin/llm" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do [[ "$a" == "--thinking" ]] && main_loop=1; done
if [[ "${main_loop:-0}" -ne 1 ]]; then printf '{}\n'; exit 0; fi
n=$(( $(cat "$LLM_COUNT" 2>/dev/null || echo 0) + 1 ))
printf '%s' "$n" > "$LLM_COUNT"
# Record the env the child runs with, so the test can check the output limits.
printf 'prompt_limit=%s block_limit=%s effort=%s\n' "${SHELLM_STDOUT_PROMPT_LIMIT:-}" "${SHELLM_CONTEXT_BLOCK_LIMIT:-}" "${SHELLM_EFFORT:-}" > "$LLM_SCRIPT/env.seen"
for a in "$@"; do [[ "$prev" == "--effort" ]] && printf 'effort_flag=%s\n' "$a" >> "$LLM_SCRIPT/env.seen"; prev="$a"; done
if [[ -f "$LLM_SCRIPT/$n" ]]; then cat "$LLM_SCRIPT/$n"; else cat "$LLM_SCRIPT/last"; fi
EOF
chmod +x "$WORK/toolbin/llm"
export PATH="$WORK/toolbin:$PATH" LLM_COUNT="$WORK/count" LLM_SCRIPT="$WORK/script"
export HOME="$WORK/home" HEADLONG_HOME="$WORK/home/.headlong" ANTHROPIC_API_KEY="test-key"
export SHELLM_MODEL="test-model" SHELLM_ENV=local SHELLM_BEACON_INTERVAL=1 TMPDIR="$WORK/tmp"
mkdir -p "$TMPDIR"
fence() { printf '```bash\n%s\n```\n' "$1"; }

# --- small task: synchronous, FINAL on stdout, report file written -------------
: > "$LLM_COUNT"; rm -f "$LLM_SCRIPT"/*
fence 'FINAL="sync-answer"' > "$WORK/script/last"
out=$(cd "$WORK/wd" && subrun --max-iterations 2 --report "$WORK/sync.report" "detach-test-task small" 2>"$WORK/err")
grep -q 'sync-answer' <<<"$out" && ok "a small task runs in the foreground and prints its FINAL" || bad "sync FINAL" "$out / $(tail -3 "$WORK/err")"
grep -q 'sync-answer' "$WORK/sync.report" && grep -q '# state: exited rc=0' "$WORK/sync.report" && ok "the report file holds the FINAL and the exit state" || bad "sync report" "$(cat "$WORK/sync.report")"
grep -q 'prompt_limit=24576 block_limit=32768' "$LLM_SCRIPT/env.seen" && ok "the child sees whole outputs (24 KB prompt limit, 32 KB block limit)" || bad "child limits" "$(cat "$LLM_SCRIPT/env.seen")"
grep -q 'effort_flag=medium' "$LLM_SCRIPT/env.seen" && ok "effort defaults to medium" || bad "effort default" "$(cat "$LLM_SCRIPT/env.seen")"
: > "$LLM_COUNT"
out=$(cd "$WORK/wd" && subrun --max-iterations 2 --effort high "detach-test-task effort" 2>/dev/null)
grep -q 'effort_flag=high' "$LLM_SCRIPT/env.seen" && ok "--effort overrides the default" || bad "effort override" "$(cat "$LLM_SCRIPT/env.seen")"
grep -q 'at most 10 read-only steps' "$(ls "$WORK/tmp"/subrun.* 2>/dev/null | head -1)" 2>/dev/null && bad "prompt file is removed after a sync run" || ok "prompt file is removed after a sync run"

# --- big task: detached, returns at once, report lands later ------------------
: > "$LLM_COUNT"
printf '3' > "$WORK/script/1.sleep"
fence 'FINAL="detached-answer"' > "$WORK/script/last"
start=$(date +%s)
out=$(cd "$WORK/wd" && subrun --max-iterations 40 --report "$WORK/big.report" "detach-test-task big" 2>"$WORK/err")
elapsed=$(( $(date +%s) - start ))
[[ "$elapsed" -le 2 ]] && ok "a big task returns immediately (took ${elapsed}s)" || bad "detach returns at once" "took ${elapsed}s"
grep -q "Report file: $WORK/big.report" <<<"$out" && grep -q 'do NOT wait here' <<<"$out" && ok "it says where the report lands and not to wait" || bad "detach message" "$out"
grep -q '# state: running' "$WORK/big.report" && ok "the report file exists at once and says running" || bad "report placeholder" "$(cat "$WORK/big.report")"
wait_until 25 grep -q 'detached-answer' "$WORK/big.report" && ok "the FINAL lands in the report file later" || bad "detached FINAL" "$(cat "$WORK/big.report")"
wait_until 10 grep -q '# state: exited rc=0' "$WORK/big.report" && ok "the exit state is appended when the child ends" || bad "detached exit state" "$(tail -2 "$WORK/big.report")"
grep -q 'prompt_limit=24576' "$LLM_SCRIPT/env.seen" && ok "the detached child has the same whole-output limits" || bad "detached limits" "$(cat "$LLM_SCRIPT/env.seen")"

# --- --wait and --detach override the size rule ---------------------------------
: > "$LLM_COUNT"; rm -f "$WORK/script/1.sleep"
out=$(cd "$WORK/wd" && subrun --wait --max-iterations 40 "detach-test-task forced-wait" 2>/dev/null)
grep -q 'detached-answer' <<<"$out" && ok "--wait keeps a big task in the foreground" || bad "--wait" "$out"
: > "$LLM_COUNT"
out=$(cd "$WORK/wd" && subrun --detach --max-iterations 1 --report "$WORK/small-detached.report" "detach-test-task forced-detach" 2>/dev/null)
grep -q 'Report file:' <<<"$out" && wait_until 20 grep -q 'detached-answer' "$WORK/small-detached.report" && ok "--detach detaches a small task" || bad "--detach" "$out"

# --- a default report path is announced when none is given ---------------------
: > "$LLM_COUNT"
out=$(cd "$WORK/wd" && subrun --detach --max-iterations 1 "detach-test-task default-report" 2>/dev/null)
rp=$(grep -o 'Report file: .*' <<<"$out" | cut -d' ' -f3)
[[ -n "$rp" && "$rp" == "$TMPDIR"/subrun-*.report ]] && wait_until 20 grep -q 'detached-answer' "$rp" && ok "default report path under TMPDIR" || bad "default report path" "$out"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
