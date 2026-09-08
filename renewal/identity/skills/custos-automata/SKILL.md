---
name: custos-automata
description: Develop the live Automata particle-life app and autonomously deploy or roll back its changes to Automata LXC126 through the approved host channel.
---

# Own the Automata source-to-deployment workflow

Hal explicitly authorized you to deploy your Automata changes to LXC126 on
2026-09-08. Routine deployments and recovery through this channel need no further
approval. This named exception grants application deployment to Automata, not
Proxmox administration or arbitrary access to other homelab hosts.

The checkout is `/opt/custos/work/repos/collettiquette/automata` and GitHub repo
`collettiquette/automata`. Load `custos-repositories` for ordinary source work.
The live app is **particle-life/**, served by `terrarium.service` in LXC126 at
192.168.86.67:8080. Source recovered from that live app was published on 2026-09-08.
Read `DEPLOY-CUSTOS.md` and the current branch. Root-level legacy simulation files,
`deploy.sh`, old Tycho identity/heartbeat boilerplate and LXC110 are historical.
Do not revive old SSH credentials, Proxmox `pct` access or retired deployment scripts.

1. Choose or reuse a native goal. Modify the actual `particle-life/` app in an
   appropriate branch/worktree. Verify `node particle-life/sim.test.js`, server
   syntax and the relevant behavior; UI work also needs real visual evidence.
2. Commit and push to a branch of `collettiquette/automata` through your own GitHub
   account. Use a **full 40-character commit**. Deployment accepts a commit contained
   on a currently published branch, not dirty working-tree files or a guessed ref.
3. Pipe a stable operation object to `custos-actions automata-deploy`:

```json
{"request_id":"automata-unique-deploy-001","goal_id":"0123abcd","commit":"FULL_40_HEX_COMMIT"}
```

Use an actual native goal ID and commit. The host fetches that fixed repository,
checks publication and transfers only its `particle-life/` Git tree. It executes
no project code on Proxmox. Candidate syntax/simulation/HTTP checks run in 126;
healthy candidates replace `/srv/terrarium-pl` atomically, restart the named service
and receive fresh HTTP checks. A failed post-switch check restores the old release.

4. Inspect `custos-actions status automata-unique-deploy-001` until terminal;
   queued/running is not deployed. `succeeded` must include `ok:true`, the intended
   commit, `healthy:true`, `state_consistent:true` and service activity. Follow with
   `custos-actions automata-status`. Record receipt, tests, commit and real result
   on the same native goal. HTTP health is not proof of visual correctness.
5. For a regression, `custos-actions automata-rollback` reads
   `{request_id,goal_id}` from stdin and restores the retained previous app release.
   Check its receipt and status too. Species data is shared/preserved, not rewound
   with source; do not claim a data migration was undone by an app rollback.

Saved species live outside release trees at `/var/lib/automata/species`; each
release links `species/` there. Never commit `particle-life/species/`, secrets,
symlinks or device files into deployment archives. Releases, original app/data
snapshot, state and logs remain in `/srv/custos-automata` in 126. The host maintains
its own operation receipts. These are evidence, not another goal store.

On timeout or `uncertain`, inspect the same operation and `automata-status` before
anything else. Reuse the exact request ID/payload; never blindly send a new deploy
or rollback to resolve an unknown outcome. A state mismatch after an interrupted
switch is a reconciliation condition, not a claimed success. Long-running deploys
serialize and operator pause prevents new actions. No direct LAN or SSH route is
needed from your home.
