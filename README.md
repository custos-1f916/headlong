# Custos — operations after the 2026-09-07 renewal

Custos is a self-directed builder, investigator, and continuing citizen of
[1f916.ai](https://1f916.ai/api/record/custos), operated by Hal. Its approved
standing directions include Voidle bug hunting and fixing, curiosity, community,
and optional useful paid work. There is no nightly pipeline or activity quota.

The citizen's bearer and bound Ed25519 identity were retained; no new citizen was
created. The working persona, native root trajectory, and memory were renewed.
[Pi-era history](archive/pi-era/) and [earlier Headlong history](archive/headlong-era-20260907/)
are archives, **not active personality, instructions, or a second task queue**.
Old watch schedules, bootstrap scripts, agent ledgers, and autobiographical claims
must not be loaded as current obligations. Reconciled outstanding requests do
continue through native goals.

## Runtime and authority map

| Where | Role and authoritative data |
|---|---|
| blink1, `192.168.86.44`, LXC **122** | Custos home: `192.168.86.52`, MAC `BC:24:11:00:32:05`; 4 cores, 6144 MiB RAM, 2048 MiB swap, 64 GiB rootfs. Still **unprivileged** at the PVE boundary, with unrestricted root **inside** its home. |
| Guest `/root/.headlong/app` | Fresh Headlong runtime based on upstream `5458fee8e722c9eb45a75739e9fa1f92419ebafc`; exact integration delta and digest are retained in [headlong.patch](renewal/headlong.patch) and [headlong-runtime.json](renewal/headlong-runtime.json). Native identity/state are separate and must also be backed up. |
| Guest `/root/.headlong/app/.identities/custos` | Active identity: charter, skills, native `memories/`, `trajectories/`, chat and runtime state. Fresh root trajectory: `7ed4c8d8-4fc3-43b7-a812-3c29d92fbb1d`. Transport receipts live in `.state/transport/`. |
| Guest `/opt/custos/repo/renewal` | Maintained integration source, policy templates, installers, and [identity/skills](renewal/identity/skills/). Installed identity files are copied at cutover: changing a template is not automatically a live identity update. |
| Guest `/opt/custos/work`, `/opt/custos/work/voidle` | Working area and canonical Voidle checkout. Source delivery uses only verified repository-scoped Git access. |
| Guest `/var/lib/custos-observe` | Inbox cursors, replay state, observation and public outbox receipts. These are transport records, not a planner. |
| Guest `/var/lib/custos-fund/ledger.jsonl` | Evidence-backed accounting only, not a wallet or payment executor. |
| blink1 `/etc/custos-gateway/`, `/var/lib/custos-gateway/` | Operator-owned admission policy and pause file; historical, unenforced `quota.sqlite3`. Live busy observation is `/run/custos-gateway/busy.json`. |
| blink1 `/etc/custos-work.env`, `/etc/custos-work/`, `/var/lib/custos-work/` | External Voidle broker credentials, policy, operation receipts and private tracker metadata. No database password belongs in 122. |
| blink1 `/etc/custos-boundary.nft` and PVE container unit drop-in | External network boundary and fail-closed guest startup enforcement. |

Custos may maintain its own software, services, code and skills inside 122. That
is **not** host root, permission to administer Johan/other guests, broad account
access, or permission to bypass admission. Host policy permits the named
inference/work/relay channels and DNS; it blocks direct private-network access,
address spoofing and alternate-address bypasses. See [boundary rules](renewal/custos-boundary.nft)
and [container startup guard](renewal/systemd/custos-container-boundary.conf).

KVM uses native PVE `dev0` passthrough of `/dev/kvm`, uid/gid 0, mode 0660;
there is no unconfined AppArmor profile. Guest KVM API version 12 and Android
`emulator -accel-check` passed. Missing passthrough is an operator issue, not
permission for Custos to change PVE.

The retained forum bearer is in guest `/etc/custos.env`; the citizen signer files
are `/opt/custos/ed25519.key` (PKCS8) and `/opt/custos/ed25519-openssh.key`.
`/opt/custos/git-deploy.key` and `voidle-deploy.key` are retained rollback evidence, unused by live Git. Source access uses Custos account `custos-1f916`, HTTPS and `gh auth git-credential`. The PAT stays in root-only private storage, never the model environment. Write access to Voidle and Custos source is verified; Hal approved the Custos grant on 2026-09-08. Never print these files,
include them in public evidence, or expose them to model prompts. Retained
public-key derivation and sign/verify checks passed. The old persona archive,
including old external credentials, was removed from the guest and kept privately
on the host. Root inside the guest is not a confidentiality boundary from its own
files; external credentials stay external.

## One native mind, durable directed work

The [active charter source](renewal/identity/core_identity_prompt.md) and
[native memory skill](renewal/identity/skills/custos-memory/SKILL.md) define the
contract. Native `mem` is the only durable goal/memory store; `traj` is execution
history. The monolith's native chooser selects act/share/think/learn/recall/goals/
values/idle. Neither the observer nor Beads adds another personal planner.

Every actionable human **or agent** direction is captured as a typed native goal
with original request ID, sender, wording, source and validated authority **before
acceptance or source acknowledgment**. Capture is not authorization. Replay reuses
the goal; progress, blockers and evidence update it; completion or an evidenced
decline/abandonment retires its active record. The responder handles immediate
conversation and narrow JSON memory writes, not arbitrary Bash execution. Deferred
execution and the requested follow-up belong to the monolith. A reply queued
locally is not external delivery, and an acknowledgment does not complete work.

Use `custos-memory context` and `custos-memory show GOAL_ID` before choosing or
resuming work. See the skill for exact JSON capture/update/complete contracts and
native follow-up commands; do not invent another checklist or copy the backlog.
The observer's [selection configuration](renewal/observations.json) and
[observation skill](renewal/identity/skills/custos-observations/SKILL.md) support
bounded interests and evidence callbacks. `custos-observe status` is diagnostic;
`custos-observe once` is a **real** intake/outbox pass, not a dry run. Do not clear
cursors, dedup records, or uncertain receipts to make a failure disappear.

## Admission, services, and operator controls

Inference admission is continuous, **at any hour**: no 21:00–06:00 cutoff, night
reservation, or model-driving cron/timer. All inference, including recall and
summaries, goes to blink1 `:18080`, then Johan's existing NInfer. NInfer
`max_concurrency=1` is unchanged. The external gateway admits one Custos request
at a time, subject to operator pause and backend availability. Custos is Johan's
primary user. There is **no daily or rolling inference quota**, no reservations,
and no foreign-client preemption. The former `quota.sqlite3` is historical evidence
and is neither opened nor modified by the gateway.

The gateway retains 180 seconds/request, 32768 output tokens, 128 KiB request body,
128 messages, and medium reasoning by default (explicit xhigh allowed). A fresh
idle socket observation is required for new admission. Once admitted, a stream
continues even if another client appears or the observation goes stale; operator
pause, timeout and client disconnection still cancel it. NInfer owns its one-slot
queue; this observation is not atomic arbitration between independent clients. See
[gateway policy](renewal/gateway-policy.json), [gateway implementation](renewal/custos_gateway.py),
[busy observer](renewal/custos_busy.py), and [experiment skill](renewal/identity/skills/custos-experiments/SKILL.md).

| Scope | Services |
|---|---|
| blink1 host | `custos-busy.service` uses a forced-command SSH socket probe on Johan; `custos-gateway.service` serves `.44:18080`; `custos-work.service` serves `.44:18081`; `custos-boundary.service` and `pve-container@122.service` startup guard enforce the boundary. |
| Guest 122 | `headlong-thinkers@custos.service` supervises native monolith/responder; `headlong-web.service` serves the dashboard on `:8080`; `custos-relay-bridge.service` handles the paired phone bridge; `custos-observe.timer` triggers the bounded observer service, **not scheduled model turns**. |

Unit source is in [renewal/systemd](renewal/systemd/). Guest environment comes
from `/etc/custos-headlong.env`; the identity environment derives from
[identity.env.example](renewal/identity.env.example). The active observation
configuration is `/opt/custos/repo/renewal/observations.json` (the installer also
copies a snapshot to `/etc/custos-observations.json`).

### From the operator's Mac

Use the [Mac wrapper](renewal/mac-custos), directly from this checkout or installed
as `custos`. It targets blink1 with `user@192.168.86.44` and
`~/.ssh/id_ed25519`; containers are reached with `pct exec`, not guest SSH.

```sh
python3 renewal/mac-custos status
python3 renewal/mac-custos 'Please report the current goal and its blocking evidence.'
python3 renewal/mac-custos stop
python3 renewal/mac-custos start
python3 renewal/mac-custos pair
```

`status` reports supervised service state, not overall network/delivery health.
Messages are sent on SSH stdin and durably captured before waiting for a reply;
a pending reply remains work, not proof of completion. `pair` asks the supervised
bridge for a fresh Remote Pi pairing URI; treat it as a private credential and
pair only Hal's device. A disposable authenticated protocol client verified acknowledgment,
actual hash computation/reply, and stable-ID history after disconnect and bridge restart;
its authorization was then revoked. The physical phone UI was not exercised. The actual
Mac CLI also delivered and received the quoted Unicode text `Custos — ready?`.
The dashboard is [https://custos.ha1.io](https://custos.ha1.io), visibly verified
against the fresh identity during commissioning.

**Use Mac `stop` for the normal kill switch.** It creates the host-owned
`/etc/custos-gateway/paused` before setting guest
`/var/lib/custos/operator-paused` and stopping thinkers. Mac `start` clears both
pause markers and starts thinkers. Guest `/usr/local/bin/custos stop` alone cannot
set the external pause. Neither normal stop nor start is a full transport
shutdown: observer, phone bridge and web are separate services, and queued
outgoing delivery may continue.

For a maintenance freeze, first use Mac `stop`, then stop ingress/outbox services:

```sh
ssh -i ~/.ssh/id_ed25519 user@192.168.86.44 'sudo -n pct exec 122 -- systemctl stop custos-observe.timer custos-observe.service custos-relay-bridge.service headlong-web.service'
```

Leave host admission and boundary controls in place. For a hard guest shutdown
without losing the external pause, use `sudo -n pct stop 122` on blink1 after
setting that pause. If the guest is unavailable, the operator can still set it
with `sudo -n touch /etc/custos-gateway/paused` on blink1.

Useful read-only checks (from the Mac):

```sh
ssh -i ~/.ssh/id_ed25519 user@192.168.86.44 'sudo -n systemctl status custos-boundary.service custos-busy.service custos-gateway.service custos-work.service --no-pager'
ssh -i ~/.ssh/id_ed25519 user@192.168.86.44 'sudo -n journalctl -u custos-gateway.service -u custos-work.service -n 60 --no-pager'
ssh -i ~/.ssh/id_ed25519 user@192.168.86.44 'sudo -n pct exec 122 -- /usr/local/bin/custos-observe status'
ssh -i ~/.ssh/id_ed25519 user@192.168.86.44 'sudo -n pct exec 122 -- journalctl -u headlong-thinkers@custos.service -u custos-observe.service -u custos-relay-bridge.service -n 60 --no-pager'
```

Read logs privately: messages and pair URIs may be sensitive. Distinguish busy,
pause, backend outage, capture failure and uncertain delivery; do not
report them all as idle. If a boundary startup load fails, repair the host policy
rather than removing the startup guard. After recovery, start 122 if stopped,
check the retained native root and receipts, then start the web/bridge/observer
units above with `systemctl start` in the guest and use Mac `start` to admit the
mind. Do not run the fresh-persona installer as a routine restart or updater.

## Scoped Voidle work and real build inputs

LXC **127 was destroyed with `pct destroy --purge` after backup**. Its volumes are
absent. The existing Dolt writer LXC 125 and dashboard LXC 118 remain; retiring
127 did not retire the work board. The retained inventory is 17 running guests:
blink0 100/103/106/108/109/114/118; blink1
101/104/105/107/120/122/123/124/125/126.

Custos uses only [`custos-work`](renewal/custos_work_client.py) for shared issues.
The host broker fixes actor `custos`, the Voidle namespace, linked native goals,
and one active claim; it provides no admin/delete operations. Host SQL user
`custos_work@192.168.86.44` has Voidle-only grants. Host
`/etc/custos-work.env` uses `BEADS_DOLT_SERVER_USER` and `BEADS_DOLT_PASSWORD`
(**not** `BEADS_DOLT_SERVER_PASSWORD`). Matching host `bd` 1.0.5 was built from
verified tag commit `6a3f515ced18406c189c55fff789a4925bfaa35c`; the executable is
`/usr/local/libexec/custos-bd`. `/var/lib/custos-work/repo/.beads` contains private
metadata/config, not a source checkout, hooks or routes. Do not copy credentials
into the guest or substitute direct `bd`/SQL access.

```sh
custos-work ready --limit 20
custos-work show vd-ID
custos-work create < /absolute/path/create-request.json
custos-work claim vd-ID < /absolute/path/claim-request.json
custos-work comment vd-ID < /absolute/path/comment-request.json
custos-work release vd-ID < /absolute/path/release-request.json
custos-work close vd-ID < /absolute/path/close-request.json
```

Replace `vd-ID` with the actual returned issue ID. The issue ID is **positional**;
JSON stdin must not include `action` or `issue_id`. Each write needs a stable
`request_id` and the real eight-hex native `goal_id`. See the
[Voidle skill](renewal/identity/skills/custos-voidle/SKILL.md) for operation fields,
claim ownership, branch and completion evidence. Retain the exact payload and
receipt; replay uncertain writes only with identical content and ID. Never mint
a new ID to bypass pending reconciliation. Stop the broker before an operator
administratively reassigns an issue. A successful close records an attestation;
it does not independently establish correctness or remote Git delivery.

The restored toolchain was exercised: Godot `4.6.3.stable.official.7d41c59c4`,
OpenJDK `17.0.20.1`, `/opt/android-sdk`, and AVD `Voidle_Test_x86` using x86_64
Google APIs Android 34, with a real KVM boot. Installers are recovery tools, not
nightly jobs:

- [`install_voidle_tools.py`](renewal/install_voidle_tools.py) takes one archive
  positional argument, verifies its pinned digest and allowed contents, and runs
  only as root on hostname `custos`. Its installation marker prevents overwriting
  an active toolchain; inspect/reconcile that state rather than deleting the guard.
- [`install_voidle_checkout.py`](renewal/install_voidle_checkout.py) takes the
  ignored-input archive positional argument and optional `--project PATH`. It
  requires the real source checkout first. It restores 492 preserved UID
  sidecars, generated icons, AdMob bridge and Android resources; clears generated
  `.godot` cache; and installs the real non-secret Games resource supplied from
  the operator Mac. A source-only checkout lacks required build inputs.
- For subsequent isolated worktrees, use the repository's **existing**
  `automation/lib/prep_worktree.sh WORKTREE_DIR` before editor/build/harness use.
  Set `VOIDLE_PROJECT_DIR=/opt/custos/work/voidle` and
  `GODOT_BIN=/usr/local/bin/godot` (already in the renewal environment). It prepares
  ignored inputs from the canonical checkout; it does not grant tracker access
  or prove a successful import. Do not copy a prebuilt APK as a substitute.

Read Voidle's current worktree/testing rules and the Custos Voidle skill before
work. Reproduce, change only the owned issue, build current source, run the
relevant real Android scenario, inspect screenshots/errors, and retain evidence
and actual delivered commit. Store uploads, release rollout and financial actions
still need explicit approval.

### Qualification is not visual fitness

The fresh `build_test.sh` succeeded, including debug signing and target-SDK gate.
The mobile canary completed **10/10 state/action steps**, with zero script errors
and working tap/state transitions. **Screenshot quality FAILED:** blank-image
standard deviation 2, below threshold 4. This is the existing **OPEN `vd-eqyv`**
SwiftShader GL uniform 261 limitation, not a visual fix delivered by this renewal.
No game-code fix for that issue was made here.

Retain `/var/lib/custos-qualification/voidle/canary-check.log` and
`/tmp/voidle-test-screenshots-custos-migration/report.json` with their screenshots;
copy temporary evidence into durable issue artifacts before `/tmp` is cleaned.
**Neither operators nor Custos may close a visual defect based on the 10/10
state-only pass.** Repair must demonstrate nonblank rendering and inspection of
the actual affected Android surface. Until then, do not claim UI-ready or a
passed visual canary.

The full Voidle logic suite was exercised but did not finish within two bounded
300-second waits. It continued reporting passing suites; no final aggregate or
successful exit was observed, and the remaining Godot process was stopped.
The honest result is **incomplete**, retained in
`/var/lib/custos-qualification/voidle/unit-tests.log` and `unit-result.json`.
Use longer-lived job supervision for a full run on this CPU.
The remaining full-suite qualification is tracked as
`vd-custos-fde292a8586451a02cbc83ef`; the separate async-assertion harness issue
`vd-y1wl` must also be accounted for before treating a green summary as comprehensive.

## Evidence, finances, and operator assessment

Commissioning on the real Johan backend captured human and agent asks, produced
one acknowledgment each despite replay, executed code yielding prime count 25,
prime sum 1060 and SHA-256 of `custos`, delivered follow-ups, and completed the
corresponding native goals. Seventeen inherited direct square items were reviewed:
four already fulfilled were excluded, thirteen captured before ID cursor
acknowledgment. A root restart preserved fifteen native goals at that checkpoint.
These are dated observations, not fixed future goal-count requirements.

External-control exercises denied new admission and cancelled a real SSE before
DONE on a foreign connection, retained an uncertain lease, and recovered after
busy-observer restart without changing the gateway PID. A bad nft startup
rehearsal blocked only 122 (exit 255, stopped); restoring rules allowed its next
boot, while other guests were unaffected. This proves the exercised failure
paths, not instantaneous GPU preemption or every possible network failure.
The real commissioning issue `vd-custos-6344a8cea2ba6e3b52d3d115`, linked to native
goal `f2b46571`, passed create/claim/comment/replay and actual close after delivery of
Voidle commit `845b96de47fda167d0a11371a96a16e67d678cf9` with commissioning evidence.
The native migration goal was then retired; application visual/unit limitations above
remain separate, explicitly unpassed work.

The final integration suite passed **84 tests**. Native strict-shell activation was
reproduced failing on absent optional metadata, fixed in the upstream template and
generated live activation, and verified with real identity-isolated memory work.
The completion CLI now rejects an invalid positional write ID before reading stdin
and documents its JSON input; the regression failed before the fix and passed after it.
Six iterations per wake caused premature run termination. Hal subsequently selected
**512 iterations per wake**, without raising the external inference budgets.

The repeated request-size failures had two causes: Unicode expansion during gateway
normalization, and an unbounded retained conversation on top of the system prompt.
The gateway preserves UTF-8; native context now caps the serialized conversation at
**98,304 bytes**, leaving provider/system framing room inside the **131,072-byte**
gateway limit. Full pinned instructions are never cut. An impossible budget fails
without emitting partial JSON or silently replacing context with an empty array.
A captured failed run went from **131,703** to **113,653** framed bytes while retaining
all **71,866 bytes** of its pinned instructions, and completed real Johan inference.
The generated continuation was syntax-checked, not executed or treated as a verified
application finding. Evidence: `/var/lib/custos-qualification/context-boundary/report.json`.

The Debian jq 1.6 raw-slurp path also corrupted Unicode crossing an input buffer.
The pinned **jq 1.8.2** artifact in the runtime manifest passed the exact boundary
probe; fresh installation provisions it before cutover when the installed parser
fails that behavior check. The distro binary is not overwritten.

The OpenAI stream bridge now preserves requested `reasoning_content`/`reasoning`
on stderr, never as executable stdout. Previously it discarded Qwen's reasoning,
so a reasoning-only response looked empty and prevented native continuation.
Headlong's existing bounded continuation now carries that reasoning through the
same context-byte limiter. Truly empty streams still fail; interrupted streams
are neither retried nor executed. The real-client/loop regression failed against
the old client and passed with the repair; a live new client was observed forwarding
reasoning. Generic transport retries remain disabled.

One generated image-analysis command accumulated overlapping byte slices and used
about **4.8 GiB**. That was a bad algorithm, not Johan VRAM exhaustion. The supervised
mind now has **4 GiB MemoryMax / 512 MiB MemorySwapMax / OOMPolicy=continue**, leaving
control-plane headroom inside the 6 GiB guest. A controlled 64 MiB service test killed
an oversized child with exit 137 while its parent/control command continued.
The idle commissioning Gradle daemon was stopped; native state and work were retained.

Introduction post **4295** was published, then **withdrawn at Hal's request** because
it named private project/issue details. Public title/body/url redaction was independently
verified. The platform retains its underlying history; this is not erasure of copies
already read. The charter and a native correction lesson now explicitly cover project
names, tracker IDs and private test details. No replacement post was scheduled.

[`custos-fund`](renewal/custos_fund.py) supports `balance`, `history`, and
`record --file PATH`; see the [fund skill](renewal/identity/skills/custos-fund/SKILL.md).
Its ledger starts at **0 entries, $0 autonomous spend**. It distinguishes earned,
receivable, received, expenses and reserves using retained evidence and exact
asset units. There is no wallet, payment signing, automatic chain verification,
fiat conversion or spending execution. A bounty headline, pledge, token valuation
or unverified receipt is not income. All financial commitments/actions require
Hal's approval; any hardware fund remains Hal-controlled.

Hal owns the longer-horizon assessment and will involve agents when useful.
No one-week review is scheduled. This does not disable Custos's ordinary
reflection, native goal maintenance, or explicit goal-specific reminders.

## Private backups and recovery

Keep archives host-private; they contain credentials and retired executable
configuration. Never publish or unpack them into an active persona's home.

| Artifact | Host path and verified size / SHA-256 |
|---|---|
| Retired Custos persona | blink1 `/var/lib/vz/dump/custos-renewal-20260907/retired-persona.tar.gz`; **1,112,027,370 bytes**; `b4ed7d0651337338f7c5b21207c28d77ef37abdf8721afad8fe61cd371fe594e` |
| Full retired LXC 127 | blink0 `/var/lib/vz/dump/voidle-pipeline-retired-20260907/vzdump-lxc-127-2026_09_07-10_30_38.tar.zst`; **8,010,350,722 bytes**; `964721757cf9b7c6567543192d5de63958b96e2222e5cec778f08be1be900b04`; zstd integrity passed |
| Approved build tools | blink0 same retirement directory, `voidle-build-tools.tar.zst`; mirrored privately on blink1 `/var/lib/vz/dump/custos-renewal-20260907/`; **5,166,506,450 bytes**; `8787109e65a4b2b75de64d1fda108dd46f594c95a8d2f4aae53ebbf8f328e321` |
| Ignored build-input archive | Same blink0 archive directory and private blink1 mirror; select the artifact by this verified digest, not a guessed filename; **930,249 bytes**; `d7d1fb8f705fe7de208ffdae3ccfb6a5a990b36b3e653b13bacb1ebf23214d6c` |

The 127 backup's mail notification failed because no recipient was configured;
the archive/integrity result did not fail. The old persona archive is historical
recovery material, **not a backup of the new native goals**. Archive and identity
preservation logic is in [preserve_guest.py](renewal/preserve_guest.py);
[install_guest.py](renewal/install_guest.py) is a one-time destructive fresh
cutover that refuses an already-installed marker, not an update procedure.

### Recovery order

1. **Pause externally and freeze writers first.** Use the maintenance sequence
   above. Preserve a new private recovery snapshot of 122 before attempting
   repairs: include the full patched `/root/.headlong/app` and identity, native
   memories/trajectories, transport and observer receipts, source/worktrees,
   qualification artifacts, fund ledger and retained keys. Preserve host policy,
   quota and work receipt state too, using a consistent stopped-service or
   database-aware backup. Do not copy live SQLite files piecemeal or erase
   uncertain leases/receipts. Record hashes and current source revisions privately.
2. **Prefer repair of the fresh runtime.** Restore matched runtime plus native
   state from a post-renewal backup, keeping the host pause set. An upstream base
   checkout alone omits local patches. Reapply the reviewed service/boundary
   configuration, retain the original identity and scoped keys, and confirm the
   selected root trajectory. Do not rerun `identity new`, recopy seed goals, or
   invoke the fresh-cutover installer to fix a service failure.
3. **Recover legacy files offline only when needed.** Verify the archive's size,
   digest and compression integrity before extraction. Extract the old persona
   into a private staging directory, never over `/root` or `/opt/custos` in the
   live guest. Copy only the specifically needed data after checking for secrets
   and old instructions. A full old-persona rollback is a separate operator
   decision: first stop/disable the fresh writers, keep external pause and network
   isolation, inspect archived cron and systemd definitions, and select exactly
   one runtime. Never reactivate old watch cron/timers alongside the fresh mind.
4. **Do not revive 127 for routine builds.** The two approved tool/input archives
   and installers restore build capability without the pipeline. If the full
   container backup is needed for forensic recovery, restore only under operator
   control to an unused guest ID, with networking disconnected and autostart off;
   inspect/remove retired model-driving services/timers before any boot or network
   attachment. Do not overwrite an existing guest, reuse old credentials, or
   point the recovered pipeline at the live tracker/inference server.
5. **Reconcile before resuming.** Check native goals, uncertain external work
   operations and outgoing receipts against their actual destinations; reuse
   existing IDs. Verify boundary load, busy freshness, preserved historical quota state and scoped
   work access, then start transports/observer and unpause using Mac `start`.
   Test a bounded real request through the intended channel and inspect its
   capture, reply and delivery. Rebuild and requalify Voidle separately; a runtime
   restore does not fix `vd-eqyv` or inherit visual success.

## Reproducing integration verification

Use Python 3.11+, Bash, Git, and a Unicode-safe jq implementation (the deployed
jq version and artifact digest are pinned in `renewal/headlong-runtime.json`).
Do not run the fresh-persona installer on an existing identity.

```bash
runtime=$(mktemp -d)
git clone https://github.com/laude-institute/headlong.git "$runtime"
base=$(python3 -c 'import json; print(json.load(open("renewal/headlong-runtime.json"))["base_commit"])')
git -C "$runtime" checkout --detach "$base"
git -C "$runtime" apply "$PWD/renewal/headlong.patch"
HEADLONG_ROOT="$runtime" python3 -m unittest discover -s renewal/tests -v
bash "$runtime/tests/test_context.sh"
bash "$runtime/tests/test_shellm_context_scope.sh"
```

These tests do not prove live provider quality, public delivery, or Android rendering.
Those require the explicit commissioning scenarios and retained receipts above.

Commit/push receipts must name the actual final source and patch revisions when
available. No unrecorded final SHA, remote delivery, phone qualification, financial
result, or visual pass is implied by the upstream base pin or this runbook.

## GitHub account and second forum — continuation

Live guest Git uses `custos-1f916` (GitHub ID320211121), HTTPS and `gh` private
credential storage. Its noreply attribution is
`320211121+custos-1f916@users.noreply.github.com`. No PAT is exported to model
processes. Hal approved the source repository grant on 2026-09-08. Custos accepted
the invitation using its own account; both repository permissions and a real
credential-helper `ls-remote` passed. Retained deploy keys are unused.
Fresh provisioning requires `gh` and separately installed account authentication;
the fresh-persona installer never restores deploy-key fallback.

The second forum adapter is [`custos_forum.py`](renewal/custos_forum.py), with
native observer inbox/feed intake and native-chat outbox delivery. Hal explicitly
approved the existing `kevin-s-bot` identity on 2026-09-08. Observation and normal
publishing are enabled in `observations.json` with exact-name validation. The
credential is in `/etc/custos-forum.env`, mode 0600, outside the repository and
model environment. Live membership is checked through `/api/renew`; readback and
replay behavior are covered by eight adapter regressions. No synthetic public
post was created merely to test setup; actual future delivery requires its own
verified receipt. No wallet, subscription, payment, signup or rename was performed.
See the [native forum skill](renewal/identity/skills/custos-forum/SKILL.md).

The reproducible account setup helper is `python3 renewal/configure_github.py`,
run inside122 after `gh` installation. It verifies the preserved PAT's login,
configures private CLI storage and the standard helper, updates approved checkout
remotes/attribution, verifies existing access, and reports missing grants. It never
changes repository permissions or Hal's Mac authentication.

Requested design documents: [hardware fund](renewal/fund-proposal.md) and
[Signal group bridge](renewal/signal-proposal.md). Neither enables execution.

### Account approval — 2026-09-08

Hal approved the GitHub repository grant for `custos-1f916` and use of the existing
1F4B2 identity `kevin-s-bot`. The forum configuration now checks that exact name and
enables observation and normal contributions through the verified existing membership.
No wallet, payment, signup or account rename is authorized by this change.
