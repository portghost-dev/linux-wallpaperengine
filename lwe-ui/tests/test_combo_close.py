"""Regression pins for the 2026-07-19 dropdown round.

  1. ThemedCombo click-to-close: the replaced popup lost the stock CloseOnPressOutsideParent
     policy, so a click on the control closed-on-press then reopened-on-release - every
     combo could open but never close by re-click. Simulate two real
     clicks and assert open -> closed.
  2. TapHandler tapped(eventPoint) position: the isolator chip-zone exclusion reads
     pt.position.x in the handler's parent coordinates. Prove the signature and the
     coordinate space behaviorally with clicks at known x positions.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_combo_close.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def main() -> None:
    from PySide6.QtCore import QUrl, QPoint, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView
    from PySide6.QtTest import QTest
    from PySide6.QtQml import qmlRegisterSingletonInstance
    from lwe_ui.models import ThemeTokens
    from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

    app = QGuiApplication.instance() or QGuiApplication(["t"])
    tokens = ThemeTokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)

    # ---- 1: ThemedCombo click-toggle ----------------------------------------------------
    # PySide cannot convert the QQuickPopup* property directly - surface its visible flag
    # through a QML-side bool on a wrapper instead.
    wrapper = ("import QtQuick\nimport \".\"\n"
               "Item { width: 300; height: 200\n"
               "  property alias popOpen: inner.popupOpen\n"
               "  ThemedCombo { id: cb; objectName: \"cbUnderTest\"; x: 0; y: 0; width: 200\n"
               "    model: [\"alpha\", \"beta\", \"gamma\"] }\n"
               "  Item { id: inner; property bool popupOpen: cb.popup.visible }\n"
               "}\n").encode()
    from PySide6.QtQml import QQmlComponent
    view = QQuickView()
    view.engine().addImportPath(str(_QML_DIR))
    comp0 = QQmlComponent(view.engine())
    comp0.setData(wrapper, QUrl.fromLocalFile(str(_QML_DIR / "inline-combotest.qml")))
    assert comp0.status() == QQmlComponent.Status.Ready, comp0.errorString()
    view.setContent(QUrl(), comp0, comp0.create())
    view.resize(300, 200)
    view.show()
    QTest.qWaitForWindowExposed(view)
    root0 = view.rootObject()
    QTest.qWait(50)
    center = QPoint(100, 13)

    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.qWait(120)
    assert root0.property("popOpen") is True, "first click must open the popup"

    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.qWait(160)
    assert root0.property("popOpen") is False, \
        "second click on the control must CLOSE the popup (CloseOnPressOutsideParent)"
    view.close()

    probe_qml = b"""
import QtQuick
Item {
    id: root
    width: 400; height: 40
    property real lastX: -1
    property int fires: 0
    TapHandler {
        onTapped: (pt) => { root.lastX = pt.position.x; root.fires++ }
    }
}
"""
    v2 = QQuickView()
    comp = QQmlComponent(v2.engine())
    comp.setData(probe_qml, QUrl.fromLocalFile(str(_QML_DIR / "inline-tapprobe.qml")))
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    v2.setContent(QUrl(), comp, comp.create())
    v2.resize(400, 40)
    v2.show()
    QTest.qWaitForWindowExposed(v2)
    root = v2.rootObject()

    QTest.mouseClick(v2, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(350, 20))
    QTest.qWait(80)
    assert root.property("fires") == 1, "TapHandler (pt) arrow handler must fire on click"
    x = float(root.property("lastX"))
    assert abs(x - 350) < 2, \
        f"eventPoint.position.x must be parent-item coordinates (got {x}, clicked 350)"

    QTest.mouseClick(v2, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, 20))
    QTest.qWait(80)
    assert abs(float(root.property("lastX")) - 30) < 2

    print("OK test_combo_close - re-click closes the ThemedCombo popup; "
          "TapHandler eventPoint.position.x proven in parent coordinates")


if __name__ == "__main__":
    main()
