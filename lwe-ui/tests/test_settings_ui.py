"""Settings surface structural + geometry regressions (offscreen render).

Every assertion in the previous file described a world this build deletes, so each is listed
here with the clause that kills it rather than quietly dropped:

  RETIRED  Normal/Advanced mode segment (`settingsModeSegment`, UI_MODE)   spec S11 + sec 5.1
           #34: the surface always shows everything; the key leaves the schema.
  RETIRED  Advanced disclosure section + Schedule containment inside it     spec S11, sec 4.1:
           the Schedule is an INLINE section, and disclosure anatomy is gone app-wide.
  RETIRED  `libraryInstallLocationRow` anatomy-only row                     spec sec 4.3 / 7 R4:
           a disabled row showing a path with no key behind it was a drawing, not a control;
           it dies into the real `Steam install` row with the new STEAM_DIR key.
  RETIRED  `_check_general_source_has_no_enabled_row` / `_check_no_local_modeseg_component`
           / `_check_b14_removals` static greps                             they policed the
           OLD page bodies, which no longer exist; the removals they guarded are re-asserted
           below against the new law (no SRow, no Save verb, no dead strings).

What replaces them: the acceptance tests of sec 10 that can be proven headlessly - shell
structure, row grammar, section anatomy, flush right edges, the scrollbar, the frozen-file
guards, the string sweep, and the Schedule gate exercised with the flag FORCED ON.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_ROOT = Path(__file__).resolve().parent.parent
_QML_SRC_DIR = _ROOT / "src" / "lwe_ui" / "qml"
_REWORKED = ("SettingsView.qml", "SettingsGeneral.qml", "SettingsEngine.qml",
             "SettingsLibrary.qml")


def _src(name: str) -> str:
    return (_QML_SRC_DIR / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    """Source with `//` comments stripped.

    The dead-string sweep is about what the SURFACE says, not what the file explains. The
    rewritten pages name the strings they killed, in comments, so that the next reader knows
    what used to be there - grepping the raw text would make documenting a removal look
    identical to failing to remove it.
    """
    out = []
    for line in _src(name).splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _test_shell_has_no_nav_column_or_page_title() -> None:
    """T1: the 190px nav column and the page titles are DELETED, not hidden."""
    view = _src("SettingsView.qml")
    assert "width: 190" not in view, "the 190px nav column must be deleted whole"
    assert "view.pageNames[view.pageIndex]" not in view, \
        "the page title is deleted - the segment names the page"
    assert "SegmentControl" in view, "page nav is a four-cell SegmentControl"
    assert 'sizeClass: "h24"' in view, "the page segment is the h24 size class (P16)"
    assert "Math.min(640" in view, "the content column stays capped at 640"
    assert "readonly property int groupW: 28 + 640 + 28" in view, \
        "the centring arithmetic must drop the nav terms or the column strands left (P17)"
    print("OK T1 shell: no nav column, no page title, h24 segment, 640 column")


def _test_no_srow_on_the_reworked_pages() -> None:
    """T2: SRow is replaced by the popup/editor row grammar on all three pages."""
    for name in _REWORKED:
        assert "SRow" not in _src(name), f"{name} must not instantiate SRow"
    assert (_QML_SRC_DIR / "SettingsRow.qml").exists(), "the replacement row grammar ships"
    print("OK T2 no SRow instance on any reworked page")


def _test_every_section_header_is_a_prule() -> None:
    """T3: header-with-rule is the ONLY section anatomy; no chevrons, no disclosure."""
    for name in ("SettingsGeneral.qml", "SettingsEngine.qml", "SettingsLibrary.qml"):
        text = _code(name)
        assert "PRule {" in text, f"{name} must use PRule for section headers"
        assert "SectionHeader" not in text, f"{name} must not use the dead SectionHeader"
        assert "IconChevron" not in text, f"{name} must carry no disclosure chevron"
        assert "advanced" not in text.replace("Advanced", ""), \
            f"{name} must carry no Normal/Advanced gate"
    print("OK T3 every section header is a PRule; no disclosure anatomy anywhere")


def _test_confirmpop_and_prule_are_new_standalone_files() -> None:
    """T30 / U13 / U14: both exist as NEW files, and neither was obtained by editing the
    frozen EditorView.qml."""
    for name in ("ConfirmPop.qml", "PRule.qml"):
        assert (_QML_SRC_DIR / name).exists(), f"{name} must ship as a standalone file"
    editor_view = _src("EditorView.qml")
    assert "component PRule" in editor_view, \
        "the editor keeps its own inline PRule - it was duplicated, never extracted"
    print("OK T30 ConfirmPop + PRule are new standalone files; the editor keeps its inline one")


def _test_frozen_surfaces_are_untouched() -> None:
    """T23: zero diff in the popup and the editor views; editor.py carries exactly the one
    authorized persistence change."""
    frozen = ["src/lwe_ui/qml/DeckSettingsPopup.qml", "src/lwe_ui/deck_popup.py",
              "src/lwe_ui/qml/EditorView.qml", "src/lwe_ui/qml/ObjectsPanel.qml"]
    proc = subprocess.run(["git", "diff", "--stat", "HEAD", "--"] + frozen,
                          cwd=str(_ROOT), capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        assert proc.stdout.strip() == "", \
            f"frozen surfaces must diff to zero lines:\n{proc.stdout}"

    proc = subprocess.run(["git", "diff", "-U0", "HEAD", "--", "src/lwe_ui/editor.py"],
                          cwd=str(_ROOT), capture_output=True, text=True, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        added = [ln for ln in proc.stdout.splitlines()
                 if ln.startswith("+") and not ln.startswith("+++")]
        removed = [ln for ln in proc.stdout.splitlines()
                   if ln.startswith("-") and not ln.startswith("---")]
        assert not removed, f"editor.py must lose nothing:\n{chr(10).join(removed)}"
        assert len(added) == 1 and "_persist_setting" in added[0], \
            f"editor.py may gain exactly the setAudioDial persistence line:\n{added}"
    print("OK T23 frozen surfaces diff to zero; editor.py carries only the P0 line")


def _test_dead_strings_are_gone() -> None:
    """T24: every string sec 8.5 names dies with its row or its rewrite, and no Save verb
    survives anywhere in the Settings family."""
    dead = [
        "Advanced reveals the disclosure sections on every page",
        "Also parks the engine while displays sleep",
        "Watcher restarts the engine above this",
        "Memory recycle",
        "Empty uses the engine default",
        "UV clamp mode at the wallpaper edges",
        "Auto-mute when another app plays audio",
        "Close LWE (frees VRAM)",
        "Stop the wallpaper (frees VRAM)",
        "Whitelist / blacklist",
        "Which layer-shell layer the wallpaper anchors to",
        "BC7 compression trades a little banding for a lot of VRAM",
        "Global timescale",
        "Per-wallpaper grades live in the editor",
        "Steam / Wallpaper Engine (source)",
        "LWE (destination)",
        "Where Steam drops subscribed items",
        "Passed as --assets-dir",
        "Really reset?",
        "Clean up",
    ]
    joined = "\n".join(_code(n) for n in _REWORKED)
    for phrase in dead:
        assert phrase not in joined, f"dead string still on the surface: {phrase!r}"
    for name in _REWORKED:
        text = _code(name)
        assert 'text: "Save"' not in text, f"{name} must carry no Save verb (S1)"
        assert "\u2026" not in _src(name), f"{name} must carry no literal ellipsis (check_text)"
    assert "Per-wallpaper overrides live in the editor" not in joined, \
        "the struck Audio response footnote must not ship"
    print("OK T24 every sec 8.5 string is gone; no Save verb; no literal ellipsis")


def _test_ruled_strings_are_verbatim() -> None:
    """S6 / sec 8: the ruled strings are law, character for character."""
    general, engine, library = (_src("SettingsGeneral.qml"), _src("SettingsEngine.qml"),
                                _src("SettingsLibrary.qml"))
    for text, phrase in (
        (general, 'label: "Start on login"'),
        (general, 'label: "Close to tray"'),
        (general, 'caption: "The app keeps running in the tray"'),
        (general, 'label: "Switch playlists by time of day"'),
        (general, '"Changes at the next rotation, never mid-wallpaper."'),
        (general, 'label: "Engine mode"'),
        (general, 'caption: "Set by the service file"'),
        (general, 'text: "Open logs"'),
        (general, 'caption: "Reset keeps the engine mode"'),
        (engine, 'label: "FPS"'),
        (engine, 'label: "Speed"'),
        (engine, 'label: "Scaling"'),
        (engine, 'caption: "When another app plays audio"'),
        (engine, 'caption: "Wallpapers respond to what is playing"'),
        (engine, 'caption: "Trades a little banding for a lot of video memory"'),
        (library, '"Steam install"'),
        (library, '"Workshop content"'),
        (library, '"Library folder"'),
        (library, '"Engine assets"'),
        (library, 'text: "Browse"'),
        (library, 'caption: "New items wait in Workshop until you approve them"'),
        (library, 'caption: "Copy in survives unsubscribes; Reference saves disk"'),
        (library, 'caption: "Trashed items that never reimport"'),
        (library, 'text: "Rescan now"'),
    ):
        assert phrase in text, f"ruled string missing or altered: {phrase}"
    print("OK sec 8 ruled strings present verbatim")


def _test_cut_keys_have_no_qml_row() -> None:
    """T21's UI half: no QML file anywhere reads or writes a cut key."""
    cut = ["TRANSITION", "AVOID_REPEAT", "MONITOR_MODE", "POLL", "MEMCAP_MB", "ACTIVE_CHECK",
           "PAUSE_ON_BATTERY", "UI_MODE", "NOTIFY", "SHOW_TRAY", "LOG_LEVEL"]
    for qml in _QML_SRC_DIR.glob("*.qml"):
        text = _code(qml.name)
        for key in cut:
            assert f'"{key}"' not in text, f"{qml.name} still references the cut key {key}"
    print("OK T21 no QML file references any of the eleven cut keys")


def _test_fullscreen_legacy_keys_have_no_row_but_keep_their_keys() -> None:
    """T14: the two-combo `Pause or stop` row is gone; both legacy keys survive as
    migration input to the derive path."""
    engine = _code("SettingsEngine.qml")
    for key in ("PAUSE_RECOVERY_CONDITION", "PAUSE_RECOVERY_ACTION"):
        assert key not in engine, f"{key} must have no UI row"
    models = (_ROOT / "src/lwe_ui/models.py").read_text(encoding="utf-8")
    assert "PAUSE_RECOVERY_ACTION" in models and "PAUSE_RECOVERY_CONDITION" in models, \
        "both keys stay in the legacy derive path"
    print("OK T14 legacy pause keys lose their row and keep their key")


def _test_no_xdg_open_text_editor_on_the_surface() -> None:
    """T16: the `Edit` verb that opened a text editor dies with its row."""
    for name in _REWORKED:
        text = _code(name)
        assert "editBlacklist" not in text and "editWhitelist" not in text, \
            f"{name} must not open a pause list in an external text editor"
    print("OK T16 no xdg-open into a text editor anywhere on the surface")


def _test_released_halts_are_actually_built() -> None:
    """This release unblocked six string-blocked rows and two units. Each is
    asserted here so a "resolved" HALT cannot quietly stay unbuilt."""
    engine, library, general = (_code("SettingsEngine.qml"), _code("SettingsLibrary.qml"),
                                _code("SettingsGeneral.qml"))

    assert 'label: "While a game or app is fullscreen"' in engine
    assert '["off", "pause", "stop"]' in engine, \
        "full reclaim rides the existing `stop`; the enum gains no fourth member"
    assert '"Keep playing"' in engine, "entry 1 stays the ruled string"

    assert 'childLabel: "Exceptions"' in engine
    assert 'childCaption: "These apps never trigger the rule"' in engine
    assert 'childLabel: "Apps"' in engine
    assert 'childCaption: "The rule applies while any of these is open"' in engine
    assert 'PRule { label: "App rules" }' in engine, "the section header is App rules (A1)"
    assert "AppListPopup" in engine, "both Edit doors open the shared list editor"
    assert (_QML_SRC_DIR / "AppListPopup.qml").exists()
    assert "exceptionsList" not in engine, "the rejected inline exceptions editor must stay dead"
    assert not (_QML_SRC_DIR / "ExceptionsEditor.qml").exists(), \
        "the rejected modal exceptions editor stays deleted"
    assert "exceptionsAddPill" not in engine, "the rejected inline editor stays dead"
    popup = _code("AppListPopup.qml")
    assert '"Process name"' in popup and '"App id"' in popup, \
        "the placeholder names the identifier space per list (S-18)"
    assert 'visible: pop.forApps' in popup, \
        "Browse is an apps-only door - a file cannot resolve to a window class (S-18)"

    for text, label, entries in (
        (engine, "Edge behavior", ["Engine default", "Hold the edge pixels"]),
        (engine, "Wayland layer", ["Background", "Overlay"]),
        (engine, "Video decode", ["Software", "Hardware when available"]),
        (engine, "Texture detail", ["Automatic", "Full"]),
        (library, "Detect new items", ["On launch", "On a timer"]),
    ):
        assert f'label: "{label}"' in text, f"{label} row must be built"
        for entry in entries:
            assert f'"{entry}"' in text, f"{label} menu missing {entry!r}"

    assert 'prompt: "Reset all settings?"' in general and 'verb: "Reset"' in general
    assert "enabled: false" not in general, "the Reset verb must no longer be inert"

    assert '"These apply to every wallpaper."' in engine

    assert (_ROOT / "src/lwe_ui/tray.py").exists()
    assert "onClosing" not in _src("Main.qml"), \
        "the close is intercepted from Python; Main.qml stays at HEAD (S13)"
    print("OK released HALTs are built: fullscreen story, exceptions editor, five menus, "
          "reset copy, audio footnote, tray")


def _test_no_raw_enum_reaches_the_user() -> None:
    """S6's real target: the surface must never render a schema token as copy."""
    raw = ["clamp", "border", "repeat", "nvdec", "stretch", "interval", "manual",
           "scene", "video", "overlay", "bottom"]
    for name in ("SettingsEngine.qml", "SettingsLibrary.qml"):
        text = _code(name)
        for line in text.splitlines():
            if not any(k in line for k in ("model: [", "text: \"", "label: \"", "caption: \"")):
                continue
            if "vals" in line or "readonly property var vals" in line:
                continue
            for token in raw:
                assert f'"{token}"' not in line, \
                    f"{name}: raw enum {token!r} rendered as copy: {line.strip()}"
    print("OK no raw lowercase enum is rendered to the user anywhere on the surface")


def _test_regenerate_is_never_reachable_before_the_dial_generator() -> None:
    """T26: write_files() is called only from the sec 6.5 path, and that generator already
    emits the three dial lines (the sec 1.2 sequencing law)."""
    callers = []
    for path in (_ROOT / "src").rglob("*.py"):
        if path.name == "daemon_unit.py":
            continue
        if "write_files(" in path.read_text(encoding="utf-8"):
            callers.append(path.relative_to(_ROOT).as_posix())
    assert callers == ["src/lwe_ui/settings_bridge.py"], \
        f"write_files() must be called only from the settings bridge, found {callers}"
    gen = (_ROOT / "src/lwe_ui/engine/daemon_unit.py").read_text(encoding="utf-8")
    assert "AUDIO_DIAL_ENV" in gen, \
        "the only caller exists ONLY because the generator already emits the dial lines"
    print("OK T26 the single write_files() caller is the sec 6.5 path, generator dial-aware")


def _shape(value) -> int:
    """Cursor shapes come back as a QEnum on some property reads and an int on others."""
    return int(getattr(value, "value", value))


def _live() -> None:
    home = tempfile.mkdtemp(prefix="lwe-setui-")
    orig = {k: os.environ.get(k) for k in
            ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        import sys
        sys.path.insert(0, str(_ROOT / "src"))
        from PySide6.QtCore import QUrl, QPointF, Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest
        from PySide6.QtQml import (QQmlApplicationEngine, QQmlComponent,
                                   qmlRegisterSingletonInstance)
        from lwe_ui import constants as C
        from lwe_ui.models import Backend, ImportBridge, ThemeBridge, ThemeTokens
        from lwe_ui.settings_bridge import SettingsBridge
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import (_resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME)

        paths.ensure_dirs()
        settings.ensure_exists()

        app = QGuiApplication.instance() or QGuiApplication(["t"])
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(_QML_DIR))
        backend = Backend()
        import_bridge = ImportBridge(backend)
        sb = SettingsBridge(backend, import_bridge)
        rc = engine.rootContext()
        rc.setContextProperty("backend", backend)
        rc.setContextProperty("importBridge", import_bridge)
        rc.setContextProperty("settingsBridge", sb)
        rc.setContextProperty("themeBridge", ThemeBridge(tokens))

        comp = QQmlComponent(engine)
        comp.setData(b'''
import QtQuick
import QtQuick.Window
import "."
Window { width: 1400; height: 620; visible: true
         SettingsView { anchors.fill: parent } }
''', QUrl.fromLocalFile(str(_QML_DIR / "host.qml")))
        win = comp.create()
        assert win is not None, comp.errorString()
        QTest.qWait(200)
        view = win.findChild(QQuickItem, "settingsView")
        assert view is not None, "SettingsView not mounted"

        def walk(item):
            yield item
            for c in item.childItems():
                yield from walk(c)

        def cls(o):
            return o.metaObject().className().split("_QMLTYPE")[0].split("_QML_")[0]

        def rect(it):
            tl = it.mapToScene(QPointF(0, 0))
            return tl.x(), tl.y(), it.width(), it.height()

        for page_index in (0, 1, 2):
            view.setProperty("pageIndex", page_index)
            QTest.qWait(150)
            items = list(walk(view))

            col = next(i for i in items
                       if i.property("objectName") == "settingsContentColumn")
            cx, cy, cw, _ = rect(col)
            assert abs(cw - 640) < 0.51, f"page {page_index}: content column {cw}, expected 640"
            # S3's "flush right" is now flush at the CONTENT right edge, which reserves the
            # 16px scrollbar clearance (defect 2). The column is still 640; content ends 16
            # short of it so the bar has somewhere to be that is not on top of a control.
            right_edge = cx + cw - 16

            seg = next(i for i in items
                       if i.property("objectName") == "settingsPageSegment")
            sx, sy, _, sh = rect(seg)
            assert abs(sh - 24) < 0.51, f"page {page_index}: segment h {sh}, expected 24"
            assert abs(sx - cx) < 0.51, "segment left edge is flush with the column's"
            assert abs((sy - cy) - 18) < 0.51, f"segment top margin {sy - cy}, expected 18"

            bar = next(i for i in items
                       if i.property("objectName") == "settingsScrollBar")
            handle = bar.property("contentItem")
            hx, _, hw, _ = rect(handle)
            assert abs(hw - 4) < 0.51, f"scrollbar handle {hw}px, expected 4"
            # the 3px inset is measured against the COLUMN edge, not the content edge -
            # the bar rides in the clearance band, content stops before it
            column_right = cx + cw
            assert abs((column_right - (hx + hw)) - 3) < 0.51, \
                f"the handle rides 3px inside the column right edge, measured " \
                f"{column_right - (hx + hw):.2f}"
            assert bar.property("background") is None, "no track (S10)"

            rules = [i for i in items if cls(i) == "PRule" and i.isVisible()]
            assert rules, f"page {page_index} must have section headers"
            rx, _, rw, _ = rect(rules[0])
            assert abs(right_edge - (rx + rw)) < 0.51, \
                "the section rule runs to the content right edge, clearance included"
            for r in rules:
                assert abs(rect(r)[3] - 24) < 0.51, f"PRule height {rect(r)[3]}, expected 24"

            rows = [i for i in items if cls(i) == "SettingsRow" and i.isVisible()]
            assert rows, f"page {page_index} must have rows"
            for r in rows:
                want = 40 if r.property("caption") else 34
                got = rect(r)[3]
                assert abs(got - want) < 0.51, \
                    f"page {page_index} row {r.property('label')!r}: {got}, spec {want}"
                slots = [c for c in r.childItems()
                         if cls(c) == "QQuickItem" and c.width() > 0]
                for s in slots:
                    sx2, _, sw2, _ = rect(s)
                    assert abs(right_edge - (sx2 + sw2)) < 0.51, \
                        f"page {page_index} row {r.property('label')!r} control not flush"

            for c in [i for i in items if cls(i) == "SettingsCombo" and i.isVisible()]:
                _, _, w, h = rect(c)
                want = (78, 24) if c.property("compact") else (150, 26)
                assert (abs(w - want[0]) < 0.51 and abs(h - want[1]) < 0.51), \
                    f"dropdown {w}x{h}, spec {want[0]}x{want[1]}"
            for v in [i for i in items if cls(i) == "SettingsVerb" and i.isVisible()]:
                assert abs(rect(v)[3] - 24) < 0.51, "verb buttons are h24"

        print("OK T1/T2/T3/T4/T5 measured live: 640 column, h24 segment @18, 34/40 rows, "
              "flush right edges, 24px PRule, 4px trackless bar 3px inside")

        occlusion_report = []
        for page_index in (0, 1, 2, 3):
            view.setProperty("pageIndex", page_index)
            QTest.qWait(200)
            items = list(walk(view))

            bar = next(i for i in items
                       if i.property("objectName") == "settingsScrollBar")
            handle = bar.property("contentItem")
            hx, _, hw, _ = rect(handle)
            bar_left = hx

            shell = next(i for i in items
                         if i.property("objectName") == "settingsShellColumn")
            bar_items = set(id(i) for i in walk(bar))
            content_right = 0.0
            worst = None
            for it in walk(shell):
                if id(it) in bar_items or not it.isVisible() or it.width() <= 0:
                    continue
                ix, _, iw, _ = rect(it)
                if ix + iw > content_right:
                    content_right = ix + iw
                    worst = it

            clear = bar_left - content_right
            occlusion_report.append((page_index, round(content_right, 2),
                                     round(bar_left, 2), round(clear, 2)))
            assert content_right <= bar_left - 9 + 0.51, (
                f"page {page_index}: content reaches {content_right:.2f} but the scrollbar "
                f"handle starts at {bar_left:.2f} - only {clear:.2f}px of clear, needs 9. "
                f"Worst item: {cls(worst) if worst else '?'} "
                f"{worst.property('label') if worst else ''}"
            )
        for page_index, cr, bl, clear in occlusion_report:
            print(f"OK DEFECT-2 page {page_index}: content right {cr}, bar left {bl}, "
                  f"clear {clear}px (needs >= 9)")

        view.setProperty("pageIndex", 1)
        QTest.qWait(200)
        items = list(walk(view))
        slider = next(i for i in items
                      if i.property("objectName") == "settingsSpeedSlider")
        chip = next(i for i in items if i.property("objectName") == "settingsSpeedChip")

        committed_before = sb.value("ENGINE_TIMESCALE")
        text_before = chip.property("displayText")
        slider.setProperty("value", 0.85)
        QTest.qWait(80)
        text_during = chip.property("displayText")
        assert text_during != text_before, (
            f"the chip must track the slider during a drag: it read {text_before!r} "
            f"before and {text_during!r} after the position moved"
        )
        assert sb.value("ENGINE_TIMESCALE") == committed_before, (
            "moving the slider must NOT commit - the write still belongs to the release"
        )
        slider.setProperty("value", 0.30)
        QTest.qWait(80)
        assert chip.property("displayText") != text_during, "and it keeps tracking"
        assert sb.value("ENGINE_TIMESCALE") == committed_before, "still nothing committed"
        print(f"OK DEFECT-1 Speed chip live during drag: {text_before} -> {text_during} "
              f"-> {chip.property('displayText')}, store untouched throughout")

        cursor_findings = []
        for page_index in (0, 1, 2):
            view.setProperty("pageIndex", page_index)
            QTest.qWait(150)
            for it in walk(view):
                if not it.isVisible():
                    continue
                parent = it.parentItem()
                if parent is not None and cls(parent) == "SettingsCombo" \
                        and bool(parent.property("freeEntry")):
                    continue
                shape = it.property("cursorShape")
                if shape is None:
                    continue
                if _shape(shape) == _shape(Qt.CursorShape.IBeamCursor):
                    parent_cls = cls(it.parentItem()) if it.parentItem() else "?"
                    cursor_findings.append((page_index, cls(it), parent_cls,
                                            it.isVisible()))
        assert not cursor_findings, (
            f"a select-only control exposes an I-beam region: {cursor_findings}"
        )
        for page_index in (0, 1, 2):
            view.setProperty("pageIndex", page_index)
            QTest.qWait(150)
            combos = [i for i in walk(view)
                      if cls(i) == "SettingsCombo" and i.isVisible()
                      and not bool(i.property("freeEntry"))]
            for combo in combos:
                hovers = [h for h in combo.children()
                          if "HoverHandler" in h.metaObject().className()]
                assert hovers, f"page {page_index}: a dropdown with no cursor promise"
                want = _shape(Qt.CursorShape.PointingHandCursor)
                assert any(_shape(h.property("cursorShape")) == want for h in hovers), (
                    f"page {page_index}: a dropdown must promise the pointing hand"
                )
        print("OK DEFECT-5 every dropdown promises the pointing hand; "
              "no I-beam region anywhere on the three pages")

        view.setProperty("pageIndex", 1)
        QTest.qWait(200)
        assert next((i for i in walk(view)
                     if i.property("objectName") == "exceptionsList"), None) is None, \
            "the rejected inline exceptions editor must stay dead"
        rule_children = [i for i in walk(view) if cls(i) == "RuleChild"]
        assert len(rule_children) == 2, \
            f"both rules carry their child row, got {len(rule_children)}"
        assert {c.property("kind") for c in rule_children} == {"exceptions", "apps"}
        print("OK A1 both rule children mounted; on-page editing stays dead")

        from PySide6.QtCore import QMetaObject, Q_ARG
        eng_page = next(i for i in walk(view) if cls(i) == "SettingsEngine")
        QMetaObject.invokeMethod(eng_page, "openListEditor",
                                 Qt.ConnectionType.DirectConnection,
                                 Q_ARG("QVariant", "apps"))
        QTest.qWait(250)

        def qwalk(o):
            yield o
            for c in o.children():
                yield from qwalk(c)
        le = next(o for o in qwalk(eng_page)
                  if "AppListPopup" in o.metaObject().className())
        assert le.property("visible") is True, "Edit must open the list editor"
        le_body = le.property("contentItem")
        le_bg = le.property("background")
        card_right = le_bg.mapToScene(QPointF(0, 0)).x() + le_bg.width()
        le_col = next(i for i in walk(le_body) if cls(i) == "QQuickColumn")
        row_right = le_col.mapToScene(QPointF(0, 0)).x() + le_col.width()
        le_bar = next(i for i in walk(le_body)
                      if cls(i) in ("QQuickScrollBar", "ScrollBar"))
        handle_right = le_bar.mapToScene(QPointF(0, 0)).x() + le_bar.width() - 3
        handle_left = handle_right - 4
        assert handle_left - row_right >= 8 - 0.51, (
            f"popup bar overlaps the rows: handle left {handle_left}, "
            f"row edge {row_right}")
        assert 2 <= card_right - handle_right <= 6, (
            f"popup bar is not riding the card padding: edge gap "
            f"{card_right - handle_right}")
        card_cx = le_bg.mapToScene(QPointF(0, 0)).x() + le_bg.width() / 2
        assert abs(card_cx - win.property("width") / 2) < 2, (
            f"popup must center in the window, card center x {card_cx}")
        win.setProperty("height", 420)
        QTest.qWait(200)
        assert le.property("height") <= 420 - 24 + 0.51, (
            f"popup must fit a compact window: h {le.property('height')} in 420")
        win.setProperty("height", 620)
        QTest.qWait(150)
        QMetaObject.invokeMethod(le, "close", Qt.ConnectionType.DirectConnection)
        QTest.qWait(100)
        print("OK A1 popup: bar in the card padding, centered, fits a compact window")

        view.setProperty("pageIndex", 1)
        QTest.qWait(150)
        engine_page = next(i for i in walk(view)
                           if cls(i) == "SettingsEngine" and i.isVisible())
        volume_row = next(r for r in walk(engine_page)
                          if cls(r) == "SettingsRow" and r.property("label") == "Volume")
        rev_before = engine_page.property("rev")
        backend.setSetting("ENGINE_VOLUME", 31)
        QTest.qWait(120)
        assert engine_page.property("rev") > rev_before, \
            "a write from any other surface must bump the page's refresh revision (S8)"
        assert int(volume_row.property("current")) == 31, \
            "the Volume row must re-read the store without a page switch (T6)"

        toggle_row = next(r for r in walk(engine_page)
                          if cls(r) == "SettingsRow" and r.property("label") == "Parallax")
        before = bool(sb.value("PARALLAX_DEFAULT"))
        backend.setSetting("PARALLAX_DEFAULT", not before)
        QTest.qWait(120)
        switch = next(s for s in walk(toggle_row) if cls(s) == "ThemedSwitch")
        assert bool(switch.property("checked")) is (not before), \
            "a toggle is not a load-time snapshot either"
        print("OK T6 live refresh: a write from another door updates the open page in place")

        view.setProperty("pageIndex", 0)
        QTest.qWait(120)
        section = next((i for i in walk(view)
                        if i.property("objectName") == "scheduleSection"), None)
        assert section is not None, "the Schedule section must be BUILT, even while gated"
        assert C.SCHEDULE_UI is False and section.property("visible") is False, \
            "gated off means ABSENT, not dim and not disabled-and-visible"

        saved = C.SCHEDULE_UI
        try:
            C.SCHEDULE_UI = True
            view.setProperty("pageIndex", 1)
            QTest.qWait(80)
            view.setProperty("pageIndex", 0)
            QTest.qWait(150)
            section = next(i for i in walk(view)
                           if i.property("objectName") == "scheduleSection")
            assert section.property("visible") is True, \
                "flipping the one constant must render the section, fully wired"

            sec_rows = [i for i in walk(section)
                        if cls(i) == "SettingsRow" and i.isVisible()]
            labels = [r.property("label") for r in sec_rows]
            assert "Switch playlists by time of day" in labels, labels
            assert "Daytime playlist" in labels and "Night playlist" in labels, labels

            assert sb.commit("SCHEDULE_ENABLED", True) is True
            section.setProperty("packed", None)
            assert sb.commit("SCHEDULE", "07:30=day;19:00=night") is True
            assert settings.load()["SCHEDULE"] == "07:30=day;19:00=night"
            assert sb.commit("SCHEDULE", "7:30=day") is False, "HH:MM validation is live"

            assert sb.commit("SCHEDULE_ENABLED", False) is True
            QTest.qWait(120)
            dimmed = [r for r in walk(section)
                      if cls(r) == "SettingsRow" and r.property("label") == "Daytime playlist"]
            assert dimmed and abs(dimmed[0].property("opacity") - 0.5) < 0.01, \
                "a row whose precondition is off renders at 0.5 opacity"
            print("OK T29 Schedule gate: absent at False, fully wired and packing at True")
        finally:
            C.SCHEDULE_UI = saved
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main() -> None:
    _test_shell_has_no_nav_column_or_page_title()
    _test_no_srow_on_the_reworked_pages()
    _test_every_section_header_is_a_prule()
    _test_confirmpop_and_prule_are_new_standalone_files()
    _test_frozen_surfaces_are_untouched()
    _test_dead_strings_are_gone()
    _test_ruled_strings_are_verbatim()
    _test_cut_keys_have_no_qml_row()
    _test_fullscreen_legacy_keys_have_no_row_but_keep_their_keys()
    _test_no_xdg_open_text_editor_on_the_surface()
    _test_released_halts_are_actually_built()
    _test_no_raw_enum_reaches_the_user()
    _test_regenerate_is_never_reachable_before_the_dial_generator()
    _live()
    print("ALL settings UI regressions passed")


if __name__ == "__main__":
    main()
