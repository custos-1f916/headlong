# September 25 audit repairs

The gateway now admits its configured Tycho/OpenRouter display aliases and
normalizes local requests to the canonical model under the existing admission
policy. `/brain` separates `request_model` from `effective_model`, exposes a
stable code/policy configuration identifier, and reports per-route HTTP completion
and failure counters since startup. Structured journal outcomes survive restart;
a completed transfer does not certify model content or agent progress.

The real streaming client retains HTTP status and stops retrying unchanged 4xx
requests (except 408/429). Transient failures still retry only before output.
Responder diagnostics retain allowlisted error codes, status and exit code, never
arbitrary provider stderr. A deterministic failure parks the existing request;
a verified route/configuration change re-arms bounded recovery. Monolith failure
backoff applies to reactive failures too.

`shellm-final` accepts literal stdin. A correctness guard rejects the observed
double-quoted FINAL dollar-number mistake before executing that block. This is a
narrow guard, not a full shell parser. `custos-evidence receipt-text` substitutes
JSON receipt fields into a literal template and writes source/text hash provenance.
It verifies copying/arithmetic, not the source's authenticity or surrounding prose.

Simple search-only blocks default to 60 seconds, retaining partial output and
the durable pre-execution record. Builds/unknown mixed scripts retain the outer
2400-second limit. `# shellm: timeout=N` explicitly selects a bounded budget.
The prompt asks for scoped separate searches and literal reports. Optional PR
observations and brainstorming remain unchanged; no quotas or timers are added.

Guest changes use ordinary exact-commit build/qualify/deploy. Gateway/brain code
in CT131 is separately backed up and installed under a guest maintenance drain;
no inference model, power, networking, credential or admission-policy changes.
Production qualification must exercise actual resolver + streaming/nonstreaming
profiles on Tycho and confirm the normal Johan route afterward.
