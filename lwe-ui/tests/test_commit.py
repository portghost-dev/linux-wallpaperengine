"""Self-tests for the commit gate + reject.

Isolation contract (LIVE MACHINE): HOME and all XDG_* point at a fresh tempfile dir, so every
paths.* resolves under it - the real ~/.config/lwe / ~/.local/state/lwe are NEVER touched. The
workshop source and the library are likewise temp dirs handed to commit() explicitly.

bench_courier is stubbed so NO socket traffic ever reaches a running engine; the stub only
records that commit/reject asked for resume() (and lets us assert tolerance of a dead socket -
the stub returns False).

Synthesized fixtures per run:
  * a WORKSHOP item dir: project.json (declares file=<payload>) + the payload + preview.jpg
    + a PLANTED shaders/ dir (must NOT be copied) ;
  * a wp/<id>.conf for that id with BG = the workshop path (so we can prove BG is rewritten).

There is NO draft buffer any more: the bench builds and tunes wp/<id>.conf
directly, so an approved item ships with its conf exactly as it sits at approval. What commit
still owns is the publish, the BG rewrite and the tag.

Cases proven:
  1. first-commit copies exactly the 3 file kinds (and NOT shaders/) into the temp library,
     rewrites the conf's BG to the library path, tags good, and the conf parses back via
     wp.load + a real `bash -c source`.
  2. a broken source (missing payload) returns ok:False, leaves no partial publish, writes no
     tag, and leaves the conf (the user's tuning session) untouched.
  3. re-commit is a NO-OP promote: the conf it would have promoted IS the live conf, so it
     copies nothing, changes nothing, and only fires the resume.
  4. reject writes bad and copies nothing. It no longer deletes anything.

Runs as a plain `python3 tests/test_commit.py` (pytest is absent on the box).
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


def _fresh_env(tmp: str) -> dict[str, str]:
    return {
        "HOME": tmp,
        "XDG_CONFIG_HOME": os.path.join(tmp, ".config"),
        "XDG_STATE_HOME": os.path.join(tmp, ".local/state"),
        "XDG_DATA_HOME": os.path.join(tmp, ".local/share"),
    }


class _CourierStub:
    """Records courier requests instead of touching a live engine. Returns False = 'dead socket'."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def resume(self) -> bool:
        self.calls.append("resume")
        return False

    def standdown(self, *a, **k) -> bool:
        self.calls.append("standdown")
        return False


def _reload_modules(stub: _CourierStub):
    """Re-import everything under the temp env, with bench_courier swapped for the stub."""
    import lwe_ui.constants  # noqa: F401

    for name in ("paths", "atomic", "tier_a", "wp", "tags"):
        importlib.reload(importlib.import_module(f"lwe_ui.storage.{name}"))
    importlib.reload(importlib.import_module("lwe_ui.discovery.project"))
    # Swap bench_courier with the stub BEFORE (re)loading commit so commit binds the stub.
    sys.modules["lwe_ui.bench_courier"] = stub  # type: ignore[assignment]
    commit = importlib.reload(importlib.import_module("lwe_ui.commit"))
    return commit


def _make_workshop_item(workshop_dir: Path, wid: str) -> None:
    """Synthesize a valid WORKSHOP item: project.json + payload + preview.* + a PLANTED shaders/."""
    idir = workshop_dir / wid
    idir.mkdir(parents=True, exist_ok=True)
    project = {
        "title": "Synthetic, Bench Test",  # comma proves csv quoting survives
        "type": "scene",
        "file": "scene.pkg",
        "preview": "preview.jpg",
        "general": {"properties": {}},
    }
    (idir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (idir / "scene.pkg").write_bytes(b"PKGV0001fake-payload-bytes")
    (idir / "preview.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    # PLANTED: shaders/ must never be copied into the library.
    sh = idir / "shaders"
    sh.mkdir(exist_ok=True)
    (sh / "secret.frag").write_text("// must not be published", encoding="utf-8")


def _bash_source_ok(conf_path: Path) -> dict[str, str]:
    """Prove the conf is shell-sourceable: `bash -c 'source <f>; print BG/TYPE'`. Returns vals."""
    script = (
        f"set -a; source {conf_path}; set +a; "
        'printf "BG=%s\\nTYPE=%s\\n" "$BG" "$TYPE"'
    )
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    ).stdout
    vals: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k] = v
    return vals


def _run_all() -> str:
    tmp = tempfile.mkdtemp(prefix="lwe-commit-test-")
    saved = {k: os.environ.get(k) for k in _fresh_env(tmp)}
    committed_conf_text = ""
    try:
        os.environ.update(_fresh_env(tmp))
        stub = _CourierStub()
        commit = _reload_modules(stub)
        from lwe_ui.bench_bridge import seed_pending_conf
        from lwe_ui.storage import paths, tags, wp

        assert str(paths.config_dir()).startswith(tmp), paths.config_dir()
        paths.ensure_dirs()

        workshop = Path(tmp) / "workshop"
        library = Path(tmp) / "library"
        library.mkdir(parents=True, exist_ok=True)

        wid = "100001"
        _make_workshop_item(workshop, wid)
        seeded = seed_pending_conf(wid, str(workshop))
        assert wp.exists(wid)
        assert seeded["BG"] == str(workshop / wid), seeded["BG"]

        rep = commit.commit(
            wid,
            source="pending",
            title="Synthetic, Bench Test",
            workshop_dir=str(workshop),
            wallpapers_dir=str(library),
        )
        assert rep["ok"], rep
        dest = library / wid
        assert (dest / "project.json").is_file()
        assert (dest / "scene.pkg").is_file()
        assert (dest / "preview.jpg").is_file()
        assert not (dest / "shaders").exists(), "shaders/ must NOT be published"
        names = sorted(p.name for p in dest.iterdir())
        assert names == ["preview.jpg", "project.json", "scene.pkg"], names
        # the conf survives commit with BG rewritten to the LIBRARY path. Nothing is consumed:
        # under L-19 the tuned conf IS the shipped conf.
        assert wp.exists(wid)
        conf = wp.load(wid)
        assert conf["BG"] == str(dest), conf["BG"]
        rows = {r["id"]: r for r in tags.load()}
        assert rows[wid]["state"] == "good", rows[wid]
        assert rows[wid]["title"] == "Synthetic, Bench Test", rows[wid]
        srcvals = _bash_source_ok(paths.wp_file(wid))
        assert srcvals["BG"] == str(dest), srcvals
        assert srcvals["TYPE"] == "scene", srcvals
        # the courier was asked to resume (tolerated dead socket -> recorded, returned False).
        assert "resume" in stub.calls, stub.calls
        committed_conf_text = paths.wp_file(wid).read_text(encoding="utf-8")

        wid2 = "200002"
        idir2 = workshop / wid2
        idir2.mkdir(parents=True, exist_ok=True)
        # project.json declares scene.pkg but we DON'T create it -> validation must fail.
        (idir2 / "project.json").write_text(
            json.dumps({"title": "Broken", "type": "scene", "file": "scene.pkg"}),
            encoding="utf-8",
        )
        (idir2 / "preview.jpg").write_bytes(b"x")
        seed_pending_conf(wid2, str(workshop))
        rep2 = commit.commit(
            wid2,
            source="pending",
            title="Broken",
            workshop_dir=str(workshop),
            wallpapers_dir=str(library),
        )
        assert rep2["ok"] is False, rep2
        assert "reason" in rep2, rep2
        assert not (library / wid2).exists(), "broken source must leave NO publish"
        leftovers = [p.name for p in library.iterdir() if p.name.startswith(f".{wid2}")]
        assert leftovers == [], leftovers
        assert wid2 not in {r["id"] for r in tags.load()}, "broken commit must not tag"
        assert wp.exists(wid2), "broken commit must leave the conf intact (still pending)"
        assert wp.load(wid2)["BG"] == str(workshop / wid2), "a failed publish must not rewrite BG"

        # ============================ Case 3: re-commit (a no-op) ============================
        # The good item from Case 1 is re-benched. Under L-19 the bench edits the live conf in
        # place, so by the time re-commit runs the value is ALREADY committed - the branch
        # exists only to fire the resume.
        wp.update_set(wid, {"SPEED": 2.0})
        before_names = sorted(p.name for p in dest.iterdir())
        rep3 = commit.commit(
            wid,
            source="good",
            title="ignored on re-commit",
            workshop_dir=str(workshop),
            wallpapers_dir=str(library),
        )
        assert rep3["ok"], rep3
        conf3 = wp.load(wid)
        assert conf3["BG"] == str(dest), conf3["BG"]
        assert conf3["SPEED"] == 2.0, conf3["SPEED"]
        after_names = sorted(p.name for p in dest.iterdir())
        assert after_names == before_names, (before_names, after_names)
        assert sorted(p.name for p in library.iterdir() if p.name.startswith(f".{wid}")) == []
        assert {r["id"]: r["state"] for r in tags.load()}[wid] == "good"
        assert stub.calls.count("resume") >= 2, \
            "re-commit must still resume the engine"

        wid4 = "400004"
        _make_workshop_item(workshop, wid4)
        seed_pending_conf(wid4, str(workshop))
        assert wp.exists(wid4)
        rep4 = commit.reject(wid4, title="Rejected, Item")
        assert rep4["ok"], rep4
        assert {r["id"]: r["state"] for r in tags.load()}[wid4] == "bad", "reject -> bad"
        assert not (library / wid4).exists(), "reject copies nothing"

    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("lwe_ui.bench_courier", None)
        shutil.rmtree(tmp, ignore_errors=True)

    return committed_conf_text


def test_commit_lifecycle():
    _run_all()


if __name__ == "__main__":
    conf = _run_all()
    print("OK: commit first-commit / broken / re-commit no-op / reject all passed")
    print("--- one committed wp/<id>.conf ---")
    print(conf, end="")
