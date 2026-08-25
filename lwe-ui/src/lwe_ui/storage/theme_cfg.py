"""theme.json - UI theme config (Tier B) + token resolution.

Stored keys: preset, accent, followSystem. `resolve_tokens` turns a config into the flat
color-token set consumed by qml/Theme.qml, layering: preset -> optional matugen
(Material-You) follow-system mapping -> optional accent override. selectionWash/dangerWash
are DERIVED from the final accent/danger. Any failure falls back to the preset silently so
the UI always renders.
"""
from __future__ import annotations

from typing import Any

from .. import constants as C
from . import atomic, paths

# Canonical token order (design v1.0 1). The preset dicts in constants define every key
# EXCEPT selectionWash/dangerWash/segmentWash/checkHoverBorder, which resolve_tokens derives
# (the first two from the final accent/danger; segmentWash and checkHoverBorder from a fixed
# white so they never re-tint).
TOKENS = (
    "base", "surface", "surfaceVariant", "inputWell",
    "border", "borderStrong", "hoverWash", "activeWash",
    "scrimPlate", "scrimHover",
    "textPrimary", "textSecondary", "textTertiary", "textMutedBody",
    "accent", "onAccent", "selectionWash", "segmentWash",
    "danger", "dangerWash", "warning", "success",
    "checkHoverBorder",
)


def load() -> dict[str, Any]:
    out = dict(C.THEME_DEFAULTS)
    data = atomic.read_json(paths.theme_file(), default={})
    if isinstance(data, dict):
        out.update(data)
    return out


def save(d: dict[str, Any]) -> None:
    out = dict(C.THEME_DEFAULTS)
    out.update(d or {})
    atomic.atomic_write_json(paths.theme_file(), out)


def _parse_hex(color: str) -> tuple[int, int, int]:
    """'#rrggbb' (or 3/4/8-digit) -> (r,g,b) 0-255. Raises ValueError on bad input."""
    s = color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    elif len(s) == 4:  # #rgba -> drop alpha nibble
        s = "".join(ch * 2 for ch in s[:3])
    elif len(s) == 8:  # #rrggbbaa -> drop alpha
        s = s[:6]
    if len(s) != 6:
        raise ValueError(f"bad hex color: {color!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _luminance(color: str) -> float:
    """WCAG relative luminance 0..1. Each channel is gamma-linearised first - the earlier
    version weighted the raw sRGB bytes, which is not luminance and read ~0.52 for an accent
    whose true luminance is ~0.28."""
    out = []
    for c in _parse_hex(color):
        c = c / 255.0
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = out
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two colors, 1..21."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


_INK_DARK = "#0D0D12"
_INK_LIGHT = "#FFFFFF"


def _on_accent(accent: str) -> str:
    """Pick the ink with the HIGHER MEASURED CONTRAST against the accent (design rule
    2026-07-22 item 2), rather than thresholding a luminance value.

    A luminance threshold reached the WORSE ink on 6 of the 14 factory palettes - Grid
    Graphite, Neon Dusk, Light, Cozy Pink, Cozy Blue and Sakura Pop all shipped white text
    where near-black measured higher. These accents are mid-luminance saturated colors, so
    dark ink usually wins; white only genuinely wins on a light accent like Lavender Milk.
    Measuring instead of thresholding is self-correcting for future palettes and for
    user-custom accents, and needs no palette edits."""
    if _contrast_ratio(accent, _INK_LIGHT) > _contrast_ratio(accent, _INK_DARK):
        return _INK_LIGHT
    return _INK_DARK


def _with_alpha(color: str, alpha: float) -> str:
    """'#RRGGBB' + alpha 0..1 -> '#AARRGGBB' (the QML color-string alpha form)."""
    r, g, b = _parse_hex(color)
    return "#%02X%02X%02X%02X" % (round(alpha * 255), r, g, b)


def _matugen_tokens(base_tokens: dict[str, str]) -> dict[str, str] | None:
    """Map the matugen role file onto tokens; keep preset warning/success. None on any failure."""
    data = atomic.read_json(paths.matugen_colors_file(), default=None)
    if not isinstance(data, dict):
        return None
    # matugen files may nest the flat role map (e.g. under "colors"); accept either shape.
    roles = data
    if not any(isinstance(v, str) for v in data.values()):
        for v in data.values():
            if isinstance(v, dict) and any(isinstance(x, str) for x in v.values()):
                roles = v
                break
    out = dict(base_tokens)
    mapped = False
    for token, role in C.MATUGEN_ROLE_MAP.items():
        val = roles.get(role)
        if isinstance(val, str) and val.strip():
            out[token] = val.strip()
            mapped = True
    if not mapped:
        return None
    # warning / success have no matugen role -> retain preset values (already in out).
    return out


def resolve_tokens(cfg: dict[str, Any]) -> dict[str, str]:
    """Resolve a theme config to the flat v1.0 color tokens. Silent preset fallback on any error."""
    preset_name = (cfg or {}).get("preset") or C.DEFAULT_THEME_PRESET
    preset = C.THEME_PRESETS.get(preset_name) or C.THEME_PRESETS[C.DEFAULT_THEME_PRESET]
    tokens = dict(preset)

    # followSystem (matugen) takes precedence for the system-derived roles, but never crashes.
    if (cfg or {}).get("followSystem"):
        try:
            mat = _matugen_tokens(tokens)
            if mat is not None:
                tokens = mat
        except Exception:
            tokens = dict(preset)

    # explicit accent override wins last and recomputes onAccent.
    accent = (cfg or {}).get("accent")
    if accent:
        try:
            r, g, b = _parse_hex(accent)
            normalized = "#%02X%02X%02X" % (r, g, b)
            tokens["accent"] = normalized
            tokens["onAccent"] = _on_accent(normalized)
        except (ValueError, TypeError):
            # bad accent string -> leave whatever was there (preset/matugen) silently.
            tokens["accent"] = preset["accent"]
            tokens["onAccent"] = preset["onAccent"]

    # Derived washes (v1.0 1): selection from the FINAL accent, danger wash from danger.
    # Guarded - a hostile matugen value must never break the never-crash contract.
    try:
        tokens["selectionWash"] = _with_alpha(tokens["accent"], 0.08)
    except (ValueError, TypeError):
        tokens["selectionWash"] = _with_alpha(preset["accent"], 0.08)
    try:
        tokens["dangerWash"] = _with_alpha(tokens["danger"], 0.18)
    except (ValueError, TypeError):
        tokens["dangerWash"] = _with_alpha(preset["danger"], 0.18)

    tokens["segmentWash"] = _with_alpha("#FFFFFF", 0.10)

    tokens["checkHoverBorder"] = _with_alpha("#FFFFFF", 0.35)

    return {k: tokens[k] for k in TOKENS}
