"""Per-process resource samples for the Developer view's stats overlay.

Every function forks at most one external command and never raises; callers run them on
a worker thread. GPU utilization is the whole GPU: the driver reports no per-process
share (nvidia-smi pmon prints "-" for sm%), so a per-process figure has no source.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable


def rss_mb(pids: Iterable[int]) -> int:
    """Resident set size in MiB summed over pids; -1 when unreadable."""
    try:
        page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
        total_kb = 0
        for pid in pids:
            with open(f"/proc/{pid}/statm", "r", encoding="utf-8") as fh:
                total_kb += int(fh.read().split()[1]) * page_kb
        return total_kb // 1024
    except (OSError, ValueError, IndexError):
        return -1


def rss_swap_mb(pids: Iterable[int]) -> tuple[int, int]:
    """(resident MiB, swapped MiB) summed over pids from /proc/PID/status; (-1, -1) when unreadable."""
    rss_kb = swap_kb = 0
    try:
        for pid in pids:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        rss_kb += int(line.split()[1])
                    elif line.startswith("VmSwap:"):
                        swap_kb += int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return -1, -1
    return rss_kb // 1024, swap_kb // 1024


def cpu_ticks(pids: Iterable[int]) -> int:
    """User plus system clock ticks summed over pids; -1 when unreadable."""
    ticks = 0
    try:
        for pid in pids:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
                parts = fh.read().rsplit(")", 1)[1].split()
            ticks += int(parts[11]) + int(parts[12])
    except (OSError, ValueError, IndexError):
        return -1
    return ticks


def cpu_percent_of_machine(tick_delta: int, seconds: float) -> float:
    """Percent of the whole machine (all cores) spent by a tick delta over an interval."""
    if tick_delta < 0 or seconds <= 0:
        return 0.0
    hz = os.sysconf("SC_CLK_TCK") or 100
    ncpu = os.cpu_count() or 1
    return round(max(0.0, tick_delta / hz / seconds * 100 / ncpu), 1)


def gpu_sample() -> tuple[int, int]:
    """(whole-GPU utilization %, total VRAM MiB); -1 for either when unavailable."""
    util, total = -1, -1
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.total",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=3, check=False)
        if q.returncode == 0:
            cols = [c.strip() for c in q.stdout.strip().split("\n")[0].split(",")]
            if len(cols) >= 2 and cols[0].isdigit() and cols[1].isdigit():
                util, total = int(cols[0]), int(cols[1])
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return util, total


def vram_mb(pids: Iterable[int]) -> int:
    """Framebuffer MiB summed over pids from nvidia-smi pmon; -1 when none are listed."""
    wanted = set(pids)
    try:
        r = subprocess.run(["nvidia-smi", "pmon", "-c", "1", "-s", "m"],
                           capture_output=True, text=True, timeout=3, check=False)
        if r.returncode != 0:
            return -1
        total, seen = 0, False
        for ln in r.stdout.splitlines():
            cols = ln.split()
            if len(cols) >= 4 and cols[1].isdigit() and int(cols[1]) in wanted:
                seen = True
                if cols[3].isdigit():
                    total += int(cols[3])
        return total if seen else -1
    except (OSError, subprocess.SubprocessError, ValueError):
        return -1
