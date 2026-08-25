"""ThemeBridge + Settings > Theme page (v1.4 phase 2).

Bridge contract:
  * setActive pushes the resolved tokens live into ThemeTokens (the app IS the preview)
  * setRoleLive parses, persists as an overlay, pushes; garbage returns False untouched
  * beginEdit/revertEdit restores the exact values at open (the picker's Esc law)
  * resetActive drops the active theme's overlay (factory restored)
  * themeList carries 17 rows with per-theme effective accents (menu identity preview)

Page load (offscreen): SettingsTheme.qml instantiates against the real bridge with six
role rows and live values.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_theme_bridge.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TMP = tempfile.mkdtemp(prefix="lwe-themebridge-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")


def main() -> None:
    from PySide6.QtGui import QGuiApplication
    from lwe_ui.models import ThemeBridge, ThemeTokens
    from lwe_ui.storage import paths, themes

    app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
    paths.ensure_dirs()

    tokens = ThemeTokens(themes.resolve_active())
    tb = ThemeBridge(tokens)

    assert tb.activeKey() == "oledElectric"
    assert tokens.color("accent") == "#3790FF"

    changes = {"n": 0}
    tb.changed.connect(lambda: changes.__setitem__("n", changes["n"] + 1))
    tb.setActive("neonDusk")
    assert tb.activeKey() == "neonDusk"
    assert tokens.color("accent") == "#FF2E97", "setActive must push resolved tokens live"
    assert tokens.color("base") == "#12081F"
    assert changes["n"] == 1
    assert "neon pink" in tb.activeBlurb()

    assert tb.setRoleLive("accent", "rgb(0, 128, 255)") is True
    assert tokens.color("accent") == "#0080FF"
    assert tb.roleValue("accent") == "#0080FF"
    assert themes.base_roles("neonDusk")["accent"] == "#FF2E97", "factory untouched"
    assert tb.setRoleLive("accent", "not a color") is False
    assert tokens.color("accent") == "#0080FF", "garbage must change nothing"
    assert tb.setRoleLive("nonsenseRole", "#FFFFFF") is False

    # picker session: Esc revert restores the exact at-open values
    tb.beginEdit()
    tb.setRoleLive("accent", "#111111")
    tb.setRoleLive("text", "#222222")
    assert tokens.color("accent") == "#111111"
    tb.revertEdit()
    assert tokens.color("accent") == "#0080FF", "revert must restore the value at open"
    assert tb.roleValue("text") == themes.base_roles("neonDusk")["text"]

    tb.beginEdit()
    tb.setRoleLive("accent", "#ABCDEF")
    tb.endEdit()
    assert tokens.color("accent") == "#ABCDEF"

    tb.resetActive()
    assert tokens.color("accent") == "#FF2E97"
    assert tb.roleValue("accent") == "#FF2E97"

    lst = tb.themeList()
    assert len(lst) == 17
    assert lst[0]["accent"] == "#3790FF" and lst[0]["custom"] is False
    # chip data (v1.4.1): fill + per-chip polarity for the menu chips
    assert lst[0]["background"] == "#000000" and lst[0]["dark"] is True
    light_row = next(r for r in lst if r["key"] == "cozyPink")
    assert light_row["dark"] is False
    assert all(r["custom"] for r in lst[14:])
    assert lst[15]["name"] == "Custom 2"

    assert tb.isDark() is True
    tb.setActive("cozyPink")
    assert tb.isDark() is False
    assert tokens.color("danger") == "#C93A38", "light status trio must apply"
    tb.setActive("oledElectric")

    from PySide6.QtCore import QUrl, QObject
    from PySide6.QtQuick import QQuickView
    from PySide6.QtQml import qmlRegisterSingletonInstance
    from lwe_ui.app import _QML_DIR, _TOKENS_URI, _TOKENS_NAME

    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    view = QQuickView()
    view.engine().addImportPath(str(_QML_DIR))
    view.rootContext().setContextProperty("themeBridge", tb)
    view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "SettingsTheme.qml")))
    assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    root = view.rootObject()
    root.setProperty("width", 640)

    # walk the VISUAL tree: Repeater delegates are owned by the delegate model, not
    # QObject-parented under the root, so findChildren() never sees them (proven with a
    # tree dump - childItems() showed all six delegates while findChildren showed none)
    def visual_items(item):
        out = []
        for c in item.childItems():
            out.append(c)
            out.extend(visual_items(c))
        return out

    fields = [o for o in visual_items(root)
              if str(o.objectName()).startswith("themeHex_")]
    assert len(fields) == 6, f"six role hex fields expected, found {len(fields)}"
    texts = sorted(str(f.property("text")) for f in fields)
    assert "#000000" in texts and "#3790FF" in texts, texts

    print("OK test_theme_bridge - live push, overlay persistence, Esc revert, reset, "
          "17-row menu list, page loads with six live role fields")


if __name__ == "__main__":
    main()
