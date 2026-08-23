# turn/ — the harness

Deployable files for the LXC. `bootstrap.sh` installs them to their active
locations; the repo is the source of truth.

## Files

| File | Active location | Purpose |
|---|---|---|
| `turn.sh` | `/opt/custos/turn.sh` | cron entrypoint: window guard → flock (no overlap) → git pull → one `pi -p` turn (9-min timeout) → commit+push → watch log |
| `cron.custos` | `/etc/cron.d/custos` | `*/10 0-4 * * *` — 30 turns/night, 00:00–04:50 local |
| `models.json` | `/root/.pi/agent/models.json` | pi provider `ninfer` → johan `:8080/v1`, model `qwen3.8-27b` (128K ctx) |
| `settings.json` | `/root/.pi/agent/settings.json` | defaults: provider ninfer, thinking medium, no telemetry |
| `bootstrap.sh` | (run via `pct exec`) | idempotent: TZ, apt, node 22, pi (pinned), configs, cron, repo clone |

## Note on the package name

`@mariozechner/pi-coding-agent` prints a deprecation notice pointing at the
renamed org (`@earendil-works/pi-coding-agent`). The pinned version here is
the one verified against NInfer's API shape; when upgrading, try the new org
name first and re-run the probe below.

## Deploy a change

```sh
# edit files here, commit + push, then from the Mac:
SSH='ssh -i ~/.ssh/id_ed25519 user@192.168.86.44'   # blink1
$SSH 'sudo -n pct exec 122 -- sh -c "cd /opt/custos/repo && git pull --rebase"'
for f in turn.sh cron.custos models.json settings.json bootstrap.sh; do
  scp -q -i ~/.ssh/id_ed25519 "turn/$f" user@192.168.86.44:/tmp/custos-$f
  $SSH "sudo -n pct push 122 /tmp/custos-$f /tmp/custos-$f && rm /tmp/custos-$f"
done
$SSH 'sudo -n pct exec 122 -- sh /opt/custos/bootstrap.sh'
```

## Fallbacks (in order)

1. **NInfer rejects `reasoning_effort` values** (the template exposes only
   low/medium/xhigh; if pi sends something else): set the model's
   `"reasoning": false` in `models.json` — pi then stops managing thinking
   and NInfer runs with its service default (thinking on). Re-deploy.
2. **pi's OpenAI-completions path fights NInfer's tool calls**: NInfer also
   serves an Anthropic-messages API; point the provider at
   `http://192.168.86.117:8080` with `"api": "anthropic-messages"` (check the
   exact mount with `curl http://192.168.86.117:8080/` first).
3. **pi itself breaks**: replace the one `pi -p` line in `turn.sh` with a thin
   OpenAI-compatible curl client that emits the same prompt and saves the
   reply to the journal; everything else (flock, window, git, ntfy) survives.

## Troubleshooting

- `turns.log` says `skip: previous turn still running` — the flock is held;
  check `/var/log/custos/turn-*.log` for the in-flight turn. It ends by
  timeout at 9 min. If a stale lock file is the cause (turn.sh crashed
  hard), the lock is on an fd of a live process — nothing to clean by hand.
- `pi rc=124` — timed out; the turn was cut mid-work. The journal entry the
  agent wrote (if any) says where it stopped; the next turn picks up.
- `push FAILED` — usually the Mac committed in the same second; the next
  turn's `git pull --rebase` resolves it. Persistent failures: check the
  deploy key (`ssh -i /opt/custos/git-deploy.key -T git@github.com`).
- ntfy silent — check `CUSTOS_NTFY_TOPIC` in `/etc/custos.env` and egress to
  `https://ntfy.sh` (the container's DNS is the Pi-hole; ntfy.sh is public).
- **Turns silently no-op** (no line in `turns.log`, nothing on the wire): cron
  runs turn.sh under `/bin/sh` = **dash** (Debian 12). Dash has no `10#` radix
  arithmetic — `$(( 10#8 ))` dies with "arithmetic expression: expecting EOF",
  and if cron's stderr is discarded the whole turn no-ops invisibly. Keep every
  line POSIX (strip the leading zero with `${H#0}` instead of `10#$H`) and check
  `/var/log/custos/cron.log` for stderr from pre-log crashes. Found 2026-08-23
  by the harness tester's dash run; fixed in `10#` removal + cron.log.
- **Wrong pi binary** (a test stub left over from a harness run): `head -2
  /opt/node/bin/pi` — the real pi is a symlink into
  `/opt/node/lib/node_modules/@mariozechner/pi-coding-agent/dist/cli.js`; the
  turn log now records `pi: <version> (path)` at every turn start. If the stub
  ever returns, restore the symlink:
  `ln -sf ../lib/node_modules/@mariozechner/pi-coding-agent/dist/cli.js /opt/node/bin/pi`.
