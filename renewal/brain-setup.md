# Brain router — LXC 131 `custos-brain` (2026-09-11)

Hal: "Nothing should live on Blink1 directly." The inference admission layer (the gateway that
LXC 122 calls at `:18080` and the johan busy probe) moved from blink1 into LXC 131
(`192.168.86.69`), and gained a **brain policy**: per request it serves either johan (Qwen3.8-27B,
exactly as before) or a cloud tier through the ChatGPT Codex backend with Hal's OAuth login. The
token lives only in this container. Custos cannot deploy here: this is where the revert is enforced.

## Layout inside 131

| Path | Owner / mode | What |
|---|---|---|
| `/opt/custos-brain/{custos_gateway.py,custos_brain.py,custos_busy.py,custos_images.py}` | root 0644 | copies of `renewal/` at the installed commit (record it in HOST-PATCHES style below) |
| `/etc/custos-brain/gateway-policy.json` | root 0644 | the gateway policy, `bind_host` `192.168.86.69`, `allowed_clients` `["192.168.86.52"]` |
| `/etc/custos-brain/brain.json` | root 0644 | the brain policy (window, tiers, routes, johan MAC, ntfy topic) |
| `/etc/custos-gateway/{probe_ed25519,known_hosts}` | root 0600 | the forced-command probe key for johan (moved from blink1; used by `custos-busy` only) |
| `/var/lib/custos-brain/codex-auth.json` | custos-brain 0600 | copy of `~/.codex/auth.json`; rotated in place on refresh |
| `/var/lib/custos-brain/state.json` | custos-brain 0600 | `{mode, reason, at, window_id, quota}` — the sticky revert |
| `/run/custos-gateway/busy.json` | root 0644 | johan busy observation (written by `custos-busy`, read by the gateway in local mode) |

Units: `custos-busy.service` (root; SSH probe of johan every 0.5 s) and `custos-brain.service`
(user `custos-brain`; `custos_gateway.py --policy … --brain … --state-dir …`). nftables inside 131
admits `192.168.86.52` on 18080 and nothing else inbound.

## Opening a window

Edit `/etc/custos-brain/brain.json`:

```json
{"window_id": "2026-09-12-frontier", "until": "2026-09-12T22:30:00Z",
 "tiers": {"astra": "gpt-6-astra", "terra": "gpt-5.6-terra"},
 "routes": {"gpt-6-astra": "astra", "gpt-5.6-terra": "terra", "*": "terra"},
 "effort": {"medium": "medium", "xhigh": "xhigh"},
 "johan": {"mac": "d8:43:ae:4d:bc:6d", "ip": "192.168.86.117", "port": 8080},
 "ntfy_topic": "<uuid>"}
```

then `systemctl restart custos-brain`. A **new `window_id`** re-arms a state that had reverted; the
same id never does. `curl -s http://192.168.86.69:18080/brain` (from LXC 122) shows what is live.

Routing while `mode == cloud`: the request's `model` picks the tier (`routes`, `*` = default), and
`reasoning_effort` medium/xhigh maps through `effort` (a 400 naming the effort falls back to `high`
for that model). `qwen3.8-27b`, which sub-runs, recap, mem-search, the summary model and the sealed
model profile send, lands on the default tier. While `mode == local` every accepted model name is
coerced to `qwen3.8-27b` and served by johan through the unchanged admission path.

## What flips it back (sticky)

1. `now >= until`.
2. OpenAI reports the allowance spent: HTTP 429 with `usage_limit_reached` / `usage_not_included`
   (and the workspace variants), or a `used_percent` of 100 in the `x-codex-*` headers or the
   `rate_limits` block of `response.completed`.

On the flip the service writes `state.json`, sends a wake-on-LAN packet to johan's MAC, posts to
Hal's ntfy topic, and serves johan from then on (503 `upstream_or_admission_unavailable` while johan
is still booting; `bin/llm` retries that code). Rehearse without touching the live state:
`python3 /opt/custos-brain/custos_brain.py --policy /etc/custos-brain/brain.json --simulate-flip`
(sends the packet and a "[rehearsal]" ntfy). `--wake-johan` sends the packet only; `--status` prints
the state.

## Guest side

`CUSTOS_INFERENCE_URL=http://192.168.86.69:18080` (default in code) is read by `renewal/harness/seal.py`
(model profile proxy) and `renewal/custos_observe.py` (`inference-admission`); the identity `.env`
carries `LLM_API_URL`/`SHELLM_API_URL` at the same host, and `LLM_MODEL_ALLOWLIST` must list every
model name the guest is meant to send (`qwen3.8-27b,gpt-6-astra,gpt-5.6-terra`). The guest keeps
`LLM_PROVIDER=openai-compatible`; effort tiering therefore survives the cloud path.

## Rollback

blink1's `custos-gateway` and `custos-busy` units are disabled but present for one week after the
cutover. To fall back: point the guest `.env` URLs at `192.168.86.44:18080`, `systemctl start
custos-busy custos-gateway` on blink1, restart the mind. The firewall keeps both destinations open.
