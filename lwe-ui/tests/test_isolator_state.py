"""Isolator state exclusivity + tap-bounds source contract (owner findings, third pass).

Behavior (python level, no GUI):
  * solo is a SET - soloing a second object keeps the first (owner report: "if i select a
    second one it deselects the first"), and re-soloing one removes just that one
  * soloing an object REMOVES it from the skip list (solo hides everything else; skip
    hides the object itself - both on one object rendered nothing but gray)
  * skipping a soloed object drops it from the solo set for the same reason
  * plain solo toggling never touches other skips

Source contract (the regression that produced "click anywhere = solo AND ignore"):
  * no TapHandler in ToolsPalette.qml uses `target: skipChip` - a handler's tap bounds
    come from its PARENT, so that form fired for every row tap; the skip chip's handler
    must be declared INSIDE the chip.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_isolator_state.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
_SRC = str(_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-iso-")
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
    os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

    from PySide6.QtCore import QCoreApplication
    from lwe_ui.dev import DevBridge
    from lwe_ui.storage import paths, settings

    app = QCoreApplication.instance() or QCoreApplication(["t"])  # noqa: F841
    paths.ensure_dirs()
    settings.ensure_exists()

    dev = DevBridge()
    dev._auto_relaunch = False

    dev.setSkipObject("7", True)
    dev.solo("3")
    st = dev.isolationState()
    assert st["soloObjects"] == ["3"] and "3" not in st["skipObjects"], st
    assert "7" in st["skipObjects"], "unrelated skips must survive a solo"

    dev.solo("4")
    dev.solo("5")
    assert dev.isolationState()["soloObjects"] == ["3", "4", "5"], \
        f"soloing more objects must accumulate, not replace: {dev.isolationState()}"

    dev.solo("4")
    assert dev.isolationState()["soloObjects"] == ["3", "5"], dev.isolationState()

    dev.solo("")
    assert dev.isolationState()["soloObjects"] == [], dev.isolationState()

    dev.setSkipObject("3", False)
    dev.setSkipObject("3", True)
    dev.solo("3")
    st = dev.isolationState()
    assert st["soloObjects"] == ["3"] and "3" not in st["skipObjects"], \
        f"solo must remove the object from the skip list: {st}"

    dev.solo("9")
    dev.setSkipObject("3", True)
    st = dev.isolationState()
    assert st["soloObjects"] == ["9"] and "3" in st["skipObjects"], \
        f"skipping a soloed object must drop just that one: {st}"
    dev.clearIsolation()

    # source contract: the skip chip's handler lives in the chip, not on the row.
    # Scan CODE only (the explanatory comment at the old site names the banned form).
    src = (_ROOT / "src/lwe_ui/qml/ToolsPalette.qml").read_text(encoding="utf-8")
    code = "\n".join(ln.split("//", 1)[0] for ln in src.splitlines())
    assert not re.search(r"TapHandler\s*\{[^}]*target:\s*skipChip", code), \
        "no TapHandler may use `target: skipChip` - target does not scope tap bounds"
    chip = src.split("id: skipChip", 1)[1]
    chip = chip[:chip.index("HoverHandler")]
    assert "TapHandler" in chip and "setSkipObject" in chip, \
        "the skip chip must own its direct tap handler (declared inside the chip)"

    print("OK test_isolator_state - solo is a multi-select set; solo/skip mutually exclusive "
          "per object; skip chip owns its handler (no row-bounds target handler)")


if __name__ == "__main__":
    main()
