#!/usr/bin/env python3
"""blink1-only sanitized socket observer. Dedicated forced-command SSH key.

The remote probe is renewal/custos_backend_probe.py. Install the dedicated
private key and pinned Johan known_hosts in /etc/custos-gateway (root:root0600).
Never put either key in LXC122. Every observation requires fresh SSH execution;
failures publish observed_at=0 and fail admission closed. No retries inside a
probe, no remote changes, no model generation, no credential output.
"""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import tempfile
import time

DIRECTORY = Path("/run/custos-gateway")
SSH = ("/usr/bin/ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
       "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=/etc/custos-gateway/known_hosts",
       "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=2", "-o", "ConnectionAttempts=1",
       "-o", "ControlMaster=auto", "-o", "ControlPersist=60",
       "-o", "ControlPath=/run/custos-gateway/probe-control",
       "-o", "ClearAllForwardings=yes", "-o", "RequestTTY=no", "-i", "/etc/custos-gateway/probe_ed25519",
       "user@192.168.86.117", "custos-backend-probe")


def publish(value, directory=DIRECTORY):
    descriptor, temporary = tempfile.mkstemp(prefix=".busy-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
        # Ephemeral observation, not quota state: atomic rename is sufficient;
        # restart begins fail-closed and no stale signal can survive max age.
        os.replace(temporary, directory / "busy.json")
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


async def observe(command=SSH):
    started = time.time()
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL,
                                                  stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.DEVNULL, limit=4096)
    try:
        async def receive():
            raw = await process.stdout.read(1025)
            if len(raw) > 1024:
                raise ValueError("oversized probe output")
            if await process.stdout.read(1):
                raise ValueError("extra probe output")
            if await process.wait() != 0:
                raise ValueError("probe unavailable")
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"foreign_busy", "backend_idle"}:
                raise ValueError("invalid probe schema")
            if any(type(item) is not bool for item in value.values()):
                raise ValueError("invalid probe flags")
            # Timestamp START, not receipt: network delay may not refresh old data.
            return {"observed_at": started, **value}
        return await asyncio.wait_for(receive(), 2.5)
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def run():
    if os.geteuid() != 0:
        raise RuntimeError("busy producer must run on the host as root")
    DIRECTORY.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(DIRECTORY, 0o755)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    unavailable = {"observed_at": 0, "foreign_busy": True, "backend_idle": False}
    publish(unavailable)
    try:
        while not stop.is_set():
            try:
                value = await observe()
            except (OSError, ValueError, TypeError, asyncio.TimeoutError):
                value = unavailable
            publish(value)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), 0.5)
    finally:
        publish(unavailable)


if __name__ == "__main__":
    asyncio.run(run())
