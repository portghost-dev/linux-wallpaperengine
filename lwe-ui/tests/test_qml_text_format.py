"""Third-party strings render as plain text (src/lwe_ui/qml/*, discovery/project.py).

Wallpaper titles, scene object names, project property keys and engine log lines are not
authored by the panel. Every QML sink that shows one carries `textFormat: Text.PlainText`,
so Qt cannot read a tag-shaped title as rich text (which would paint markup inside the
panel and fetch an <img> over the network). The discovery layer also strips tags at the
source, which covers the attached tooltips that have no textFormat of their own.

Static on the QML side, no Qt runtime needed. Run: PYTHONPATH=src python3 tests/test_qml_text_format.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_QML = _ROOT / "src/lwe_ui/qml"
_SRC = str(_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# sink file -> the third-party bindings it renders (exact stripped line, or a prefix)
SINKS = {
    "WallpaperCard.qml": ["text: card.title"],
    "WorkshopTile.qml": ["text: tile.title"],
    "ModalFace.qml": ["text: face.title", "text: face.body"],
    "WizardFace.qml": ["text: face.title"],
    "RecordsFace.qml": ["text: face.title"],
    "Deck.qml": ["text: (deck.wizRev, wizardBridge.wpTitle())"],
    "DeckSettingsPopup.qml": ["text: (pop.rev, deckPopup.title())"],
    "TombstoneManager.qml": ["text: row.modelData.title"],
    "EditorView.qml": ["text: editor.title"],
    "ObjectsPanel.qml": ["text: modelData.label", "text: rowItem.modelData.label"],
    "DevSlotCard.qml": ["text: card.st.sceneTitle"],
    "DevIsolator.qml": ["text: rowItem.modelData.label", "text: rowItem.modelData.o ? rowItem.modelData.o.name"],
    "DevConsole.qml": ["text: line.text"],
    "DevRawEnv.qml": ["text: (door.rev, door.visible ? dev.launchPreview"],
}
WINDOW = 3  # lines on either side that still belong to the same element


def _plain_within(lines: list[str], idx: int) -> bool:
    lo, hi = max(0, idx - WINDOW), min(len(lines), idx + WINDOW + 1)
    return any("textFormat: Text.PlainText" in lines[j] for j in range(lo, hi))


def _is_text_element(lines: list[str], idx: int) -> bool:
    """Only Label and Text render markup; a TextField or a menu item shows plain text."""
    j = idx
    while j >= 0 and not re.search(r"\b[A-Z]\w*\s*\{", lines[j]):
        j -= 1
    element = re.search(r"\b([A-Z]\w*)\s*\{", lines[j]).group(1) if j >= 0 else ""
    return element in ("Label", "Text")


def check_qml_sinks() -> int:
    seen = 0
    for name, needles in SINKS.items():
        lines = (_QML / name).read_text(encoding="utf-8").split("\n")
        for needle in needles:
            hits = [i for i, ln in enumerate(lines) if ln.strip().startswith(needle)]
            assert hits, f"{name}: sink {needle!r} not found (renamed?)"
            for i in hits:
                if not _is_text_element(lines, i):
                    continue
                assert _plain_within(lines, i), f"{name}:{i + 1} renders third-party text without PlainText"
                seen += 1
    # the Deck now-playing names come through backend.titleOf inside block bindings
    deck = (_QML / "Deck.qml").read_text(encoding="utf-8").split("\n")
    blocks = [i for i, ln in enumerate(deck)
              if ln.strip() == "text: {" and any("titleOf" in deck[j] for j in range(i + 1, i + 5))]
    assert blocks, "Deck.qml: titleOf block bindings not found"
    for i in blocks:
        assert _plain_within(deck, i), f"Deck.qml:{i + 1} titleOf sink without PlainText"
        seen += 1
    return seen


def check_source_strip() -> None:
    from lwe_ui.discovery import objects, project

    assert project.strip_markup('<img src="http://x/y.png">Cozy<b>!</b>') == "Cozy !"
    assert project.strip_markup("  many   spaces ") == "many spaces"
    assert project.strip_markup(None) == ""
    assert project.strip_markup(42) == "42"
    with tempfile.TemporaryDirectory(prefix="lwe-title-") as tmp:
        wdir = Path(tmp) / "1234567"
        wdir.mkdir()
        (wdir / "project.json").write_text(json.dumps({
            "title": '<img src="http://evil/x.png">Title', "type": "scene", "file": "scene.pkg"}),
            encoding="utf-8")
        assert project.read(wdir)["title"] == "Title", project.read(wdir)["title"]
    # scene object names take the same path
    assert objects.project.strip_markup("<b>rock</b>") == "rock"


def main() -> None:
    n = check_qml_sinks()
    check_source_strip()
    print(f"OK: {n} third-party text sinks render as plain text; discovery strips markup at the source")


if __name__ == "__main__":
    main()
