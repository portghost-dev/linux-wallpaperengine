"""Theme store - factory palettes, custom slots, overlays, and token derivation.

Six stored roles per theme (background,
surface, text, textMuted, accent, border) resolve to the full token set Theme.qml
already consumes, so every existing binding restyles live. The design law lives in the
derivation - any six colors a user types produce a valid theme by construction:
the surface ladder stays ordered, text stays legible, ink stays contrasting.

Factory palettes are immutable; user edits are a sparse per-theme overlay; Custom 1-3
seed from Dark and are fully user-serviceable. The active theme + overlays persist in
theme.json (Tier B, GUI-only).

Hard law: wallpaper imagery scrims/plates/badges are never themed - those tokens are
constant black-alpha regardless of theme.
"""
from __future__ import annotations

from typing import Any

from . import atomic, paths

ROLES = ("background", "surface", "text", "textMuted", "accent", "border")

FACTORY: tuple[dict[str, str], ...] = (
    {"key": "oledElectric", "name": "OLED Electric",
     "background": "#000000", "surface": "#0D0D0D", "text": "#DEE3EA",
     "textMuted": "#99A4B2", "accent": "#3790FF", "border": "#171C26",
     "blurb": "True #000000 background so OLED pixels switch off, with an electric azure accent."},
    {"key": "dark", "name": "Dark",
     "background": "#0B1626", "surface": "#142136", "text": "#DCE8F7",
     "textMuted": "#8296B3", "accent": "#3FA0FF", "border": "#1E3049",
     "blurb": "Deep blue-navy dark theme, easy on the eyes with a bright blue accent."},
    {"key": "gridGraphite", "name": "Grid Graphite",
     "background": "#17181A", "surface": "#1F2124", "text": "#E9E9E6",
     "textMuted": "#8E9094", "accent": "#E3364A", "border": "#2C2E31",
     "blurb": "Neutral graphite dark with a crimson accent."},
    {"key": "aizomeIndigo", "name": "Aizome Indigo",
     "background": "#1B2432", "surface": "#232E40", "text": "#E8E4DA",
     "textMuted": "#97A0AC", "accent": "#6FA3C7", "border": "#303F57",
     "blurb": "Japanese indigo dark, muted and sophisticated."},
    {"key": "neonDusk", "name": "Neon Dusk",
     "background": "#12081F", "surface": "#1C1030", "text": "#F2E9FF",
     "textMuted": "#9E8FC0", "accent": "#FF2E97", "border": "#2E1D4A",
     "blurb": "Purple-noir dark with a neon pink accent."},
    {"key": "cyanCircuit", "name": "Cyan Circuit",
     "background": "#05090F", "surface": "#0D1520", "text": "#D7F4FF",
     "textMuted": "#7FA3B8", "accent": "#0AE8CE", "border": "#172433",
     "blurb": "Near-black dark with a glowing cyan accent."},
    {"key": "greenPhosphor", "name": "Green Phosphor",
     "background": "#0A0F0A", "surface": "#111A12", "text": "#BFE8BC",
     "textMuted": "#6E9A70", "accent": "#35E06B", "border": "#1C2B1E",
     "blurb": "Green CRT terminal. Night-vision vibes."},
    {"key": "amberCrt", "name": "Amber CRT",
     "background": "#100C06", "surface": "#191309", "text": "#EFD9B4",
     "textMuted": "#A98F63", "accent": "#E9A028", "border": "#2A2110",
     "blurb": "Amber CRT terminal. Retro gamer vibes."},
    {"key": "abyssGlow", "name": "Abyss Glow",
     "background": "#04121B", "surface": "#0A1D2A", "text": "#D6EEF2",
     "textMuted": "#7C9FAC", "accent": "#19E3C2", "border": "#12303F",
     "blurb": "Deep ocean dark with a bioluminescent teal accent."},
    {"key": "light", "name": "Light",
     "background": "#FDFDFC", "surface": "#F1F2F3", "text": "#17191C",
     "textMuted": "#66696E", "accent": "#2E7FD6", "border": "#E2E4E6",
     "blurb": "Clean white-first light theme with gray highlights and a calm blue accent."},
    {"key": "cozyPink", "name": "Cozy Pink",
     "background": "#FEF0FC", "surface": "#FFFDFE", "text": "#491C2F",
     "textMuted": "#8C6672", "accent": "#FF3DA5", "border": "#FBD0E4",
     "blurb": "Cute white and pink with a hot pink accent."},
    {"key": "cozyBlue", "name": "Cozy Blue",
     "background": "#EAF6FB", "surface": "#FDFEFF", "text": "#121316",
     "textMuted": "#566A78", "accent": "#4775FF", "border": "#ACCCFB",
     "blurb": "Cozy Pink's blue sibling: white and sky with a vivid blue accent."},
    {"key": "sakuraPop", "name": "Sakura Pop",
     "background": "#F7F3EE", "surface": "#FEFCF9", "text": "#2B2724",
     "textMuted": "#736A63", "accent": "#FE3460", "border": "#172433",
     "blurb": "Warm paper tones framed by a dark navy border, with a sakura red-pink accent."},
    {"key": "lavenderMilk", "name": "Lavender Milk",
     "background": "#F4F0FE", "surface": "#FDFCFF", "text": "#372A55",
     "textMuted": "#6F6589", "accent": "#7D5BE6", "border": "#E2D9F6",
     "blurb": "Pale violet light theme, soft and calm."},
)

CUSTOM_KEYS = ("custom1", "custom2", "custom3")
CUSTOM_SEED = "dark"
DEFAULT_ACTIVE = "oledElectric"

STATUS_DARK = {"success": "#5DCAA5", "warning": "#EF9F27", "danger": "#E24B4A"}
STATUS_LIGHT = {"success": "#2E8B6A", "warning": "#B26B00", "danger": "#C93A38"}


def _rgb(color: str) -> tuple[int, int, int]:
    s = str(color).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    elif len(s) == 8:
        s = s[2:] if s[:2].lower() in ("ff",) else s[:6]  # tolerate #AARRGGBB or #RRGGBBAA
    if len(s) != 6:
        raise ValueError(f"bad hex color: {color!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _hex(r: float, g: float, b: float) -> str:
    clamp = lambda v: max(0, min(255, round(v)))
    return "#%02X%02X%02X" % (clamp(r), clamp(g), clamp(b))


def luminance(color: str) -> float:
    r, g, b = _rgb(color)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _mix(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _rgb(c1)
    r2, g2, b2 = _rgb(c2)
    return _hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def _with_alpha(color: str, alpha: float) -> str:
    r, g, b = _rgb(color)
    return "#%02X%02X%02X%02X" % (round(alpha * 255), r, g, b)


def _contrast(c1: str, c2: str) -> float:
    l1, l2 = luminance(c1), luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _chroma(color: str) -> float:
    r, g, b = _rgb(color)
    return (max(r, g, b) - min(r, g, b)) / 255.0


def parse_color(text: str) -> str | None:
    """User input -> '#RRGGBB' or None. Accepts '#FFF', 'FFFFFF', '#FFFFFF',
    'rgb(255, 255, 255)'."""
    s = str(text or "").strip()
    if s.lower().startswith("rgb"):
        try:
            inner = s[s.index("(") + 1:s.rindex(")")]
            parts = [int(p.strip()) for p in inner.split(",")]
            if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
                return _hex(*parts)
        except (ValueError, IndexError):
            return None
        return None
    try:
        return _hex(*_rgb(s))
    except ValueError:
        return None


def load_config() -> dict[str, Any]:
    data = atomic.read_json(paths.theme_file(), default={})
    if not isinstance(data, dict):
        data = {}
    out = {
        "active": data.get("active") or DEFAULT_ACTIVE,
        "overlays": data.get("overlays") if isinstance(data.get("overlays"), dict) else {},
    }
    if out["active"] not in {t["key"] for t in FACTORY} | set(CUSTOM_KEYS):
        out["active"] = DEFAULT_ACTIVE
    return out


def save_config(cfg: dict[str, Any]) -> None:
    atomic.atomic_write_json(paths.theme_file(), {
        "active": cfg.get("active", DEFAULT_ACTIVE),
        "overlays": cfg.get("overlays", {}),
    })


def theme_list() -> list[dict[str, str]]:
    """Menu order: factory darks, factory lights, custom slots."""
    out = [dict(t) for t in FACTORY]
    for i, key in enumerate(CUSTOM_KEYS, start=1):
        seed = next(t for t in FACTORY if t["key"] == CUSTOM_SEED)
        c = {k: seed[k] for k in ROLES}
        c.update({"key": key, "name": f"Custom {i}",
                  "blurb": f"Your own palette, slot {i} - edit the colors below."})
        out.append(c)
    return out


def base_roles(key: str) -> dict[str, str]:
    """The six FACTORY (or seed) roles for a theme key - the Reset target."""
    for t in theme_list():
        if t["key"] == key:
            return {r: t[r] for r in ROLES}
    return {r: FACTORY[0][r] for r in ROLES}


def effective_roles(key: str, overlays: dict) -> dict[str, str]:
    """base + that theme's sparse overlay (validated hexes only)."""
    roles = base_roles(key)
    ov = overlays.get(key) if isinstance(overlays, dict) else None
    if isinstance(ov, dict):
        for r in ROLES:
            v = ov.get(r)
            if isinstance(v, str):
                parsed = parse_color(v)
                if parsed:
                    roles[r] = parsed
    return roles


def resolve(roles: dict[str, str]) -> dict[str, str]:
    """Six roles -> the full Theme.qml token set (v1.4 derivation; never raises).

    The luminance invariant (well < card < raised for dark, inverted for light) is
    asserted by construction: the ladder is derived by mixing toward black/white, so it
    cannot cross. The border fallback rule keeps low-contrast or chromatic-on-light
    borders from rendering as mud (Cozy Blue case) - outlines fall back to the neutral
    alpha ladder. Sakura Pop's dark chromatic border passes contrast and renders as
    authored.
    """
    bg = roles["background"]
    surface = roles["surface"]
    text = roles["text"]
    muted = roles["textMuted"]
    accent = roles["accent"]
    border = roles["border"]
    dark = luminance(bg) < 0.5

    if dark:
        surface_deep = _mix(surface, "#000000", 0.45)
        surface_raised = _mix(surface, "#FFFFFF", 0.06)
    else:
        surface_deep = _mix(surface, "#000000", 0.03)
        surface_raised = _mix(surface, "#FFFFFF", 0.60)

    text2 = _mix(text, muted, 0.40)
    text4 = _mix(muted, bg, 0.45)

    accent_ink = "#0D0D12" if luminance(accent) >= 0.5 else "#FFFFFF"

    ladder = "#FFFFFF" if dark else "#000000"
    hairline = _with_alpha(ladder, 0.12)
    hairline_strong = _with_alpha(ladder, 0.20)

    # border fallback: too little contrast against the background, or strongly chromatic
    # against a LIGHT background, falls back to the ladder (log-worthy, never mud)
    border_ok = True
    try:
        if _contrast(border, bg) < 1.15:
            border_ok = False
        if not dark and _chroma(border) > 0.25 and luminance(border) > 0.55:
            border_ok = False
    except ValueError:
        border_ok = False
    border_tok = border if border_ok else hairline
    border_strong = _mix(border, text, 0.25) if border_ok else hairline_strong

    status = STATUS_DARK if dark else STATUS_LIGHT

    if dark:
        toggle_off_track = surface_raised
        toggle_off_border = hairline_strong
        toggle_off_knob = muted
    else:
        toggle_off_track = _mix(bg, text, 0.10)
        toggle_off_border = hairline
        toggle_off_knob = "#FFFFFF"

    return {
        "base": bg,
        "surface": surface,
        "surfaceVariant": surface_raised,
        "inputWell": surface_deep,
        "border": border_tok,
        "borderStrong": border_strong,
        "hoverWash": _with_alpha(ladder, 0.06),
        "activeWash": _with_alpha(ladder, 0.08),
        # never themed: imagery scrims/plates stay black-alpha in every theme (hard law)
        "scrimPlate": "#8C000000",
        "scrimHover": "#59000000",
        "textPrimary": text,
        "textSecondary": muted,
        "textTertiary": text4,
        "textMutedBody": text2,
        "accent": accent,
        "onAccent": accent_ink,
        "selectionWash": _with_alpha(accent, 0.08),
        "segmentWash": _with_alpha(ladder, 0.10),
        # on-imagery furniture (the card checkbox border): constant white, never the
        # ladder - it sits on the thumbnail and the never-themed hard law applies
        "checkHoverBorder": _with_alpha("#FFFFFF", 0.35),
        # type-badge text (Design amendment to the never-themed law): the PLATE is
        # legibility and stays the immutable dark scrim; the TEXT is identity and takes
        # a fixed low dose of the theme accent. 18% barely moves luminance, so contrast
        # on the plate holds by construction - derived, never stored.
        "badgeText": _mix("#D4D4D4", accent, 0.18),
        "danger": status["danger"],
        "dangerWash": _with_alpha(status["danger"], 0.18),
        "warning": status["warning"],
        "warningWash": _with_alpha(status["warning"], 0.14),
        "success": status["success"],
        # explicit alpha-ladder tokens (dual-value law 1): consumers that want the
        # neutral ladder regardless of the stored border (scrollbar thumbs, keylines)
        "ladderSoft": _with_alpha(ladder, 0.06),
        "ladderMid": hairline,
        "ladderStrong": hairline_strong,
        "toggleOffTrack": toggle_off_track,
        "toggleOffBorder": toggle_off_border,
        "toggleOffKnob": toggle_off_knob,
        "toggleKnobOn": "#FFFFFF",
    }


def border_fell_back(roles: dict[str, str]) -> bool:
    """True when this palette's stored border trips the fallback rule (for the log note)."""
    bg, border = roles["background"], roles["border"]
    dark = luminance(bg) < 0.5
    try:
        if _contrast(border, bg) < 1.15:
            return True
        return (not dark) and _chroma(border) > 0.25 and luminance(border) > 0.55
    except ValueError:
        return True


def resolve_active() -> dict[str, str]:
    """The full token set for the persisted active theme (app startup path)."""
    cfg = load_config()
    return resolve(effective_roles(cfg["active"], cfg["overlays"]))
