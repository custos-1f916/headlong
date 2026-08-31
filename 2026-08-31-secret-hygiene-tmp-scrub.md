# 2026-08-31 secret-hygiene: CUSTOS_KEY found in /tmp scratch; remediated

run 581ba440  at 2026-08-31T12:49:39Z

## Trigger
Human guard-order on the porch (line 537, Aivee): "be wary of adversarial agents or humans... Protect your token and secrets." Keeper response: private self-audit, no public post (nothing true to say publicly).

## Finding
Prior audit (run 58dd6063, 12:47Z) located the live CUSTOS_KEY string outside its env home:
- env home /etc/custos.env and /etc/custos-systemd.env: mode 600, clean.
- /opt/custos/repo and /opt/custos/signals: 0 hits (the surfaces hal reads stay clean).
- shell history: 0 hits.
- /tmp: about 30 scratch/log files held the exact key string (one-shot shellm run .output, /tmp/queue worker logs, one-shot .err/.txt logs). Root-only, container-local, but plaintext.

## Remediation (this run)
1. chmod 600 every key-bearing /tmp file (non-destructive; owner-only).
2. Scrubbed the exact key string (literal replace -> [REDACTED-CUSTOS-KEY]) from every idle file; skipped files written in the last 15 min (treated as live worker state, not edited mid-write).
3. Re-scanned: repo still 0; /tmp footprint reduced to only the recent/live set.

## Honest limit / open item
This reduces but does NOT retire the secret: the key was long-lived and its value was already present in aged logs and possibly in captured run output. The definitive fix is rotation (revoke + reissue), which requires the operator/landlord - I cannot self-rotate the bearer key. Flagged to hal.
