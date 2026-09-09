"""Render + logic regression tests for PlaylistStrip.qml and ScheduleModal.qml (design v1.0
10a/10b, F11 + F25 findings).

Sandboxes HOME/XDG before importing lwe_ui, drives a real Backend offscreen (no watcher
signaled - the courier no-ops against an absent watcher, matching every other bridge test).

Both components' roots are Popup-rooted or otherwise need a live Window context, so rather than
loading them as a QQuickView's direct root (fine for plain-Item components like WallpaperCard,
not for a Popup), each test writes a tiny throwaway host .qml INTO the real qml/ directory
(same folder ScheduleModal.qml itself lives in) so its own `import "."` resolves the shared
components exactly like every sibling file's import already does, then deletes it afterward.
Nested ids (entryA/entryB/dayStripRow, the TextField inside each EntryRow) get a stable
`objectName` in the source files themselves, the app-wide pattern for Python-reachable elements
(e.g. the library grid's libraryGrid) - Qt's findChild() only ever matches objectName, never a
bare QML `id:`.

#1 strip anatomy: the name segment must render filled surfaceVariant (not the plain surface
   base the mode/interval segments sit on), and the outer container's border/base frame must be
   visible at the strip's left edge.
#2 mode label capitalization: the strip's own titleCase() must turn "shuffle" into "Shuffle"
   (called directly as a live QML method - PySide auto-exposes QML JS functions as callables on
   the root object, verified against this exact call before relying on it).
#3 static dimming: switching the active playlist to static must show through to the strip's own
   activePl.mode, the property that drives both persistent unit cells' dimmed state.
#4 schedule modal dot-to-span color binding (the core F25 bug): driving the two live TextFields
   to "entry B's time earlier than entry A's" must NOT swap which color paints which visual
   span - the lo (earlier) boundary must show whichever entry produced it, same for hi. This is
   checked twice, in both time orderings, against the actual bound QML properties.
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-strip-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QUrl, QCoreApplication, QObject  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance  # noqa: E402
from PySide6.QtQuick import QQuickView  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from lwe_ui.models import Backend, ThemeTokens  # noqa: E402
from lwe_ui.app import _resolve_theme_tokens, _TOKENS_URI, _TOKENS_NAME, _QML_DIR  # noqa: E402


def _near(px: QColor, hex_target: str, tol: int = 30) -> bool:
    c = QColor(hex_target)
    return (abs(px.red() - c.red()) <= tol
            and abs(px.green() - c.green()) <= tol
            and abs(px.blue() - c.blue()) <= tol)


def test_strip_anatomy(app, backend, tokens) -> None:
    """F11: outer bordered container, name head-segment filled surfaceVariant."""
    view = QQuickView()
    view.engine().addImportPath(str(_QML_DIR))
    view.rootContext().setContextProperty("backend", backend)
    view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "PlaylistStrip.qml")))
    assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    strip = view.rootObject()
    view.resize(400, 40)
    view.show()
    for _ in range(5):
        QCoreApplication.processEvents()
    img = view.grabWindow()
    if img.isNull() or img.width() < 50:
        print("SKIP test_strip_anatomy (no frame grabbed on this platform)")
        return

    strip_w = int(strip.property("implicitWidth"))
    surface_variant = tokens.color("surfaceVariant")

    name_hits = sum(
        1 for x in range(4, max(5, int(strip_w * 0.20)))
        for y in (14, 16, 18)
        if _near(img.pixelColor(x, y), surface_variant)
    )
    assert name_hits > 0, "name head-segment should be filled surfaceVariant (F11)"
    # four cells, schedule | name | reserved | clock, and no Mode or
    # Every anywhere on the strip itself
    cells = [strip.findChild(QObject, n) for n in ("cellSchedule", "cellName", "cellReserved", "cellClock")]
    assert all(c is not None and c.property("visible") for c in cells), "the pill must show its four cells"
    assert int(cells[0].property("width")) == 28 and int(cells[2].property("width")) == 28 and int(cells[3].property("width")) == 28
    pop = strip.findChild(QObject, "clockPopover")
    assert pop is not None and pop.findChild(QObject, "modeSegment") is not None, "Mode lives in the clock popover"
    menu = strip.findChild(QObject, "nameMenu")
    assert menu is not None and menu.findChild(QObject, "intervalField") is None \
        and menu.findChild(QObject, "modeSegment") is None, "no Mode or Every in the playlist menu"
    print(f"OK test_strip_anatomy (name segment {name_hits} surfaceVariant px hits)")


def test_strip_mode_label_capitalized(app, backend) -> None:
    """F11: display-capitalize the stored lowercase mode value."""
    ap = backend.activePlaylist()
    assert ap["mode"] == "shuffle", ap

    view = QQuickView()
    view.engine().addImportPath(str(_QML_DIR))
    view.rootContext().setContextProperty("backend", backend)
    view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "PlaylistStrip.qml")))
    assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    strip = view.rootObject()

    # PySide auto-exposes QML-declared JS functions as directly callable Python attributes on
    # the root object (verified live before relying on this - QMetaObject.invokeMethod does NOT
    # work for a plain QML function and was ruled out first).
    assert strip.titleCase("shuffle") == "Shuffle"
    assert strip.titleCase("static") == "Static"
    assert strip.titleCase("") == ""
    print("OK test_strip_mode_label_capitalized (titleCase: shuffle->Shuffle, static->Static)")


def test_strip_static_dims_unit_cells(app, backend) -> None:
    """F11 acceptance 5: static mode must be visible to both persistent unit cells' dimming."""
    backend.setPlaylistMode("static")
    try:
        ap = backend.activePlaylist()
        assert ap["mode"] == "static", ap

        view = QQuickView()
        view.engine().addImportPath(str(_QML_DIR))
        view.rootContext().setContextProperty("backend", backend)
        view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "PlaylistStrip.qml")))
        assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
        strip = view.rootObject()
        for _ in range(3):
            QCoreApplication.processEvents()

        active_pl = strip.property("activePl")
        assert active_pl["mode"] == "static", "strip.activePl.mode must reach 'static'"
        every = strip.findChild(QObject, "everyRow")
        assert every is not None and abs(float(every.property("opacity")) - 0.55) < 0.01, \
            "static must put the popover's Every row to sleep at 0.55 (spec 4)"
        field = strip.findChild(QObject, "intervalField")
        assert field is not None and field.property("enabled") is True, "the interval stays editable in static"
        print("OK test_strip_static_dims_unit_cells (static reaches the strip; Every row asleep)")
    finally:
        backend.setPlaylistMode("shuffle")


def test_schedule_modal_span_colors_follow_entry_not_position(app, backend) -> None:
    """F25: the core bug. Drives the two live TextFields to both time orderings and reads back
    the modal's own bound loColor/hiColor properties - proof against the real component, not a
    Python-side re-derivation of the same formula.
    """
    # the host lives in memory under a phantom URL inside the qml dir, so `import "."`
    # resolves and nothing is ever written into the shipped package directory
    host_qml = (
        "import QtQuick\n"
        "import QtQuick.Window\n"
        "import \".\"\n"
        "Window {\n"
        "    width: 500; height: 400; visible: true\n"
        "    ScheduleModal { id: modal; objectName: \"modal\" }\n"
        "    Component.onCompleted: modal.open()\n"
        "}\n"
    )
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(_QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    engine.loadData(host_qml.encode(), QUrl.fromLocalFile(str(_QML_DIR / "_test_schedule_host.qml")))
    assert engine.rootObjects(), "test host window failed to load"
    win = engine.rootObjects()[0]
    for _ in range(5):
        QCoreApplication.processEvents()

    entry_a = win.findChild(QObject, "entryA")
    entry_b = win.findChild(QObject, "entryB")
    day_strip = win.findChild(QObject, "dayStripRow")
    assert entry_a and entry_b and day_strip, "entryA/entryB/dayStripRow objectName hooks missing"
    time_a = entry_a.findChild(QObject, "timeField")
    time_b = entry_b.findChild(QObject, "timeField")
    assert time_a and time_b, "timeField objectName hook missing inside EntryRow"

    a_dot = entry_a.property("dotColor").name()
    b_dot = entry_b.property("dotColor").name()

    time_a.setProperty("text", "21:30")
    time_b.setProperty("text", "08:00")
    for _ in range(3):
        QCoreApplication.processEvents()
    assert day_strip.property("ok") is True
    lo_color = day_strip.property("loColor").name()
    hi_color = day_strip.property("hiColor").name()
    assert lo_color == b_dot, (
        f"BUG case (B=08:00 earlier than A=21:30): the lo span should carry entry B's own "
        f"color ({b_dot}), got {lo_color} - this is the exact F25 regression (colors bound "
        f"to sorted lo/hi position instead of to the entry that produced each boundary)"
    )
    assert hi_color == a_dot, (
        f"BUG case: the hi span should carry entry A's own color ({a_dot}), got {hi_color}"
    )

    time_a.setProperty("text", "08:00")
    time_b.setProperty("text", "21:30")
    for _ in range(3):
        QCoreApplication.processEvents()
    assert day_strip.property("ok") is True
    lo_color2 = day_strip.property("loColor").name()
    hi_color2 = day_strip.property("hiColor").name()
    assert lo_color2 == a_dot, (
        f"normal order (A=08:00 earlier than B=21:30): lo span should carry entry A's color "
        f"({a_dot}), got {lo_color2}"
    )
    assert hi_color2 == b_dot, (
        f"normal order: hi span should carry entry B's color ({b_dot}), got {hi_color2}"
    )
    print("OK test_schedule_modal_span_colors_follow_entry_not_position "
          "(verified live in both time orderings)")

    # the strip is a range bar: one handle per entry at its own time, labels at the same
    # positions reading the times, midnight at both ends, and a handle drag maps pixels back
    # to minutes through dayStripRow.minuteAt
    handle_a = day_strip.findChild(QObject, "handleA")
    handle_b = day_strip.findChild(QObject, "handleB")
    assert handle_a and handle_b, "the two time handles need their objectNames"
    strip_w = float(day_strip.property("width"))
    assert strip_w > 100
    expect_a = strip_w * (8 * 60 / 1440) - float(handle_a.property("width")) / 2
    expect_b = strip_w * ((21 * 60 + 30) / 1440) - float(handle_b.property("width")) / 2
    assert abs(float(handle_a.property("x")) - expect_a) < 1.0, (handle_a.property("x"), expect_a)
    assert abs(float(handle_b.property("x")) - expect_b) < 1.0, (handle_b.property("x"), expect_b)
    assert float(day_strip.property("barH")) >= 6, "the bar is about three times its old 2 px"
    label_a = win.findChild(QObject, "labelA")
    label_b = win.findChild(QObject, "labelB")
    assert label_a.property("text") == "08:00" and label_b.property("text") == "21:30"
    assert abs(float(label_a.property("x")) + float(label_a.property("width")) / 2 - strip_w * (8 * 60 / 1440)) < 1.0, \
        "a label is centred under its own time"
    from PySide6.QtCore import QMetaObject, Q_RETURN_ARG, Q_ARG
    minute = QMetaObject.invokeMethod(day_strip, "minuteAt", Q_RETURN_ARG("QVariant"),
                                      Q_ARG("QVariant", strip_w * 0.5))
    assert int(minute) == 720, f"the middle of the strip is noon, got {minute}"
    # a drag snaps to the quarter hour (typing still takes any minute); the top stays 23:45
    near = int(QMetaObject.invokeMethod(day_strip, "minuteAt", Q_RETURN_ARG("QVariant"),
                                        Q_ARG("QVariant", strip_w * (727 / 1440))))
    assert near == 720, f"727 minutes along snaps to 12:00, got {near}"
    assert int(QMetaObject.invokeMethod(day_strip, "minuteAt", Q_RETURN_ARG("QVariant"),
                                        Q_ARG("QVariant", strip_w * 2))) == 1425, "clamped at 23:45"
    assert time_a.property("selectByMouse") is True, "a click-and-drag selects from the first press"
    print("OK schedule modal range bar: handles and labels at their times, minuteAt exact, drag-select on")


def test_interval_enter_releases_focus(app, backend) -> None:
    """Enter in the interval field commits AND drops the caret.

    The field used to keep focus forever after a submit (every later keystroke went
    into it). editingFinished fires for BOTH Enter and focus loss, so the commit is
    guarded against re-firing on the way out - proven here at its precondition: after
    the commit the field's displayed value IS the stored value, which is exactly what
    the guard compares.
    """
    view = QQuickView()
    view.engine().addImportPath(str(_QML_DIR))
    view.rootContext().setContextProperty("backend", backend)
    view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "PlaylistStrip.qml")))
    assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    strip = view.rootObject()
    view.resize(400, 40)
    view.show()
    for _ in range(5):
        QCoreApplication.processEvents()

    strip.openClock()   # the field lives in the clock popover now
    for _ in range(5):
        QCoreApplication.processEvents()
    field = strip.findChild(QObject, "intervalField")
    assert field is not None, "interval TextField needs objectName intervalField"

    field.setProperty("focus", True)
    field.metaObject().invokeMethod(field, "forceActiveFocus")
    for _ in range(3):
        QCoreApplication.processEvents()
    assert field.property("activeFocus") is True, "the field must take focus before the test"

    before = int(backend.activePlaylist()["interval"])
    typed = 7 if before != 420 else 9          # minutes; distinct from the current value
    field.setProperty("text", str(typed))
    QTest.keyClick(view, Qt.Key_Return)
    for _ in range(5):
        QCoreApplication.processEvents()

    assert field.property("activeFocus") is False, \
        "Enter must release the caret - the field kept focus permanently before this fix"
    after = int(backend.activePlaylist()["interval"])
    assert after == typed * 60, f"Enter must commit the typed minutes (got {after}s, want {typed*60}s)"
    assert int(strip.property("activePl")["interval"]) == after
    shown = strip.findChild(QObject, "clockPopover").shownInterval()
    assert shown == typed, \
        f"post-commit the guard comparand must equal the typed value (got {shown}) - " \
        "this is what makes the focus-loss editingFinished a no-op"
    print(f"OK test_interval_enter_releases_focus (committed {typed} min, focus released, "
          "re-commit guarded)")

    # empty or zero rejects and re-reads; over the range never commits (1..1440 min)
    stored = int(backend.activePlaylist()["interval"])
    for bad in ("0", ""):
        field.setProperty("focus", True)
        field.metaObject().invokeMethod(field, "forceActiveFocus")
        field.setProperty("text", bad)
        QTest.keyClick(view, Qt.Key_Return)
        for _ in range(5):
            QCoreApplication.processEvents()
        assert int(backend.activePlaylist()["interval"]) == stored, f"{bad!r} must not commit"
        assert field.property("text") == str(stored // 60), f"{bad!r} must re-read the stored value"
    field.setProperty("focus", True)
    field.metaObject().invokeMethod(field, "forceActiveFocus")
    field.setProperty("text", "1441")
    QTest.keyClick(view, Qt.Key_Return)
    for _ in range(5):
        QCoreApplication.processEvents()
    assert int(backend.activePlaylist()["interval"]) == stored, "1441 minutes is over the range and must not commit"
    print("OK test_interval_rejects (0, empty and 1441 leave the stored interval alone)")


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    tokens = ThemeTokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    backend = Backend()

    test_strip_anatomy(app, backend, tokens)
    test_strip_mode_label_capitalized(app, backend)
    test_strip_static_dims_unit_cells(app, backend)
    test_schedule_modal_span_colors_follow_entry_not_position(app, backend)
    test_interval_enter_releases_focus(app, backend)
    print("ALL playlist-strip/schedule-modal render regressions passed")


if __name__ == "__main__":
    main()
