"""Where a test window goes: the focused output's usable area as the compositor lays it out,
split into a two by two grid, and the dispatches that float a mapped window into one cell.

Shared by the Developer view's exhibits and the Workshop wizard's bench. Everything is a
plain function over `hyprctl` so a caller can stub the compositor away in a test.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

MIN_W = 320
MIN_H = 180
PLACE_TRIES = 5


def hyprctl(args: list[str]) -> str:
    try:
        r = subprocess.run(["hyprctl", *args], capture_output=True, text=True,
                           timeout=2, check=False)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def dispatch(expr: str) -> bool:
    """One Lua-form dispatcher; the classic flat syntax is a Lua error and does nothing."""
    return hyprctl(["dispatch", expr]).strip() == "ok"


def clients() -> list:
    out = hyprctl(["-j", "clients"])
    if not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def layout(query: Callable[[list[str]], str] = hyprctl) -> dict | None:
    """The focused output: logical geometry, the edges the compositor keeps reserved right
    now (a bar that is present, none when it is hidden or absent) and its outer gap. None
    when the compositor cannot be queried."""
    try:
        out = query(["-j", "monitors"])
        mons = json.loads(out) if out.strip() else []
        m = next((x for x in mons if x.get("focused")), mons[0]) if mons else None
        if not m:
            return None
        scale = float(m.get("scale") or 1.0) or 1.0
        res = [int(v) for v in (m.get("reserved") or [])[:4]]
        res += [0] * (4 - len(res))
        gap = 0
        opt = query(["getoption", "general:gaps_out", "-j"])
        if opt.strip():
            css = str(json.loads(opt).get("css", "")).split()
            if css and css[0].isdigit():
                gap = int(css[0])
        return {"x": int(m.get("x", 0)), "y": int(m.get("y", 0)),
                "w": int(int(m["width"]) / scale), "h": int(int(m["height"]) / scale),
                "reserved": res, "gap": gap}
    except (ValueError, KeyError, TypeError, IndexError):
        return None


def quadrant(side: str, lay: dict) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the top-left ("A") or top-right ("B") cell of the usable area, laid
    out like a two by two tiling: reserved edges and the outer gap stay clear."""
    left, top, right, bottom = lay["reserved"]
    gap = lay["gap"]
    qw = max(MIN_W, (lay["w"] - left - right - 3 * gap) // 2)
    qh = max(MIN_H, (lay["h"] - top - bottom - 3 * gap) // 2)
    qx = lay["x"] + left + gap + (0 if side == "A" else qw + gap)
    qy = lay["y"] + top + gap
    return qx, qy, qw, qh


def window_geometry(side: str, lay: dict | None) -> str | None:
    """--window geometry for one cell: its size. The position part is ignored on Wayland;
    the compositor maps the window and `place` moves it."""
    if not lay:
        return None
    q = quadrant(side, lay)
    return f"0x0x{q[2]}x{q[3]}"


def placed(win: dict, cell: tuple[int, int, int, int]) -> bool:
    at = win.get("at") or [0, 0]
    return abs(int(at[0]) - cell[0]) <= 4 and abs(int(at[1]) - cell[1]) <= 4


def place(win: dict, cell: tuple[int, int, int, int],
          run: Callable[[str], bool] | None = None) -> None:
    """Float a mapped window and move it into its cell."""
    run = run or dispatch
    addr = f"address:{win['address']}"
    qx, qy, qw, qh = cell
    if not win.get("floating"):
        run(f'hl.dsp.window.float({{ window = "{addr}" }})')
    run(f'hl.dsp.window.resize({{ x = {qw}, y = {qh}, window = "{addr}" }})')
    run(f'hl.dsp.window.move({{ x = {qx}, y = {qy}, window = "{addr}" }})')
