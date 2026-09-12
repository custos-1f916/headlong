---
name: custos-harness
description: Experiment on your Headlong runtime, integrate upstream, build and qualify an exact release, and deploy or roll it back through the external supervisor.
---

# Own your harness

Hal grants standing authority to choose, implement, test, publish to the private
Custos repo, and deploy ordinary changes to your own harness. You do not need a
human review for each qualified release. This includes helpers, native runtime,
thinker prompts, skills and dashboard code. Pick changes that improve your work;
retain evidence and be candid when an experiment fails.

`/opt/custos/repo` is editable source. `runtime/headlong/` retains upstream history;
`headlong-upstream` is the public remote. Fetch and integrate pinned commits on a
worktree branch under `/opt/custos/work`. Never pull upstream into the live release.
`/opt/custos/current` is the selected release; do not edit it. Identity, native mem,
trajectories, policies, receipts and credentials persist outside release code.
The historical `renewal/install_guest.py` resets a fresh identity and is never an
upgrade tool.

## Experiment

Run foreign tests/builds through `hermetic`, including your own runtime tests:

```bash
hermetic --cwd /opt/custos/work/my-change --timeout 600 -- python3 -m unittest discover -s renewal/tests
```

The default profile boots a disposable KVM machine with no network and no live
filesystem mounts. HOME, application state, processes and credentials are separate.
Jobs have memory/CPU/time/output/storage bounds; inspect the printed result JSON
and artifacts under `/var/lib/custos-harness/jobs`. Exit status is the test status;
125 means the runner failed, not that tests passed. Timed-out descendants die with
the VM. Never rerun bare in the activated identity to get around a seal failure.

`--profile fetch` exposes only package manifests/locks and an allowlisted HTTPS
proxy. Use frozen locks and disable install hooks, then run builds offline.
`--profile model` grants one bounded model route through the existing admission
gateway, serially, with a disposable identity. It cannot contact real transports.
Neither profile exposes live tokens, memories, service sockets or the action broker.

## Release

Commit your change and relevant tests, and push the private branch. Build only the
commit you intend to deploy; uncommitted files are excluded. Keep stable request
IDs for retries; record the artifact hash and request IDs in your work memory.

```bash
custos-harness current
custos-harness build --repo /opt/custos/work/my-change --commit HEAD
custos-harness qualify ARTIFACT_SHA256 --request-id unique-qualify-id
custos-harness status unique-qualify-id
custos-harness deploy ARTIFACT_SHA256 --expected-current CURRENT_SHA256 --request-id unique-deploy-id
```

Qualification runs fixed continuity contracts plus candidate tests under the seal.
Read the receipt; acceptance is not qualification or deployment success. A stale
expected-current value fails: inspect the new current version and decide afresh.
Deployment is asynchronous. Once accepted, finish the current atomic operation; the native loop yields between steps so the
host supervisor can drain the mind. Do not poll forever inside a model tool call.
Inspect status on the next wake. A repeated request returns the original operation.

The supervisor retains the exact artifact externally, drains writers, checkpoints
state, switches code, and checks fresh successful model/tool progress plus service health. It
restores previous code after failure or interrupted promotion without rewinding
memories or delivery receipts. Roll back a retained qualified artifact with
`custos-harness rollback OLD_SHA --expected-current CURRENT_SHA --request-id ID`.
Operator pause always wins and is never cleared by a rollout.

## Persistent edits and limits

Your local skills are overlays. A release may replace a skill unchanged since the
previous release; your own independent edit survives. If both changed the same
skill, reconcile it in source and the overlay deliberately; deployment stops on a
conflict. Observation/dream settings live in `/var/lib/custos/config/`, and your
social policy remains inside the persistent identity. Release defaults do not
silently replace those files.

The first deployment format accepts state version 1 only. Incompatible/destructive
state migrations are rejected before stopping the mind. Dependencies are pinned in
a separately hashed bundle; a changed lock requires a newly prepared and tested
bundle before release construction. Retain old bundles and releases for rollback.

The host supervisor, fixed acceptance contracts, network/admission controls,
operator pause and access grants are external operator-owned controls. You may
propose changes to them in the repo, but a guest harness release cannot install
those changes. Do not erase identity or bypass an external boundary as an experiment.

## Sub-runs and drains (2026-09-11)

A deploy or rollback drains the mind between steps and then stops it. A sub-run that is
mid-task dies with it, and the parent's next wake starts the task over (the PDF-bridge run,
2026-09-11 16:19Z). So:

- `custos-harness deploy` / `rollback` **refuse while a sub-run is alive** (a `shellm` whose
  prompt file is `subrun`'s). Wait for it, read its report file, or pass `--allow-live-subrun`
  knowing it will be killed. The same rule binds the deploy agent: no release while a directed
  goal is mid-implementation with a live sub-run.
- When the maintenance flag appears, the monolith run gets **one more block** and a feedback
  step telling it to write a scratchpad note (`custos-memory note GOAL_ID "…"`) with its files,
  next command and any sub-run id. Use that block for exactly that.
- Generic `subrun` behavior remains: `--max-iterations` above 12 automatically **detaches**
  and prints a report path. That behavior is not permitted for Custos's inference: do not use
  automatic detachment, `--detach`, background model workers, or a fallback route. Run exactly
  one bounded synchronous helper at a time, suppressing its competing summary worker:
  `SHELLM_RUN_SUMMARY=0 subrun --wait --cwd DIR --max-iterations 8 --effort "${SHELLM_EFFORT:?}" "precise task: files, behaviour, how to test" > /tmp/subrun-NAME.txt 2>&1`.
  Retain the helper output in this action and hand off unfinished work precisely; do not make
  reading a detached model-worker report later Custos's normal workflow. Sub-runs see command
  output whole up to 24 KB, so read a file once, then write.
