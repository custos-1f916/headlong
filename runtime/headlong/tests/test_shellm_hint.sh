#!/usr/bin/env bash
# tests/test_shellm_hint.sh — bin/shellm-hint did-you-mean lines
#
# Usage: tests/test_shellm_hint.sh
#
# A sampled model slips one token in a rare name and cannot see that it did;
# "No such file or directory" then reads as the filesystem changing under it
# (Custos, 2026-09-11). shellm-hint reads a block's output and names the
# sibling that exists within two edits. Pure function of the output text and
# the filesystem; no model, no side effects.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
HINT="$REPO/bin/shellm-hint"
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL %s%s\n' "$1" "${2:+ — $2}"; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/opt/custos/work/pdf-signal-bridge/renewal" "$WORK/bin"
printf '#!/bin/sh\n' > "$WORK/bin/custos-memory"; chmod +x "$WORK/bin/custos-memory"

# A path whose first missing component is one edit from a real sibling.
out=$(printf "ls: cannot access '%s/opt/costos/work/pdf-signal-bridge': No such file or directory\n" "$WORK" | "$HINT")
grep -qF "did you mean '$WORK/opt/custos/work/pdf-signal-bridge'" <<<"$out" && ok "path slip → the existing sibling, rest of the path kept" || bad "path slip" "$out"

# The tail of the path is kept even when it does not exist yet.
out=$(printf "sed: can't read %s/opt/costos/work/pdf-signal-bridge/renewal/custos_pdfs.py: No such file or directory\n" "$WORK" | "$HINT")
grep -qF "did you mean '$WORK/opt/custos/work/pdf-signal-bridge/renewal/custos_pdfs.py'" <<<"$out" && ok "sed's phrasing is parsed and the tail is preserved" || bad "sed phrasing" "$out"

# cd's phrasing.
out=$(printf "bash: line 12: cd: %s/opt/costos/work: No such file or directory\n" "$WORK" | "$HINT")
grep -qF "did you mean '$WORK/opt/custos/work'" <<<"$out" && ok "cd's phrasing is parsed" || bad "cd phrasing" "$out"

# A genuinely absent name with no near sibling gets no hint.
out=$(printf "ls: cannot access '%s/opt/nothinglikeit/x': No such file or directory\n" "$WORK" | "$HINT")
[[ -z "$out" ]] && ok "no near sibling → no hint" || bad "no near sibling" "$out"

# Very short components are never guessed.
mkdir -p "$WORK/opt/ab"
out=$(printf "ls: cannot access '%s/opt/ac': No such file or directory\n" "$WORK" | "$HINT")
[[ -z "$out" ]] && ok "short names are not guessed" || bad "short names" "$out"

# A command slip against PATH.
out=$(printf 'bash: line 3: costos-memory: command not found\n' | PATH="$WORK/bin:/usr/bin:/bin" "$HINT")
grep -qF "command 'costos-memory' not found; did you mean 'custos-memory'" <<<"$out" && ok "command slip → the PATH sibling" || bad "command slip" "$out"

# A command that exists is not a slip (the failure was something else).
out=$(printf 'bash: line 3: custos-memory: command not found\n' | PATH="$WORK/bin:/usr/bin:/bin" "$HINT")
[[ -z "$out" ]] && ok "an existing command gets no hint" || bad "existing command" "$out"

# Relative paths resolve against SHELLM_HINT_CWD; duplicates collapse; at most five lines.
out=$(printf "ls: cannot access 'costos/work': No such file or directory\nls: cannot access 'costos/work': No such file or directory\n" | SHELLM_HINT_CWD="$WORK/opt" "$HINT")
[[ $(grep -c 'did you mean' <<<"$out") -eq 1 ]] && grep -qF "'$WORK/opt/custos/work'" <<<"$out" && ok "relative path via SHELLM_HINT_CWD, deduplicated" || bad "relative + dedupe" "$out"

# Empty input, and output with no failure text, produce nothing.
out=$(printf '' | "$HINT"); [[ -z "$out" ]] && ok "empty input → nothing" || bad "empty input" "$out"
out=$(printf 'all good\n' | "$HINT"); [[ -z "$out" ]] && ok "clean output → nothing" || bad "clean output" "$out"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
