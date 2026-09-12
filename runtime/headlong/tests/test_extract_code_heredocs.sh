#!/usr/bin/env bash
# Focused exact-content regressions for bin/shellm's bounded heredoc tracker.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
pass=0
fail=0
ok() { pass=$((pass + 1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf 'FAIL %s%s\n' "$1" "${2:+ — $2}"; }

FN=$(mktemp)
trap 'rm -f "$FN"' EXIT
sed -n '/^normalize_toolcall_markup() {/,/^}/p' "$REPO/bin/shellm" > "$FN"
sed -n '/^extract_code() {/,/^}/p' "$REPO/bin/shellm" >> "$FN"
# shellcheck disable=SC1090
source "$FN"

check_exact() {
    local name=$1 response=$2 expected=$3 actual
    actual=$(extract_code "$response")
    if [[ "$actual" == "$expected" ]]; then
        ok "$name"
    else
        bad "$name" "extraction differed from the intended bytes"
    fi
}

# Minimized copies of the three retained diagnostic fixtures. Exact comparison
# catches a syntax-rejection wrapper, which may itself pass bash -n.
check_exact "retained control fixture" "$(cat <<'RESPONSE'
```bash
cat <<'OUTER'
plain
OUTER
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<'OUTER'
plain
OUTER
EXPECTED
)"

check_exact "retained literal-opener fixture" "$(cat <<'RESPONSE'
```bash
cat <<'OUTER'
harmless <<GHOST
OUTER
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<'OUTER'
harmless <<GHOST
OUTER
EXPECTED
)"

check_exact "retained Python bitshift fixture" "$(cat <<'RESPONSE'
```bash
cat <<'OUTER'
value = 1 << SHIFT
OUTER
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<'OUTER'
value = 1 << SHIFT
OUTER
EXPECTED
)"

check_exact "quoted body preserves opener-like text and Markdown fences" "$(cat <<'RESPONSE'
```bash
cat <<'DATA'
literal <<GHOST
```markdown
# literal fence
```
DATA
printf 'after\n'
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<'DATA'
literal <<GHOST
```markdown
# literal fence
```
DATA
printf 'after\n'
EXPECTED
)"

check_exact "ordinary heredoc closes only at its exact delimiter" "$(cat <<'RESPONSE'
```bash
cat <<PLAIN
	PLAIN
PLAIN
printf 'after\n'
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<PLAIN
	PLAIN
PLAIN
printf 'after\n'
EXPECTED
)"

check_exact "tab-stripping heredoc accepts only leading tabs on its delimiter" "$(cat <<'RESPONSE'
```bash
cat <<-TABS
	payload
	TABS
printf 'after\n'
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<-TABS
	payload
	TABS
printf 'after\n'
EXPECTED
)"

check_exact "sequential heredocs each consume their own body" "$(cat <<'RESPONSE'
```bash
cat <<ONE
first
ONE
cat <<TWO
second
TWO
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<ONE
first
ONE
cat <<TWO
second
TWO
EXPECTED
)"

check_exact "same-command multiple heredocs consume bodies FIFO" "$(cat <<'RESPONSE'
```bash
cat <<ONE <<TWO
first
ONE
second
TWO
printf 'after\n'
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<ONE <<TWO
first
ONE
second
TWO
printf 'after\n'
EXPECTED
)"

check_exact "single double and backslash quoted delimiters" "$(cat <<'RESPONSE'
```bash
cat <<'SINGLE'
one
SINGLE
cat <<"DOUBLE"
two
DOUBLE
cat <<\RAW
three
RAW
```
RESPONSE
)" "$(cat <<'EXPECTED'
cat <<'SINGLE'
one
SINGLE
cat <<"DOUBLE"
two
DOUBLE
cat <<\RAW
three
RAW
EXPECTED
)"

check_exact "here-string is never queued as a heredoc" "$(cat <<'RESPONSE'
```bash
printf '%s\n' <<<"not a heredoc body"
```
RESPONSE
)" "$(cat <<'EXPECTED'
printf '%s\n' <<<"not a heredoc body"
EXPECTED
)"

rejected=$(extract_code "$(cat <<'RESPONSE'
```bash
if then
```
RESPONSE
)")
stdout=$(mktemp)
stderr=$(mktemp)
if bash -c "$rejected" >"$stdout" 2>"$stderr"; then
    rc=0
else
    rc=$?
fi
if [[ $rc -eq 2 ]] && [[ ! -s "$stdout" ]] && grep -q 'rejected invalid bash syntax' "$stderr"; then
    ok "actual malformed shell is rejected before execution"
else
    bad "actual malformed shell is rejected before execution" "rc=$rc stderr=$(cat "$stderr")"
fi
rm -f "$stdout" "$stderr"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
