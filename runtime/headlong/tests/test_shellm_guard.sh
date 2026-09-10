#!/usr/bin/env bash
# tests/test_shellm_guard.sh — the self-termination guard shellm prepends to
# every executed block. Pins: a protected pid, the protected pgid, `kill 0`,
# pkill/killall aimed at the runtime, and stopping the thinkers service or the
# thinkers runtime are refused with rc 125 and a message; killing a child the
# block started, and an ordinary failed kill, behave normally; SHELLM_GUARD=0
# yields an empty preamble. No LLM calls.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(dirname "$HERE")"
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL %s%s\n' "$1" "${2:+ — $2}"; }

# shellcheck disable=SC1091
_shellm_guard_preamble() { printf '. %q\n' "$REPO/bin/shellm-guard.sh"; }

export SHELLM_PROTECTED_PIDS="$$ 4242424" SHELLM_PROTECTED_PGID="$(ps -o pgid= -p $$ | tr -d ' ')"
preamble=$(_shellm_guard_preamble)
[[ -n "$preamble" ]] && ok "preamble is emitted" || bad "preamble is emitted"
_SHELLM_SCRIPT_PATH="$REPO/bin/shellm"
eval "$(sed -n '/^_shellm_guard_preamble() {/,/^}/p' "$REPO/bin/shellm")"
[[ -n "$(_shellm_guard_preamble)" && -z "$(SHELLM_GUARD=0 _shellm_guard_preamble)" ]] && ok "SHELLM_GUARD=0 disables it" || bad "SHELLM_GUARD=0 disables it"

run() { bash -c "$preamble
set -e
$1" 2>&1; printf '\n__rc=%s' "$?"; }
refused() { # name, block
    local out; out=$(run "$2")
    if [[ "$out" == *"[shellm guard] refused"* && "$out" == *"__rc=125" ]]; then ok "$1"; else bad "$1" "$(printf '%s' "$out" | tail -c 160)"; fi
}
refused "own pid is refused"            "kill $$"
refused "an ancestor pid is refused"    "kill -9 4242424"
refused "own process group is refused"  "kill -- -$SHELLM_PROTECTED_PGID"
refused "kill 0 is refused"             "kill 0"
refused "pkill of the runtime is refused"   "pkill -f shellm"
refused "killall bash is refused"           "killall bash"
refused "stopping the service is refused"   "systemctl stop headlong-thinkers@custos.service"
refused "thinkers stop is refused"          "thinkers stop monolith"

sleep 300 & child=$!
out=$(run "kill $child && echo child-killed")
[[ "$out" == *child-killed* && "$out" == *"__rc=0" ]] && ok "a child the block started can be killed" || bad "a child the block started can be killed" "$out"
kill "$child" 2>/dev/null; wait "$child" 2>/dev/null
out=$(run "printf '%s\\n' 'kill $$' > \$TMPDIR/child.sh; bash \$TMPDIR/child.sh")
[[ "$out" == *"[shellm guard] refused"* ]] && ok "a child script inherits the guard" || bad "a child script inherits the guard" "$(printf '%s' "$out" | tail -c 160)"
out=$(run "kill -9 4000000 || echo normal-failure")
[[ "$out" == *normal-failure* ]] && ok "a nonexistent pid fails normally, not via the guard" || bad "a nonexistent pid fails normally" "$out"
STUB=$(mktemp -d); printf '#!/usr/bin/env bash\necho real-systemctl "$@"\n' > "$STUB/systemctl"; chmod +x "$STUB/systemctl"
out=$(PATH="$STUB:$PATH" run "systemctl status headlong-thinkers@custos"); rm -rf "$STUB"
[[ "$out" == *"real-systemctl status"* ]] && ok "systemctl status passes through" || bad "systemctl status passes through" "$out"

printf '\n%d passed, %d failed\n' "$pass" "$fail"; [[ "$fail" -eq 0 ]]
