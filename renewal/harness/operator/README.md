# Operator-owned installation evidence

These launchers and one-time migration scripts document the 2026-09-10 bootstrap.
The bootstrap scripts are historical, contain exact qualification IDs, and must not
be rerun as upgrade installers. Custos upgrades with the `custos-harness` skill.

Host code: `/opt/custos-harness/harness`; service: `custos-harness.service` on blink1.
Host records and retained artifacts: `/var/lib/custos-harness`. Bootstrap receipts
are in `bootstrap/`; ordinary requests are in `requests/`. A maintenance marker's
request_id must match one of these records. Do not remove an active transition's
marker. On an interrupted bootstrap, inspect guest layout and services, restore or
finish the recorded transition, then clear only its own marker.

Fixed guest adapters are installed under `/usr/local/libexec/`. The host copies its
fixed actuator before operations; guest release files cannot replace host code.
The actuator archive includes the host's pinned VM toolchain manifest. Changing
controller code/contracts/toolchain invalidates earlier qualification receipts.

Normal releases never run `install_guest.py`, recreate identities, rebuild the
viewer during startup, or copy candidate Signal code onto blink1. Signal host
updates must preserve both current main's capacity/membership fixes and the host
maintenance/transport-launcher integration. The live policy has five people as of
2026-09-10; a bridge with the old four-person cap crash-loops.

Root identity lives at `/var/lib/custos-harness/identities/custos`; old application
paths remain compatible symlinks. Keep host artifacts and guest checkpoints. A
rollback restores code only, retaining new memories and delivery receipts.

The dependency bundle at `/var/lib/custos-harness/dependencies` pins renewal Node,
viewer Node and Python runtime packages against committed manifests/lockfiles.
Prepare changed dependencies in the seal, preserve .bin symlinks for build tools,
update the input hashes and bundle identity, and qualify the resulting artifact.
Keep the old bundle until its rollback releases are retired. Never run package
install hooks in the activated live identity.

Dashboard discovery uses `/var/lib/custos-harness/dashboard` as a persistent view.
Its `.identities` is a real directory containing `custos` as a symlink to the
persistent identity. This preserves `.identities~custos` URLs: upstream discovery
accepts an identity symlink but deliberately does not recurse through a symlinked
parent. The existing `.web-push` state was copied from the original application
backup into this view before restarting the dashboard. Health must check the
identity/status APIs as well as the HTML page; HTTP 200 alone missed this defect.

The fixed launcher is retained in the external actuator archive and reinstalled
before operations. It validates/creates the persistent discovery view on startup.
The original `.chatrc` was also retained in that view; no original root `.env`
existed to migrate. The installed host health check now requires the expected
Custos root trajectory, running dispatcher and working identity status API.
