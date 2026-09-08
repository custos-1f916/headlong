# Proactive Signal and Automata deployment (2026-09-08)

Hal approved independent DMs to every allowlisted Signal contact, starting topics
in the approved group, and deploying Custos's Automata changes to LXC126.

The guest `custos-actions` CLI connects to the fixed host endpoint
192.168.86.44:18082/v1/actions. Host source-IP validation and the existing bridge
anti-spoofing firewall admit LXC122 only; add 18082 to both existing approved-port
sets in `custos-boundary.nft`. No guest Proxmox key, arbitrary command, target LXC,
filesystem path, RPC endpoint or deployment credential is accepted.

The host unit `custos-actions.service` runs
`/opt/custos-signal/bridge/custos_actions.py`, sharing the already installed
`custos_signal.py`, `custos_images.py` and `custos_reactions.py` imports. Its durable
queue/receipts are `/var/lib/custos-actions/actions.sqlite`, root-only WAL SQLite.
The Signal bridge alone consumes signal-send entries; a separate serial worker
in custos-actions consumes Automata deployments/rollbacks. Request IDs are stable
and conflicting payloads are rejected. Queue state is delivery bookkeeping, not
another goal store. Connection loss after sending/deployment begins leaves an
uncertain outcome; neither service blindly retries it after restart.

Signal uses the existing private account and host policy. `signal-contacts` lists
current labels/opaque targets; `signal-send` accepts `{request_id,target,message}`.
The host resolves a label once and rechecks the stored destination at send time.
Group membership/expiration are checked through signal-cli. The bridge retains
operator pause, shared per-conversation pacing and native reaction/image intake.
Proactive messages need no synthetic incoming request or native directed goal.
Successful Signal receipts mean submitted/accepted, not read by the recipient.
Restart the Signal bridge after adding the module to pick up queue consumption.

Automata's current app is `/srv/terrarium-pl`, service `terrarium`, port8080 on
LXC126. Its six live source/design/test files were recovered into `particle-life/`
in `collettiquette/automata`; PR1 merged at
`5f4f2412c11f272d342d94d0bb4c525ace37f3c6`. The original default branch's app,
LXC110 deploy script and Tycho persona protocol are historical. Custos's checkout
is `/opt/custos/work/repos/collettiquette/automata` using its own account.

Install the operator-owned `custos_automata_deploy.py` as
`/usr/local/libexec/custos-automata-deploy.py` inside126, create root-only
`/var/lib/custos-automata-incoming`, then run its `initialize` action once. It stops
the app briefly, preserves the original tree and service unit, puts saved species
in `/var/lib/automata/species`, links a legacy release and restarts/health-checks.
It refuses unexpected initial data paths rather than overwriting them.

`automata-deploy` accepts `{request_id,goal_id,commit}`; `automata-rollback` accepts
`{request_id,goal_id}`. The host fetches the fixed Automata repository through the
Custos guest and requires the full commit on a published branch. It bounds a Git
archive of that commit's particle-life tree to32MiB and copies opaque bytes into
126. Nothing from the source executes/extracts on Proxmox. The target runner rejects
links, device files, traversal and archived persistent species; limits extracted
files/bytes; checks Node syntax and simulation test; starts a separate canary unit
on18083 and probes root/sim/species. Healthy candidates switch the app symlink,
restart terrarium and receive HTTP checks. Failed activation restores the prior
release. Explicit rollback uses the recorded previous release. HTTP checks are
not a visual quality claim; source work still needs appropriate behavior tests.

Data is shared across releases and is not rolled back with app code. Original
snapshot, releases, deployment state and logs live under `/srv/custos-automata`.
An interrupted switch is surfaced by `automata-status` as inconsistent state and
no claimed commit. Reconcile actual path and retained evidence before a new deploy.
Automatic host worker recovery marks an interrupted running request uncertain.
Operator pause holds new jobs; an already running deploy completes its recovery.

Deploy the guest source/launcher and copied identity charter/skills/workspace;
never rerun the destructive fresh-persona installer on the running identity.
Publish and record runtime evidence in HANDOFF.md. The main entrypoints and full
payload/status semantics are in the custos-signal and custos-automata skills.

Restoring an existing commit compares normalized source paths, executable modes
and bytes, not tar headers: Git subtree archives carry changing timestamps. The
wire SHA-256 still verifies transfer integrity. Existing release markers are
upgraded only after checking the actual installed source contents.
