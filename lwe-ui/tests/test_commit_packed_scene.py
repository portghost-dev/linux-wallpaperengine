"""Regression: first-commit must ingest a PACKED scene whose project.json.file is `scene.json`
but whose real on-disk payload is `scene.pkg` (scene.json is packed inside the pkg).

The old assumption was "payload = project.json.file", which is true for video/web but false
for packed scenes - without the _resolve_payloads fix every scene ingest fails validation. This
test synthesizes that exact shape and asserts the pkg is published. bench_courier is stubbed in
sys.modules so no signal reaches a live watcher; all paths are tempfile trees.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

# stub the socket courier BEFORE importing commit (a real request would hit the live engine)
_stub = types.ModuleType("lwe_ui.bench_courier")
for _fn in ("available", "standdown", "wait_clear", "resume"):
    setattr(_stub, _fn, (lambda *a, **k: False))
sys.modules["lwe_ui.bench_courier"] = _stub


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="lwe-packed-scene-"))
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(home / "config")
        os.environ["XDG_STATE_HOME"] = str(home / "state")
        os.environ["XDG_DATA_HOME"] = str(home / "data")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from lwe_ui.storage import paths, tags, wp
        from lwe_ui.bench_bridge import seed_pending_conf
        from lwe_ui import commit

        paths.ensure_dirs()
        wid = "555000111"
        ws = home / "workshop"
        (ws / wid).mkdir(parents=True)
        # PACKED scene: project.json names scene.json, but only scene.pkg is on disk.
        (ws / wid / "project.json").write_text(
            json.dumps({"type": "scene", "file": "scene.json", "title": "Packed Scene"})
        )
        (ws / wid / "scene.pkg").write_bytes(b"PKGV0001fake-but-present")
        (ws / wid / "preview.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
        lib = home / "library"
        lib.mkdir()

        seed_pending_conf(wid, str(ws))
        rep = commit.commit(wid, source="pending", title="Packed Scene",
                            workshop_dir=str(ws), wallpapers_dir=str(lib))
        assert rep.get("ok"), f"packed-scene commit should succeed: {rep}"
        published = sorted(os.listdir(lib / wid))
        assert "scene.pkg" in published, f"scene.pkg must be published, got {published}"
        assert "project.json" in published and any(p.startswith("preview") for p in published)
        assert wp.exists(wid) and wp.load(wid)["BG"] == str(lib / wid)
        assert wid in tags.good_ids() and wp.exists(wid)
        print("OK: packed-scene (file=scene.json / on-disk scene.pkg) ingests; scene.pkg published")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
