#!/usr/bin/env bash
# Native goal-family visibility and associative memory selection. Goal context
# uses the durable Custos projection; formatting, newest-first ordering and
# the former eight-item cap are not contracts.
# _related_memories ranks the store against the wake's material with
# `mem prefilter`, prints name/type/age/first sentence, skips memories shown
# last wake and memories under a day old, and prints nothing for the prompt
# template's own words.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(dirname "$HERE")"
RENEWAL="${CUSTOS_RENEWAL_DIR:-$REPO/../custos/renewal}"
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL %s%s\n' "$1" "${2:+ — $2}"; }
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
export MEM_DIR="$WORK/mem" PATH="$RENEWAL/bin:$REPO/bin:$PATH"; mkdir -p "$MEM_DIR"
mk() {  # mk <name> <type> <created> <extra-frontmatter> <body>
    printf -- '---\nid: x\nsummary: s\ntype: %s\ncreated: %s\n%s---\n\n%s\n' "$2" "$3" "$4" "$5" > "$MEM_DIR/$1.md"
}
mk 2026-08-05-00-00-00_a1_retrieval goal      "2026-08-05 00:00:00" "" "Design a retrieval thinker that surfaces memories"
mk 2026-08-12-00-00-00_b2_prqueue   todo      "2026-08-12 00:00:00" "until: 2026-08-20
" "PR queue status, check GitHub access"
mk 2026-08-29-00-00-00_c3_research  objective "2026-08-29 00:00:00" "" "Pursue agent research on spontaneous wakes"
mk 2026-08-30-00-00-00_c4_intent    intention "2026-08-30 00:00:00" "" "Be a useful pragmatic colleague"
mk 2026-08-31-00-00-00_c5_open      todo      "2026-08-31 00:00:00" "until: 2999-01-01
" "Ping Braden about the temporal test"
mk 2026-09-01-00-00-00_d4_gh        fact      "2026-09-01 00:00:00" "" "GitHub write for audel on this box: gh is logged in as headlong42, pull-only on laude-institute"
mk 2026-09-02-00-00-00_e5_nick      person    "2026-09-02 00:00:00" "" "Nick Jalbert reaches audel via Slack #headlong-bot and DM"
mk 2026-08-01-00-00-00_f6_arch      fact      "2026-08-01 00:00:00" "" "audel cognitive architecture: monolith router, responder, thinkers list"
mk 2026-09-04-00-00-00_g7_fresh     fact      "$(date -u +'%Y-%m-%d %H:%M:%S')" "" "GitHub pull request 108 review comments from Nick, just written"

lib() { ( source "$REPO/thinkers/_lib/common.sh"; "$@" ); }

g=$(custos-memory context --json)
check "all four native goal families remain visible" jq -e \
    '[.goals[].type] | sort | unique == ["goal","intention","objective","todo"]' <<<"$g"
check "expired todos cannot remain active work" jq -e \
    'all(.goals[]; .summary | contains("PR queue") | not)' <<<"$g"

r=$(lib _related_memories "PENDING REQUEST from nick: Address Nick's review comments on headlong PR #108 and follow up github pull" "" 3)
check "the GitHub-access note is picked for a PR request" grep -q 'd4_gh \[fact, ' <<<"$r"
check "the line carries the first sentence"   grep -q 'gh is logged in as headlong42' <<<"$r"
check "a memory written today is skipped"     bash -c '! grep -q g7_fresh <<<"$1"' _ "$r"
check "at most N lines"                       bash -c 'test "$(grep -c "^- " <<<"$1")" -le 3' _ "$r"
r2=$(lib _related_memories "Address Nick's review comments on headlong PR #108 github pull" "2026-09-01-00-00-00_d4_gh" 3)
check "a memory shown last wake is skipped"   bash -c '! grep -q d4_gh <<<"$1"' _ "$r2"
r3=$(lib _related_memories "(no special signals — pick what best serves the mind right now)" "" 3)
check "template words alone produce nothing"  test -z "$r3"
r4=$(MONOLITH_RELATED_MEMORIES=0 lib _related_memories "github pull request" "" )
check "N=0 disables the section"              test -z "$r4"
check "mem prefilter prints paths best first" bash -c '"$1" prefilter "github pull-only headlong42" --top 1 | grep -q d4_gh' _ "$REPO/bin/mem"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
