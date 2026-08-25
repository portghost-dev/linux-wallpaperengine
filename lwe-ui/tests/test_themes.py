"""Theme store v1.4 - factory integrity, derivation law, fallback rules, persistence.

Pins the following rules:
  * exactly 14 factory palettes (9 dark + 5 light), all hexes valid, blurbs present
  * custom slots seed from Dark
  * luminance invariant: well < card < raised (dark), inverted (light) - by construction
  * status trio is GLOBAL per mode, never per-theme
  * imagery scrims identical across every theme (never-themed hard law)
  * Cozy Blue's chromatic border trips the fallback (alpha ladder); Sakura Pop's dark
    border renders as authored
  * accent ink auto-picks (near-black on bright accents, white on dark)
  * overlay edit + active selection survive a save/load round trip; factory stays immutable

Run: PYTHONPATH=src python3 tests/test_themes.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TMP = tempfile.mkdtemp(prefix="lwe-themes-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

from lwe_ui.storage import paths, themes  # noqa: E402


def main() -> None:
    paths.ensure_dirs()

    assert len(themes.FACTORY) == 14, len(themes.FACTORY)
    darks = [t for t in themes.FACTORY if themes.luminance(t["background"]) < 0.5]
    lights = [t for t in themes.FACTORY if themes.luminance(t["background"]) >= 0.5]
    assert len(darks) == 9 and len(lights) == 5, (len(darks), len(lights))
    keys = [t["key"] for t in themes.FACTORY]
    assert len(set(keys)) == 14
    for t in themes.FACTORY:
        assert t["blurb"], t["key"]
        for r in themes.ROLES:
            assert themes.parse_color(t[r]) == t[r], (t["key"], r, t[r])

    lst = themes.theme_list()
    assert [t["key"] for t in lst][:14] == keys
    assert [t["key"] for t in lst][14:] == list(themes.CUSTOM_KEYS)
    dark_roles = themes.base_roles("dark")
    for ck in themes.CUSTOM_KEYS:
        assert themes.base_roles(ck) == dark_roles, f"{ck} must seed from Dark"

    for t in lst:
        roles = {r: t[r] for r in themes.ROLES}
        tok = themes.resolve(roles)
        well = themes.luminance(tok["inputWell"])
        card = themes.luminance(tok["surface"])
        raised = themes.luminance(tok["surfaceVariant"])
        if themes.luminance(roles["background"]) < 0.5:
            assert well < card < raised, f"{t['key']}: dark ladder must ascend {well} {card} {raised}"
            assert tok["danger"] == "#E24B4A" and tok["success"] == "#5DCAA5", t["key"]
        else:
            assert well < card, f"{t['key']}: light well must recess below card"
            assert raised >= card, f"{t['key']}: light raised must clamp toward white"
            assert tok["danger"] == "#C93A38" and tok["success"] == "#2E8B6A", t["key"]
        # imagery never themed
        assert tok["scrimPlate"] == "#8C000000" and tok["scrimHover"] == "#59000000", t["key"]
        # text roles pass through / derive
        assert tok["textPrimary"] == roles["text"]
        assert tok["textSecondary"] == roles["textMuted"]

    # border fallback rule: Cozy Blue trips (chromatic on light), Sakura Pop does not
    assert themes.border_fell_back(themes.base_roles("cozyBlue")) is True
    assert themes.border_fell_back(themes.base_roles("sakuraPop")) is False
    cozy = themes.resolve(themes.base_roles("cozyBlue"))
    assert cozy["border"].startswith("#1F") or cozy["border"].startswith("#1E"), \
        f"cozyBlue border must fall back to the alpha ladder: {cozy['border']}"
    sakura = themes.resolve(themes.base_roles("sakuraPop"))
    assert sakura["border"] == "#172433", "sakuraPop's authored dark border must survive"

    # accent ink: bright amber accent gets near-black ink; deep navy accent gets white
    amber = themes.resolve(themes.base_roles("amberCrt"))
    assert amber["onAccent"] == "#0D0D12", amber["onAccent"]
    ink_dark_accent = themes.resolve({**themes.base_roles("dark"), "accent": "#20304A"})
    assert ink_dark_accent["onAccent"] == "#FFFFFF"

    assert themes.parse_color("#FFF") == "#FFFFFF"
    assert themes.parse_color("ffffff") == "#FFFFFF"
    assert themes.parse_color("rgb(255, 0, 128)") == "#FF0080"
    assert themes.parse_color("rgb(300,0,0)") is None
    assert themes.parse_color("nonsense") is None

    # persistence: active + overlay round trip; factory base stays immutable
    cfg = themes.load_config()
    assert cfg["active"] == themes.DEFAULT_ACTIVE, cfg
    cfg["active"] = "cyanCircuit"
    cfg["overlays"] = {"cyanCircuit": {"accent": "#FF0000"}}
    themes.save_config(cfg)
    cfg2 = themes.load_config()
    assert cfg2["active"] == "cyanCircuit"
    eff = themes.effective_roles("cyanCircuit", cfg2["overlays"])
    assert eff["accent"] == "#FF0000", "overlay must apply"
    assert themes.base_roles("cyanCircuit")["accent"] == "#0AE8CE", "factory stays immutable"
    tok = themes.resolve_active()
    assert tok["accent"] == "#FF0000", "resolve_active must honor the overlay"
    # reset = drop the overlay
    cfg2["overlays"] = {}
    themes.save_config(cfg2)
    assert themes.resolve_active()["accent"] == "#0AE8CE"

    # bad overlay values are ignored, never crash
    eff = themes.effective_roles("dark", {"dark": {"accent": "notacolor", "text": "#123456"}})
    assert eff["accent"] == "#3FA0FF" and eff["text"] == "#123456"

    print("OK test_themes - 14 factory palettes, derivation invariants, border fallback, "
          "global status trio, never-themed scrims, overlay persistence")


if __name__ == "__main__":
    main()
