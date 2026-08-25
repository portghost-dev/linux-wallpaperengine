"""Regression tests for the bench-backend fixes.

Covers: commit path-traversal guard, tag-failure rollback, copy de-dup, conf CC nested-preset
derivation, and bench empty-BG guard. All sandboxed (tempfile HOME/XDG); bench_courier stubbed in
sys.modules so no signal can reach a live watcher.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

_stub = types.ModuleType("lwe_ui.bench_courier")
for _fn in ("available", "standdown", "wait_clear", "resume"):
    setattr(_stub, _fn, (lambda *a, **k: False))
sys.modules["lwe_ui.bench_courier"] = _stub


def _sandbox():
    home = Path(tempfile.mkdtemp(prefix="lwe-fixes-"))
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / "config")
    os.environ["XDG_STATE_HOME"] = str(home / "state")
    os.environ["XDG_DATA_HOME"] = str(home / "data")
    os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
    return home


def _mk_workshop_item(ws: Path, wid: str, *, file_field: str, files: dict[str, bytes]):
    (ws / wid).mkdir(parents=True)
    proj = {"type": "scene", "file": file_field, "title": f"T{wid}"}
    (ws / wid / "project.json").write_text(json.dumps(proj))
    for name, data in files.items():
        (ws / wid / name).write_bytes(data)


def test_path_traversal_rejected():
    home = _sandbox()
    try:
        from lwe_ui.storage import paths, tags
        from lwe_ui.bench_bridge import seed_pending_conf
        from lwe_ui import commit
        paths.ensure_dirs()
        ws = home / "ws"; lib = home / "lib"; lib.mkdir(parents=True)
        victim = lib / "VICTIM.txt"; victim.write_text("original")
        # project.json.file = '../VICTIM.txt' (escapes per-id dir) - and a preview present
        _mk_workshop_item(ws, "evil1", file_field="../VICTIM.txt", files={"preview.jpg": b"j"})
        (ws / "evil1" / ".." ).mkdir(exist_ok=True)
        seed_pending_conf("evil1", str(ws))
        rep = commit.commit("evil1", source="pending", title="E", workshop_dir=str(ws), wallpapers_dir=str(lib))
        assert not rep.get("ok"), f"traversal should be rejected: {rep}"
        assert victim.read_text() == "original", "victim file was clobbered (traversal!)"
        assert "evil1" not in tags.known_ids()
        _mk_workshop_item(ws, "evil2", file_field="/etc/hostname", files={"preview.jpg": b"j"})
        seed_pending_conf("evil2", str(ws))
        rep2 = commit.commit("evil2", source="pending", title="E2", workshop_dir=str(ws), wallpapers_dir=str(lib))
        assert not rep2.get("ok"), f"absolute path should be rejected: {rep2}"
        print("OK test_path_traversal_rejected")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_tag_failure_rolls_back():
    home = _sandbox()
    try:
        from lwe_ui.storage import paths, tags, wp
        from lwe_ui.bench_bridge import seed_pending_conf
        from lwe_ui import commit
        paths.ensure_dirs()
        ws = home / "ws"; lib = home / "lib"; lib.mkdir(parents=True)
        _mk_workshop_item(ws, "777", file_field="scene.json",
                          files={"scene.pkg": b"PKGV0001x", "preview.jpg": b"j"})
        seed_pending_conf("777", str(ws))
        orig = tags.set_state
        tags.set_state = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            rep = commit.commit("777", source="pending", title="X", workshop_dir=str(ws), wallpapers_dir=str(lib))
        finally:
            tags.set_state = orig
        assert not rep.get("ok"), "tag failure should yield ok:False, not raise"
        assert not (lib / "777").exists(), "publish must be rolled back on tag failure (no orphan)"
        # L-19: there is no draft any more - this conf IS the user's tuning session, so a
        # failed tag costs them the publish and NOTHING else. The one thing rolled back is
        # the BG rewrite, which goes back to pointing at the workshop tree (still pending,
        # still retryable).
        assert wp.exists("777"), "the conf must survive a failed commit (it is the user's work)"
        assert wp.load("777")["BG"] == str(ws / "777"), \
            f"BG must be rolled back to the workshop path, got {wp.load('777')['BG']!r}"
        assert "777" not in tags.known_ids()
        print("OK test_tag_failure_rolls_back")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_copy_dedup():
    home = _sandbox()
    try:
        from lwe_ui.storage import paths, wp
        from lwe_ui.bench_bridge import seed_pending_conf
        from lwe_ui import commit
        paths.ensure_dirs()
        ws = home / "ws"; lib = home / "lib"; lib.mkdir(parents=True)
        # payload IS a preview.* name -> must be copied/listed once, not twice
        _mk_workshop_item(ws, "888", file_field="preview.mp4",
                          files={"preview.mp4": b"video", "preview.jpg": b"thumb"})
        seed_pending_conf("888", str(ws))
        rep = commit.commit("888", source="pending", title="D", workshop_dir=str(ws), wallpapers_dir=str(lib))
        assert rep.get("ok"), rep
        copied = rep["copied"]
        assert copied.count("preview.mp4") == 1, f"preview.mp4 listed twice: {copied}"
        print("OK test_copy_dedup")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_conf_cc_nested_preset():
    home = _sandbox()
    try:
        from lwe_ui.storage import paths, wp
        from lwe_ui.bench_bridge import seed_pending_conf
        paths.ensure_dirs()
        ws = home / "ws"; (ws / "p1").mkdir(parents=True)
        # wec_* NESTED under preset (real WE shape): brs 51 -> 1.02, sa 100 -> 2.0, neutral con/hue
        proj = {"type": "scene", "file": "scene.json", "title": "P",
                "preset": {"wec_brs": 51, "wec_con": 50, "wec_sa": 100, "wec_hue": 50}}
        (ws / "p1" / "project.json").write_text(json.dumps(proj))
        d = seed_pending_conf("p1", str(ws))
        cc = d["CC"].split()
        assert cc != ["1", "1", "1", "0"], f"CC should reflect the nested preset, got {d['CC']!r}"
        assert abs(float(cc[0]) - 1.02) < 1e-6 and abs(float(cc[2]) - 2.0) < 1e-6, d["CC"]
        (ws / "p2").mkdir()
        (ws / "p2" / "project.json").write_text(json.dumps({"type": "scene", "file": "scene.json"}))
        d2 = seed_pending_conf("p2", str(ws))
        assert d2["CC"] == "1 1 1 0", d2["CC"]
        print(f"OK test_conf_cc_nested_preset (CC={d['CC']!r})")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bench_empty_bg_raises():
    home = _sandbox()
    try:
        from lwe_ui import bench
        try:
            bench.build_test_argv("/eng", "/assets", ["DP-1"], {"BG": "", "props": {}},
                                  source="pending", workshop_dir="/ws", wallpapers_dir="/lib")
        except ValueError:
            print("OK test_bench_empty_bg_raises")
            return
        raise AssertionError("empty BG must raise, not render the tree root")
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    test_path_traversal_rejected()
    test_tag_failure_rolls_back()
    test_copy_dedup()
    test_conf_cc_nested_preset()
    test_bench_empty_bg_raises()
    print("ALL bench-fix regressions passed")
