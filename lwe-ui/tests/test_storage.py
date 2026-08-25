"""Round-trip + coercion tests for the storage/ typed schema modules.

Isolation: HOME and all XDG_* vars are pointed at a fresh tempfile dir so every paths.*
resolves under it - the real ~/.config/lwe is NEVER touched. Runs under pytest if present,
otherwise as a plain `python3 tests/test_storage.py` script (asserts + a manual harness).
"""
from __future__ import annotations

import importlib
import os
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


def _reload_storage():
    """Re-import the storage modules so cached path lookups (none, but be safe) re-read env."""
    import lwe_ui.constants  # noqa: F401
    mods = {}
    for name in ("paths", "atomic", "tier_a", "settings", "wp", "tags",
                 "meta", "discover_cfg", "theme_cfg"):
        m = importlib.import_module(f"lwe_ui.storage.{name}")
        mods[name] = importlib.reload(m)
    return mods


def check_tier_a(S):
    tier_a = S["tier_a"]

    # newline values must raise loud (line-based format can't carry them)
    for bad in ("a\nb", "trailing\n", "\rcr", "mixed\r\nval"):
        raised = False
        try:
            tier_a.quote(bad)
        except ValueError:
            raised = True
        assert raised, f"quote() must raise on newline value: {bad!r}"
        raised = False
        try:
            tier_a.serialize({"K": bad})
        except ValueError:
            raised = True
        assert raised, f"serialize() must raise on newline value: {bad!r}"

    d = {
        "PLAIN": "value",
        "SPACES": "a b c",
        "DOLLAR": "cost is $5",
        "QUOTE": 'he said "hi"',
        "BACKTICK": "x`y`z",
        "BACKSLASH": "a\\b",
        "MIXED": 'a "b" $c `d` \\e f',
        "EMPTY": "",
        "SAFE": "08:00=work,18:00",
    }
    assert tier_a.parse(tier_a.serialize(d)) == d, tier_a.parse(tier_a.serialize(d))


def check_settings(S):
    settings, paths = S["settings"], S["paths"]
    settings.ensure_exists()
    assert paths.settings_file().exists()

    d = settings.load()
    assert d["ROTATION_ENABLED"] is True
    assert isinstance(d["INTERVAL"], int) and d["INTERVAL"] == 900
    assert d["ORDER"] == "shuffle"

    d["ROTATION_ENABLED"] = False
    d["INTERVAL"] = 1234
    d["ORDER"] = "sequential"
    d["SCHEDULE"] = "08:00=work;18:00=chill mix"
    settings.save(d)

    raw = paths.settings_file().read_text(encoding="utf-8")
    assert "ROTATION_ENABLED=false" in raw, raw
    assert "INTERVAL=1234" in raw
    assert 'SCHEDULE="08:00=work;18:00=chill mix"' in raw, raw

    r = settings.load()
    assert r["ROTATION_ENABLED"] is False
    assert r["INTERVAL"] == 1234
    assert r["ORDER"] == "sequential"
    assert r["SCHEDULE"] == "08:00=work;18:00=chill mix"

    r["INTERVAL"] = 999999
    r["ORDER"] = "bogus"
    settings.save(r)
    r2 = settings.load()
    assert r2["INTERVAL"] == 7200, r2["INTERVAL"]
    assert r2["ORDER"] == "shuffle", r2["ORDER"]

    paths.settings_file().write_text(
        paths.settings_file().read_text(encoding="utf-8").replace(
            "ROTATION_ENABLED=false", "ROTATION_ENABLED=true"),
        encoding="utf-8")
    assert settings.load()["ROTATION_ENABLED"] is True


def check_wp(S):
    wp, paths = S["wp"], S["paths"]
    wid = "1234567890"
    assert wp.exists(wid) is False

    d = wp.load(wid)
    assert d["TYPE"] == "scene"
    assert d["SPEED"] == 1.0
    assert d["FPS"] == ""
    assert d["props"] == {}

    d["BG"] = wid
    d["TYPE"] = "scene"
    d["SPEED"] = 1.5
    d["VOLUME"] = 0
    d["MOUSE"] = True
    d["AUTOMUTE"] = False
    d["SKIP"] = "101 102 103"
    d["CLAMPING"] = ""
    d["FPS"] = ""
    d["FULLSCREEN_PAUSE"] = ""
    d["props"] = {"schemecolor": "1 0.5 0.2", "speed": "", "bloom": "1"}

    wp.save(wid, d)
    assert wp.exists(wid) is True
    raw = paths.wp_file(wid).read_text(encoding="utf-8")

    assert "MOUSE=true" in raw, raw
    assert "AUTOMUTE=false" in raw, raw
    assert "PROP_bloom=1" in raw, raw
    assert "PROP_speed=" not in raw, raw
    assert 'PROP_schemecolor="1 0.5 0.2"' in raw, raw
    assert 'SKIP="101 102 103"' in raw, raw
    for k in ("FPS=", "CLAMPING=", "FULLSCREEN_PAUSE="):
        assert k not in raw, (k, raw)

    r = wp.load(wid)
    assert r["BG"] == wid
    assert r["SPEED"] == 1.5
    assert r["MOUSE"] is True
    assert r["AUTOMUTE"] is False
    assert r["SKIP"] == "101 102 103"
    assert r["FPS"] == ""
    assert r["CLAMPING"] == ""
    assert r["FULLSCREEN_PAUSE"] == ""
    assert r["props"] == {"schemecolor": "1 0.5 0.2", "bloom": "1"}, r["props"]

    r["FULLSCREEN_PAUSE"] = True
    wp.save(wid, r)
    raw2 = paths.wp_file(wid).read_text(encoding="utf-8")
    assert "FULLSCREEN_PAUSE=true" in raw2, raw2
    assert wp.load(wid)["FULLSCREEN_PAUSE"] is True


def check_tags(S):
    tags, paths = S["tags"], S["paths"]
    assert tags.load() == []

    tags.set_state("111", "Fictitious, Scene ☃ 42", "good")
    tags.set_state("222", "Plain bad one", "bad")
    tags.set_state("333", "Café résumé, déjà vu", "good")

    raw = paths.tags_file().read_text(encoding="utf-8")
    assert raw.splitlines()[0] == "id,title,state", raw.splitlines()[0]
    assert '"Fictitious, Scene ☃ 42"' in raw, raw

    rows = tags.load()
    assert len(rows) == 3
    by_id = {r["id"]: r for r in rows}
    assert by_id["111"]["title"] == "Fictitious, Scene ☃ 42"
    assert by_id["111"]["state"] == "good"

    assert tags.good_ids() == {"111", "333"}
    assert tags.known_ids() == {"111", "222", "333"}

    tags.set_state("222", "Now Good", "good")
    by_id = {r["id"]: r for r in tags.load()}
    assert by_id["222"]["state"] == "good"
    assert by_id["222"]["title"] == "Now Good"
    assert tags.good_ids() == {"111", "222", "333"}

    # legacy header 'tag' tolerated on read
    paths.tags_file().write_text(
        'id,title,tag\n999,"Legacy, row",good\n', encoding="utf-8")
    legacy = tags.load()
    assert legacy == [{"id": "999", "title": "Legacy, row", "state": "good"}], legacy


def check_meta(S):
    meta = S["meta"]
    assert meta.load() == {}
    assert meta.get("abc") == {}

    meta.update("abc", {"favorite": True, "added": 1718000000})
    assert meta.get("abc") == {"favorite": True, "added": 1718000000}

    meta.update("abc", {"favorite": False, "note": "líked, then not"})
    e = meta.get("abc")
    assert e["favorite"] is False
    assert e["added"] == 1718000000
    assert e["note"] == "líked, then not"

    meta.update("xyz", {"rank": 5})
    assert set(meta.load().keys()) == {"abc", "xyz"}


def check_discover(S):
    discover_cfg = S["discover_cfg"]
    d = discover_cfg.load()
    assert d["acquireMethod"] == "client"
    assert d["apiKey"] == ""
    discover_cfg.save({"apiKey": "SECRET123"})
    d2 = discover_cfg.load()
    assert d2["apiKey"] == "SECRET123"
    assert d2["acquireMethod"] == "client"


def check_theme(S):
    theme_cfg, paths = S["theme_cfg"], S["paths"]
    import lwe_ui.constants as C

    tokens = theme_cfg.resolve_tokens(theme_cfg.load())
    assert len(tokens) == len(theme_cfg.TOKENS), tokens
    assert set(tokens.keys()) == set(theme_cfg.TOKENS)
    assert tokens["base"] == C.THEME_PRESETS[C.DEFAULT_THEME_PRESET]["base"]
    for v in tokens.values():
        assert isinstance(v, str) and v.startswith("#")
    # derived washes: selection = accent @0.08 alpha, danger wash = danger @0.18 (AARRGGBB)
    assert tokens["selectionWash"] == "#147F77DD", tokens["selectionWash"]
    assert tokens["dangerWash"] == "#2EE24B4A", tokens["dangerWash"]

    light = theme_cfg.resolve_tokens({"preset": "True Black", "accent": "#FFFFFF"})
    assert light["accent"] == "#FFFFFF"
    assert light["onAccent"] == "#0D0D12", light["onAccent"]

    dark = theme_cfg.resolve_tokens({"preset": "True Black", "accent": "#101030"})
    assert dark["accent"] == "#101030"
    assert dark["onAccent"] == "#FFFFFF", dark["onAccent"]

    # accent is normalized to '#RRGGBB' (strips surrounding spaces, upper-cases, drops alpha)
    norm = theme_cfg.resolve_tokens({"preset": "True Black", "accent": "  #00ff00  "})
    assert norm["accent"] == "#00FF00", norm["accent"]
    assert norm["onAccent"] == "#0D0D12", norm["onAccent"]  # bright green -> near-black text
    assert norm["selectionWash"] == "#1400FF00", norm["selectionWash"]  # derives from FINAL accent

    # unknown preset falls back silently to default (still the full token set)
    fb = theme_cfg.resolve_tokens({"preset": "Does Not Exist"})
    assert len(fb) == len(theme_cfg.TOKENS)
    assert fb["base"] == C.THEME_PRESETS[C.DEFAULT_THEME_PRESET]["base"]

    # followSystem with a matugen file maps roles, keeps preset warning/success
    mat_path = paths.matugen_colors_file()
    mat_path.parent.mkdir(parents=True, exist_ok=True)
    mat_path.write_text(
        '{"background":"#0A0A0A","primary":"#22AAFF","on_primary":"#00111F",'
        '"error":"#FF5555","surface_container":"#161616"}',
        encoding="utf-8")
    fs = theme_cfg.resolve_tokens({"preset": "True Black", "followSystem": True})
    assert fs["base"] == "#0A0A0A", fs["base"]
    assert fs["accent"] == "#22AAFF", fs["accent"]
    assert fs["danger"] == "#FF5555", fs["danger"]
    # warning/success have no matugen role -> preset constants retained
    assert fs["warning"] == C.THEME_PRESETS["True Black"]["warning"]
    assert fs["success"] == C.THEME_PRESETS["True Black"]["success"]
    assert fs["selectionWash"] == "#1422AAFF", fs["selectionWash"]
    assert len(fs) == len(theme_cfg.TOKENS)

    # followSystem but the file is garbage -> silent preset fallback
    mat_path.write_text("not json at all {{{", encoding="utf-8")
    bad = theme_cfg.resolve_tokens({"preset": "True Black", "followSystem": True})
    assert bad == theme_cfg.resolve_tokens({"preset": "True Black"})

    theme_cfg.save({"preset": "Charcoal", "accent": "#AABBCC"})
    cfg = theme_cfg.load()
    assert cfg["preset"] == "Charcoal"
    assert cfg["accent"] == "#AABBCC"
    assert cfg["followSystem"] is False


_CHECKS = (
    ("tier_a", check_tier_a),
    ("settings", check_settings),
    ("wp", check_wp),
    ("tags", check_tags),
    ("meta", check_meta),
    ("discover", check_discover),
    ("theme", check_theme),
)


def _run_all() -> None:
    tmp = tempfile.mkdtemp(prefix="lwe-storage-test-")
    saved = {k: os.environ.get(k) for k in _fresh_env(tmp)}
    try:
        os.environ.update(_fresh_env(tmp))
        # sanity: paths must resolve under the temp dir, never the real config
        S = _reload_storage()
        assert str(S["paths"].config_dir()).startswith(tmp), S["paths"].config_dir()
        for _name, fn in _CHECKS:
            fn(S)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_storage_roundtrips():
    _run_all()


if __name__ == "__main__":
    _run_all()
    print("OK: all storage round-trip + coercion checks passed")
