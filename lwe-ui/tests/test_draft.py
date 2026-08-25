"""The draft world is GONE - this file proves it, and covers what replaced each piece.

Was: self-tests for storage/draft.py, the sticky bench draft buffer. That module is deleted
whole by the editor build (no pending store, no second buffer, realtime
autosave everywhere). This file is retained rather than removed because the removal itself is
the contract now - a reintroduced draft buffer would silently split the store in two again and
nothing else would notice.

What each old check became:
  * roundtrip_and_crud / schema_parity -> moot. There is one file on one schema, so there is
    no second serialization to keep in parity with; storage/wp.py's own tests cover it.
  * seed_pending -> bench_bridge.seed_pending_conf, which builds wp/<id>.conf instead of a
    draft file. Same auto-derivations, same stickiness; asserted below.
  * seed_rebench -> deleted. It was literally wp.load + save; the bench now just calls wp.load.
  * _derive_cc (with its wec_e enabled-flag honoring) -> discovery.project.derive_cc,
    relocated as a pure function with no draft state. Asserted below, wec_e included.

Isolation: HOME + all XDG_* point at a fresh tempfile dir, so paths resolve under it and the
live ~/.local/state/lwe is NEVER touched. Also exercises a real bash `source` round-trip to
prove a bench-built conf is shell-sourceable (a value with spaces survives). Runs under pytest
if present, otherwise as a plain `python3 tests/test_draft.py`.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _fresh_env(tmp: str) -> dict[str, str]:
    return {
        "HOME": tmp,
        "XDG_CONFIG_HOME": os.path.join(tmp, ".config"),
        "XDG_STATE_HOME": os.path.join(tmp, ".local/state"),
        "XDG_DATA_HOME": os.path.join(tmp, ".local/share"),
    }


def _reload():
    import lwe_ui.constants  # noqa: F401
    mods = {}
    for name in ("paths", "atomic", "tier_a", "wp"):
        mods[name] = importlib.reload(importlib.import_module(f"lwe_ui.storage.{name}"))
    mods["project"] = importlib.reload(importlib.import_module("lwe_ui.discovery.project"))
    mods["bench_bridge"] = importlib.reload(importlib.import_module("lwe_ui.bench_bridge"))
    return mods


def _bash_source_get(conf_path: Path, key: str) -> str:
    """Source the Tier A conf in real bash and echo one key, proving shell-sourceability."""
    script = f'set -a; source {json.dumps(str(conf_path))}; set +a; printf "%s" "${{{key}}}"'
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True)
    return out.stdout


def check_draft_module_is_gone(S):
    """THE contract: importing the draft buffer must fail, and no path may still reach one."""
    try:
        importlib.import_module("lwe_ui.storage.draft")
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError(
            "lwe_ui.storage.draft still imports - the draft world must stay deleted (L-19)")

    src = Path(__file__).resolve().parent.parent / "src" / "lwe_ui"
    offenders = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for needle in ("storage.draft", "from .draft import", "import draft"):
            if needle in text:
                offenders.append(f"{py.name}: {needle}")
    assert not offenders, f"live draft references survive: {offenders}"


def check_seed_pending_conf(S):
    """The pending seed writes wp/<id>.conf directly, with the same auto-derivations."""
    bench_bridge, paths, wp = S["bench_bridge"], S["paths"], S["wp"]
    workshop = paths.state_dir() / "synthetic-workshop"
    wid = "1111111111"
    wdir = workshop / wid
    wdir.mkdir(parents=True, exist_ok=True)
    # type=video + a wec_* color-grade preset (50=neutral). brightness 75 -> 1.5,
    # contrast 25 -> 0.5, saturation 100 -> 2.0, hue 50 -> 0 (identity).
    (wdir / "project.json").write_text(json.dumps({
        "title": "Synthetic Pending",
        "type": "video",
        "file": "clip.mp4",
        "wec_brs": 75,
        "wec_con": 25,
        "wec_sa": 100,
        "wec_hue": 50,
    }), encoding="utf-8")

    d = bench_bridge.seed_pending_conf(wid, workshop)
    assert wp.exists(wid) is True
    assert paths.wp_file(wid) == paths.wp_dir() / f"{wid}.conf", paths.wp_file(wid)
    assert not (paths.draft_dir() / f"{wid}.conf").exists(), \
        "the pending seed must NOT write a draft file"
    # TYPE from project.json (file .mp4 also forces video)
    assert d["TYPE"] == "video", d["TYPE"]
    assert d["BG"] == str(wdir), d["BG"]
    assert d["CC"] == "1.5 0.5 2 0", d["CC"]
    assert wp.load(wid)["CC"] == "1.5 0.5 2 0"

    # STICKY: re-seeding returns the existing conf untouched even after an edit, so a reopen
    # never clobbers an in-progress tuning session
    wp.update_set(wid, {"SPEED": 3.0})
    again = bench_bridge.seed_pending_conf(wid, workshop)
    assert again["SPEED"] == 3.0, again["SPEED"]

    wid2 = "2222222222"
    wdir2 = workshop / wid2
    wdir2.mkdir(parents=True, exist_ok=True)
    (wdir2 / "project.json").write_text(json.dumps({"title": "No Grade", "type": "scene"}),
                                        encoding="utf-8")
    d2 = bench_bridge.seed_pending_conf(wid2, workshop)
    assert d2["TYPE"] == "scene"
    assert d2["CC"] == "1 1 1 0", d2["CC"]
    assert d2["BG"] == str(wdir2)


def check_conf_is_shell_sourceable(S):
    """A bench-built conf carries space-bearing values through a real bash `source`."""
    wp, paths = S["wp"], S["paths"]
    wid = "9876543210"
    d = wp.load(wid)
    d["BG"] = "/some/workshop/path 9876543210"   # space on purpose
    d["SPEED"] = 1.25
    d["SKIP"] = "101 102 103"
    d["MOUSE"] = True
    d["props"] = {"schemecolor": "1 0.5 0.2"}
    wp.save(wid, d)

    p = paths.wp_file(wid)
    assert _bash_source_get(p, "BG") == "/some/workshop/path 9876543210"
    assert _bash_source_get(p, "SKIP") == "101 102 103"
    assert _bash_source_get(p, "PROP_schemecolor") == "1 0.5 0.2"
    assert _bash_source_get(p, "MOUSE") == "true"

    r = wp.load(wid)
    assert r["BG"] == "/some/workshop/path 9876543210"
    assert r["SPEED"] == 1.25
    assert r["SKIP"] == "101 102 103"
    assert r["MOUSE"] is True
    assert r["props"] == {"schemecolor": "1 0.5 0.2"}, r["props"]


def check_derive_cc_relocated(S):
    """derive_cc moved to discovery.project intact - wec_e honoring included.

    wec_e is the color-correction ENABLED flag: a preset can ship non-neutral brs/con/sat
    sliders with correction OFF, and the engine applies none of them. Deriving a CC from those
    dormant values applied a correction the preset disabled.
    """
    derive_cc = S["project"].derive_cc
    off = {"wec_e": False, "wec_brs": 50, "wec_con": 100, "wec_sa": 50, "wec_hue": 50}
    assert derive_cc(off) == "1 1 1 0", derive_cc(off)
    on = {"wec_e": True, "wec_brs": 50, "wec_con": 100, "wec_sa": 50, "wec_hue": 50}
    assert derive_cc(on) == "1 2 1 0", derive_cc(on)
    absent = {"wec_brs": 50, "wec_con": 100, "wec_sa": 50, "wec_hue": 50}
    assert derive_cc(absent) == "1 2 1 0", derive_cc(absent)
    assert derive_cc({"title": "x"}) == "1 1 1 0"
    assert derive_cc(None) == "1 1 1 0"


_CHECKS = (
    ("draft_module_is_gone", check_draft_module_is_gone),
    ("seed_pending_conf", check_seed_pending_conf),
    ("conf_is_shell_sourceable", check_conf_is_shell_sourceable),
    ("derive_cc_relocated", check_derive_cc_relocated),
)


def _run_all() -> None:
    tmp = tempfile.mkdtemp(prefix="lwe-draft-test-")
    saved = {k: os.environ.get(k) for k in _fresh_env(tmp)}
    try:
        os.environ.update(_fresh_env(tmp))
        S = _reload()
        assert str(S["paths"].wp_dir()).startswith(tmp), S["paths"].wp_dir()
        S["paths"].ensure_dirs()
        for _name, fn in _CHECKS:
            fn(S)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def test_draft_world_is_removed():
    _run_all()


if __name__ == "__main__":
    _run_all()
    print("OK: the draft world is gone; its replacements all check out")
