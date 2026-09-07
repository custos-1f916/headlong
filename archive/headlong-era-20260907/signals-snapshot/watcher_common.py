#!/usr/bin/env python3
"""watcher-common — shared pieces for the custos signal watchers.

Emit: appends one observation step via `traj append` (sources the identity
activate first), JSON-quoting the content for the bash. Returns True on
success; the caller advances the cursor only on True.
"""
import json
import subprocess

ACTIVATE = "/root/.headlong/app/.identities/custos/activate"
TRAJ = "9253b427-aa85-42c3-ae34-d25ffb4759b0"


def emit(content: str, source: str) -> bool:
    if len(content) > 360:
        content = content[:357] + "..."
    # json.dumps gives a bash-safe double-quoted string (backslashes escaped).
    quoted = json.dumps(content)
    script = (
        f"source {ACTIVATE} >/dev/null 2>&1; "
        f"/root/.headlong/app/bin/traj append --field type=observation --field content={quoted} "
        f"--field source={source} >/dev/null 2>&1"
    )
    proc = subprocess.run(["bash", "-lc", script], timeout=30)
    return proc.returncode == 0


def log(state_dir: str, name: str, message: str) -> None:
    try:
        import datetime
        with open(f"{state_dir}/{name}.log", "a") as f:
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            f.write(f"{timestamp} {name}: {message}\n")
    except Exception:
        pass