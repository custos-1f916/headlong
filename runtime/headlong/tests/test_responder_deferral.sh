#!/usr/bin/env bash
# Native handoff, no-reply, crash replay and capture reconciliation regressions.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export HEADLONG_ROOT="$(dirname "$HERE")"
RENEWAL="${CUSTOS_RENEWAL_DIR:-$HEADLONG_ROOT/../custos/renewal}"
exec python3 "$RENEWAL/tests/test_memory.py" \
    ResponderTests.test_memory_before_ack_and_native_followup_does_not_complete_goal \
    ResponderTests.test_reply_committed_then_failed_replays_without_model_or_duplicate \
    ResponderTests.test_concurrent_duplicate_responses_send_once \
    ResponderTests.test_invalid_model_outputs_never_ack_or_retire_original \
    ResponderTests.test_no_reply_is_durable_and_never_reinfers \
    ResponderTests.test_legacy_defer_delivers_only_human_text \
    ResponderTests.test_context_recovers_capture_failure_from_old_native_ingress
