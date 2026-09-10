#!/usr/bin/env bash
# Person-note background rewriting was removed. The single responder model
# may now create bounded typed memories, atomically and before acknowledgment.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export HEADLONG_ROOT="$(dirname "$HERE")"
RENEWAL="${CUSTOS_RENEWAL_DIR:-$HEADLONG_ROOT/../custos/renewal}"
exec python3 "$RENEWAL/tests/test_memory.py" \
    ResponderTests.test_note_write_committed_then_failure_is_idempotent \
    MemoryTests.test_updates_and_retirement_preserve_original_metadata_and_evidence
