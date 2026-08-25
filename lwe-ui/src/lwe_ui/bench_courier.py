"""Bench standdown courier: free the outputs for a test engine, then hand them back.

The daemon never dies for a bench: `release-outputs` tears down its surfaces +
scene VRAM while the socket keeps serving, `acquire-outputs` restores the same
wallpaper. A release is STATE, not a lease - it holds until acquire (the engine's
own dead-man reflex re-acquires if the whole client vanishes mid-hold). The bench
owns its own children's lifecycles: bench_bridge reaps NON-detached test engines
itself.

Callers: dev.py (A/B + isolator), wizard_bridge.py (scene-approval bench),
commit.py (post-commit resume). Crash-tolerant courier discipline: nothing here
ever raises.
"""
from __future__ import annotations

import time

from . import api_client


def available() -> bool:
    """Can a bench take the display at all? The socket has to answer, because the
    courier frees the outputs by asking the daemon to release them."""
    try:
        return bool(api_client.available())
    except Exception:
        return False


def standdown(timeout_s: float = 10.0) -> bool:
    """Free the outputs for a bench engine. True when the daemon's surfaces are
    released and a test engine can map its own."""
    try:
        reply = api_client.request("release-outputs")
        if reply is None or not reply.get("ok"):
            return False
        # released state is synchronous on the engine side; confirm via status so a
        # racing bench never maps its surfaces under the daemon's
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            st = api_client.status()
            if st is not None and (st.get("outputs") or {}).get("state") == "released":
                return True
            time.sleep(0.2)
        return False
    except Exception:
        return False


def wait_clear(timeout_s: float = 2.5) -> bool:
    """Wait until no TEST engines remain (bench child swap dwell). Exactly ONE
    engine process remains - the daemon itself, released but alive - so the
    baseline is 1, not 0."""
    import subprocess
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            out = subprocess.run(["pgrep", "-x", "linux-wallpaper"], capture_output=True,
                                 text=True, timeout=2, check=False).stdout
            if len([p for p in out.split() if p.strip()]) <= 1:
                return True
        except (OSError, subprocess.SubprocessError):
            return True
        time.sleep(0.1)
    return False


def resume() -> bool:
    """Hand the outputs back to the desktop engine (idempotent)."""
    try:
        reply = api_client.request("acquire-outputs")
        return reply is not None and bool(reply.get("ok"))
    except Exception:
        return False
