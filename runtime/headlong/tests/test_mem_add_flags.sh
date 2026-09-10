#!/usr/bin/env bash
# tests/test_mem_add_flags.sh — `mem add --help` prints usage instead of storing
# a memory whose text is "--help"; an unknown option dies; `--` allows dash text.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(dirname "$HERE")"
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL %s%s\n' "$1" "${2:+ — $2}"; }
D=$(mktemp -d); trap 'rm -rf "$D"' EXIT
MEM="$REPO/bin/mem"
"$MEM" --dir "$D" add --help >/dev/null 2>&1; rc=$?
[[ "$rc" -eq 0 && -z "$(ls "$D"/*.md 2>/dev/null)" ]] && ok "add --help stores nothing" || bad "add --help stores nothing" "rc=$rc files=$(ls "$D")"
"$MEM" --dir "$D" add --bogus "text" >/dev/null 2>&1 && bad "unknown option dies" || ok "unknown option dies"
[[ -z "$(ls "$D"/*.md 2>/dev/null)" ]] && ok "unknown option stores nothing" || bad "unknown option stores nothing"
"$MEM" --dir "$D" add --type note -- "--leading-dash text" >/dev/null 2>&1
grep -q -- "--leading-dash text" "$D"/*.md 2>/dev/null && ok "-- allows text starting with a dash" || bad "-- allows text starting with a dash"
# mem done retires a goal: type flips to memory, body gets a dated DONE line, non-goals are refused
"$MEM" --dir "$D" add --type goal "Ship the thing" >/dev/null 2>&1
GID=$(ls "$D" | grep -v leading | sed -E 's/^[0-9-]+_([0-9a-f]+)_.*/\1/' | tail -1)
"$MEM" --dir "$D" done "$GID" "shipped" >/dev/null 2>&1
grep -q "^type: memory" "$D"/*"$GID"*.md && grep -q "^DONE .*: shipped" "$D"/*"$GID"*.md && ok "mem done flips type and dates the body" || bad "mem done flips type and dates the body" "$(cat "$D"/*"$GID"*.md | head -12)"
"$MEM" --dir "$D" done "$GID" >/dev/null 2>&1 && bad "mem done refuses a non-goal" || ok "mem done refuses a non-goal"
printf '\n%d passed, %d failed\n' "$pass" "$fail"; [[ "$fail" -eq 0 ]]
