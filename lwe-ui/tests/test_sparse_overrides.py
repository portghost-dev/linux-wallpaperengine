"""Overrides carry only what the user set or the wallpaper declares (R125): the importer
writes facts, never defaults; the one-time clean-up strips materialised defaults from every
file after a snapshot, keeps real choices and properties, and runs once."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-sparse-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
    from lwe_ui import constants as C
    from lwe_ui.storage import paths, settings, tier_a, wp

    paths.ensure_dirs()
    settings.ensure_exists()

    # facts_to_keys: identity always, a default never, a declared fact when it differs
    dense = {k: s["default"] for k, s in C.WP_SCHEMA.items()}
    assert wp.facts_to_keys({**dense, "BG": "1", "TYPE": "scene"}) == {"BG": "1", "TYPE": "scene"}
    flat = wp.facts_to_keys({**dense, "BG": "1", "TYPE": "scene", "CC": "0.9 1 1 0",
                             "AUDIO_REACTIVE": True, "SPEED": 1.0, "props": {"hue": "0.2", "bad name": "x"}})
    assert flat == {"BG": "1", "TYPE": "scene", "CC": "0.9 1 1 0", "AUDIO_REACTIVE": "true", "PROP_hue": "0.2"}, flat

    # write_keys writes exactly those keys
    wp.write_keys("1", flat)
    assert tier_a.parse((paths.wp_dir() / "1.conf").read_text()) == flat
    assert wp.load("1")["SPEED"] == 1.0, "the dense reader still materialises defaults on read"

    # sparsify: a dense file loses its default-valued keys and keeps the rest
    dense_text = "\n".join(f"{k}={s['default']}" for k, s in C.WP_SCHEMA.items() if k not in ("BG", "TYPE"))
    (paths.wp_dir() / "2.conf").write_text(f"BG=2\nTYPE=scene\n{dense_text}\nSPEED=2.5\nVOLUME=0\nPROP_x=1\n")
    (paths.wp_dir() / "3.conf").write_text("BG=3\nMOUSE=true\n")
    report = wp.sparsify_overrides()
    assert "2" in report and "3" not in report and "1" not in report, report
    assert "VOLUME" in report["2"] and "AUTOMUTE" in report["2"] and "SPEED" not in report["2"], report["2"]
    kept = tier_a.parse((paths.wp_dir() / "2.conf").read_text())
    assert kept == {"BG": "2", "TYPE": "scene", "SPEED": "2.5", "PROP_x": "1"}, kept
    assert wp.load_set("2") == {"BG": "2", "TYPE": "scene", "SPEED": 2.5, "props": {"x": "1"}}
    assert wp.sparsify_overrides() == {}, "a second run changes nothing"

    # the startup hook: snapshot first, marker written, runs once
    import logging
    from lwe_ui import app
    (paths.wp_dir() / "4.conf").write_text("BG=4\nVOLUME=0\nMOUSE=true\n")
    log = logging.getLogger("lwe_ui.test")
    app._sparsify_overrides_once(log)
    snaps = list((paths.state_dir() / "backups").glob("pre-sparsify-*.lwebackup"))
    assert len(snaps) == 1, snaps
    assert (paths.panel_state_dir() / "overrides-sparse").exists()
    assert wp.load_set("4") == {"BG": "4", "MOUSE": True, "props": {}}
    (paths.wp_dir() / "5.conf").write_text("BG=5\nVOLUME=0\n")
    app._sparsify_overrides_once(log)
    assert "VOLUME" in wp.load_set("5"), "the clean-up ran once; the marker holds"
    print("OK sparse overrides: facts only at arrival, one-time clean-up with snapshot, idempotent")


if __name__ == "__main__":
    main()
