"""Render/instantiation test for the developer cockpit + tools-palette QML.

Covers the three QML surfaces whose logic a static lint cannot see:
  #D2 the editable raw-env editor (Render tab) seeds from the queued env lines and Apply
      parses the typed block into dev.envLines() - a valid key queues, an invalid one is
      refused and surfaced.
  #S10 the Ref tab actually renders the chosen reference image: the refImage Image reaches
      Ready + visible once refPath points at a real PNG, and hides again when cleared.
  #S9 the Verdict context caption composes LIVE state (scene name + isolation) rather than
      the literal placeholder words.

Sandboxed HOME/XDG; bench_courier stubbed so nothing touches a real engine; no engine spawns
(the dev bridge only composes - it never starts a process in these paths).

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_dev_view_ui.py
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import zlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _write_png(path: str, w: int = 8, h: int = 8) -> None:
    """Write a tiny valid opaque-red PNG so the Ref Image has something real to load."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b""
    for _y in range(h):
        raw += b"\x00" + b"\xff\x00\x00" * w   # filter byte + RGB pixels
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


def _find(root, obj_name):
    from PySide6.QtCore import QObject
    return next((o for o in root.findChildren(QObject) if o.objectName() == obj_name), None)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-devui-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterSingletonInstance
        from PySide6.QtTest import QTest
        from lwe_ui import bench_courier
        from lwe_ui.models import ThemeTokens
        from lwe_ui.dev import DevBridge
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        settings.ensure_exists()
        bench_courier.wait_clear = lambda *a, **k: True
        bench_courier.standdown = lambda *a, **k: True
        bench_courier.resume = lambda *a, **k: True

        app = QGuiApplication.instance() or QGuiApplication(["t"])
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)

        engine = QQmlEngine()
        engine.addImportPath(str(_QML_DIR))
        dev = DevBridge()
        engine.rootContext().setContextProperty("dev", dev)

        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(_QML_DIR / "DevView.qml")))
        view = comp.create()
        assert view is not None, comp.errorString()
        subs = dev.subsystems()
        render_idx = list(subs).index("Render")
        view.setProperty("tab", render_idx)
        QTest.qWait(60)

        editor = _find(view, "devEnvEditor")
        assert editor is not None, "the raw-env editor TextArea did not mount on the Render tab"
        editor.setProperty("text", "LWE_POOL_HWM=768\n2BAD=x\nLWE_FBOPOOL=2")
        ok = editor.metaObject().invokeMethod(editor, "applyBlock")
        assert ok, "applyBlock() was not invokable"
        QTest.qWait(20)
        queued = {l["key"]: l["value"] for l in dev.envLines()}
        assert queued.get("LWE_POOL_HWM") == "768", queued
        assert queued.get("LWE_FBOPOOL") == "2", queued
        assert "2BAD" not in queued, "an invalid shell-identifier key must never queue"
        env = dev.compose_env()
        assert env.get("LWE_POOL_HWM") == "768" and env.get("LWE_FBOPOOL") == "2"
        assert "2BAD" not in env
        # the editor reseeds itself from the accepted queue (bad line dropped from the text)
        seeded = editor.property("text")
        assert "LWE_POOL_HWM=768" in seeded and "2BAD" not in seeded, seeded
        # a fresh editor instance seeds FROM the queue (round-trips the persisted-in-bridge state)
        comp2 = QQmlComponent(engine, QUrl.fromLocalFile(str(_QML_DIR / "DevView.qml")))
        view2 = comp2.create()
        view2.setProperty("tab", render_idx)
        QTest.qWait(60)
        editor2 = _find(view2, "devEnvEditor")
        assert editor2 is not None
        assert "LWE_POOL_HWM=768" in editor2.property("text"), editor2.property("text")
        dev.clearEnvLines()

        pcomp = QQmlComponent(engine, QUrl.fromLocalFile(str(_QML_DIR / "ToolsPalette.qml")))
        pal = pcomp.create()
        assert pal is not None, pcomp.errorString()
        pal.setProperty("targetName", "Test scene")

        ref_png = os.path.join(home, "ref.png")
        _write_png(ref_png)
        ref_img = _find(pal, "refImage")
        assert ref_img is not None, "the Ref tab refImage element is missing"
        assert ref_img.property("visible") is False, "no ref -> image hidden (honest empty)"
        # drive the ref path the way the FileDialog would (a file:// URL) and let it load
        pal.setProperty("tab", 3)   # Ref tab
        # set refPath on the Ref tab item (walk up to the Item that owns the property)
        owner = ref_img
        while owner is not None and owner.property("refPath") is None:
            owner = owner.parent()
        assert owner is not None, "could not find the refPath owner"
        owner.setProperty("refPath", QUrl.fromLocalFile(ref_png).toString())
        for _ in range(50):
            QTest.qWait(20)
            if ref_img.property("visible"):
                break
        # visible is bound to (refPath != "" && status === Image.Ready), so a True here proves
        # the Image actually reached the Ready state with the real PNG loaded.
        assert ref_img.property("visible") is True, (
            "the chosen reference image must render (Image Ready + visible)")
        owner.setProperty("refPath", "")
        QTest.qWait(40)
        assert ref_img.property("visible") is False, "cleared ref -> image hidden again"

        pal.setProperty("tab", 4)
        dev.setTarget("")
        dev.solo("42")
        QTest.qWait(40)
        from PySide6.QtCore import QObject
        cap = None
        for o in pal.findChildren(QObject):
            t = o.property("text")
            if isinstance(t, str) and t.startswith("Attaches automatically:"):
                cap = t
                break
        assert cap is not None, "the Verdict auto-attach caption was not found"
        assert "Test scene" in cap, cap
        assert "solo 42" in cap, cap
        assert "scene · time · isolation" not in cap, "caption must not print the placeholder words"

        # a multi-object solo counts instead of listing (several names blow the caption width)
        dev.solo("43")
        QTest.qWait(40)
        cap = None
        for o in pal.findChildren(QObject):
            t = o.property("text")
            if isinstance(t, str) and t.startswith("Attaches automatically:"):
                cap = t
                break
        assert "solo 2 objects" in cap, cap
        dev.clearIsolation()

        print("OK: dev-view UI - env-editor(seed/apply/refuse-bad)/ref-image-render/"
              "verdict-live-context all pass")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    main()
