#!/usr/bin/env bash
# precheck_block.sh — scan a candidate bash block (stdin) for the corruption
# patterns that trip the harness. Exit 0 clean, non-zero dirty.
#
# Checks:
#   1. Any ``` fence line inside the block. The harness only accepts ONE
#      ```bash-fenced block; a second fence closes it early and the rest
#      becomes prose passed to `bash -c`, which chokes on the first bare
#      paren. This is the single most common self-inflicted failure.
#   2. Function-call / tool-call markup: a line starting with the tool_
#      or <tool_call> marker. The harness has no function-calling API.
#
# Usage: cat my_candidate_block.sh | tools/precheck_block.sh
#       echo "candidate..." | tools/precheck_block.sh

set +e
FAIL=0
REASONS=""

n_fence=$(grep -c '```' /dev/stdin 2>/dev/null)
if [ "${n_fence:-0}" -gt 0 ]; then
  FAIL=1
  REASONS="${REASONS} [fence: ${n_fence} fence marker(s) in block]"
fi

n_tool=$(grep -cE '^[[:space:]]*(tool_|tool_call|<tool)' /dev/stdin 2>/dev/null)
if [ "${n_tool:-0}" -gt 0 ]; then
  FAIL=1
  REASONS="${REASONS} [tool-markup: ${n_tool} function-call line(s)]"
fi

if [ "$FAIL" -eq 1 ]; then
  echo "FAIL:${REASONS}" >&2
  exit 1
fi
echo "OK"
