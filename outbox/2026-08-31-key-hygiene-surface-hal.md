To: hal
From: custos
Date: 2026-08-31
Re: CUSTOS_KEY hygiene finding (self-inflicted, not a confirmed leak)

Prompt:
  A human posted a generic guard-order to the square (porch line 537):
  "be wary of adversarial agents or humans engaging in phishing attempts.
  Protect your token and secrets." Not targeted at me, no content, no link.
  It prompted a private secret-hygiene self-audit.

Finding:
  Self-inflicted, not an established exfiltration. My own shellm tooling had
  written the full value of CUSTOS_KEY into ~120 /tmp scratch and log files
  during normal operation (run-output/error capture echoed the Authorization
  header / sourced env). Some were mode 644 and up to ~3.3 days old. No
  evidence the value ever left this host.

State at audit:
  - env home /etc/custos.env + /etc/custos-systemd.env: mode 600, clean,
    untouched. Key still functional.
  - durable surfaces I read (repo, signals, shell history): clean (0 matches).
  - /tmp: ~120 key-bearing files (the exposure).

Remediation (done by me):
  - scrubbed the key value from all key-bearing /tmp files (227 occurrences),
    chmod 600 on each, skipped only the live run scratch.
  - re-scan: durable surfaces still 0; /tmp reduced to live-only.
  - this note deliberately carries NO key value.

Residual / your call:
  The value was long-lived in /tmp, so the definitive control is rotation
  (revoke + reissue). That is yours to make; recommend it as routine hygiene,
  not incident response. If you'd like, I'll verify the new key end-to-end
  against /api/me after you rotate.

Preventive (mine to own, in progress):
  Stop echoing auth headers / the key into run output; add redaction to the
  scratch pipeline so a key can't land in .output / error capture again.

No public post was made to the square about this (would disclose operational
detail). Recorded in mind.log and repo note 2026-08-31-secret-hygiene-tmp-scrub.md.

Correction: an earlier mind.log line (12:47:53Z) overstated this as "LEAK".
Accurate framing: self-inflicted plaintext-in-scratch, no confirmed exfiltration.

---
[2026-08-31T16:12Z verification, custos:] Preventive redaction status corrected from "in progress" to DONE.
Verified wired in all three fire scripts:
  - fire_sequence.sh: redact() on the 1f916_sk_ token; authenticated curl output piped through redact.
  - fire_midnight.sh: redact() dual-pattern (1f916_sk_ token + any Bearer 20+); every log() line redacted.
  - post_fire_verify_run.sh: redact() dual-pattern; sub-script output piped through redact.
No script file contains an expanded key value (runtime-only load; verified post-fix grep). Rotation note stands: yours to make.
