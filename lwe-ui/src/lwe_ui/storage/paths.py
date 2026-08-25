"""Resolve every config / state / content path. XDG-aware with $HOME fallback.

All app-created dirs are created on demand by ensure_dirs(). Nothing here writes files.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import constants as C

# A wallpaper id becomes both a path component ($WALLPAPERS_DIR/$id) and a token in the
# shell-sourced playlist MEMBERS list, which a shell consumer word-splits and (before finding 1's
# fix) glob-expands. Reject anything that could traverse a path or glob/split in the shell.
_UNSAFE_WID = re.compile(r"[\s*?\[\]/\\]")


def is_safe_wid(wid: str) -> bool:
    """True for a wallpaper id safe to store in the shell-sourceable files. Named local dirs
    (letters, digits, '.', '_', '-') pass; empty, '.'/'..', slashes, whitespace, and glob
    metacharacters are rejected."""
    w = str(wid or "")
    if not w or w in (".", ".."):
        return False
    return not _UNSAFE_WID.search(w)


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _xdg(var: str, default_rel: str) -> Path:
    val = os.environ.get(var)
    if val and os.path.isabs(val):
        return Path(val)
    return _home() / default_rel


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "lwe"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "lwe"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "lwe"


# --- Tier A (shell-sourceable) -------------------------------------------------------
def settings_file() -> Path:
    return config_dir() / "settings.conf"


def tags_file() -> Path:
    return config_dir() / "tags.csv"


def wp_dir() -> Path:
    return config_dir() / "wp"


def wp_file(wid: str) -> Path:
    return wp_dir() / f"{wid}.conf"


# --- Tier B (JSON, app-only) ---------------------------------------------------------
def meta_file() -> Path:
    return config_dir() / "meta.json"


def discover_file() -> Path:
    return config_dir() / "discover.json"


def theme_file() -> Path:
    return config_dir() / "theme.json"


def objindex_dir() -> Path:
    return state_dir() / "objindex"


def objindex_file(wid: str) -> Path:
    return objindex_dir() / f"{wid}.json"


def propindex_dir() -> Path:
    return state_dir() / "propindex"


def propindex_file(wid: str) -> Path:
    return propindex_dir() / f"{wid}.json"


def legacy_dir() -> Path:
    return config_dir() / "legacy"


def playlists_dir() -> Path:
    return config_dir() / "playlists"


def playlist_file(slug: str) -> Path:
    return playlists_dir() / f"{slug}.conf"


def legacy_playlists_dir() -> Path:
    """Tombstone home for deleted playlists (recoverable, mirrors the legacy/ pattern)."""
    return legacy_dir() / "playlists"


def records_dir() -> Path:
    """Per-wid item RECORD store: state/records/<wid>.jsonl append-only event logs.
    Supersedes the flat config/tombstones.json map."""
    return state_dir() / "records"


def record_file(wid: str) -> Path:
    return records_dir() / f"{wid}.jsonl"


def draft_dir() -> Path:
    return state_dir() / "draft"


def draft_file(wid: str) -> Path:
    """Sticky draft buffer for the bench; Tier A, same schema as wp/<id>.conf."""
    return draft_dir() / f"{wid}.conf"


def manual_hold_file() -> Path:
    """Marker the app drops on a manual playlist switch while a schedule is enabled; the
    watcher honors ACTIVE_PLAYLIST until the next boundary, then deletes it."""
    return state_dir() / "playlist-manual-hold"


def default_engine_bin() -> Path:
    return _home() / "src/linux-wallpaperengine/build/output/linux-wallpaperengine"


def default_assets_dir() -> Path:
    return data_dir() / "assets"


def default_wallpapers_dir() -> Path:
    return data_dir() / "wallpapers"


def manual_dir() -> Path:
    """LWE's OWN pending root, for folders added by hand (the Advanced import).

    A second PENDING source alongside Steam's workshop tree - deliberately not inside it, since
    Steam owns that directory and prunes what it does not recognize, and deliberately not the
    library, since an added folder has to be benched before it earns a place there. Items here
    behave exactly like a Steam arrival: they surface as Workshop tiles, bench from this path,
    and only reach WALLPAPERS_DIR when commit() promotes them."""
    return data_dir() / "manual"


def pending_root_for(wid: str, workshop_root: str | Path | None = None) -> Path:
    """Which pending root actually holds this item.

    Steam ids and hand-added folders share one lifecycle but live in different trees, so every
    per-item path lookup asks here rather than assuming the Steam dir.

    `workshop_root` is the CONFIGURED Steam root and must be passed by callers that honor the
    WORKSHOP_DIR setting - which is all of them in the live pipeline, and every test. Defaulting
    to detect_workshop_dir() here instead would silently ignore that setting and send the whole
    import pass at the real Steam directory."""
    if is_safe_wid(str(wid)):
        cand = manual_dir() / str(wid)
        if cand.is_dir():
            return manual_dir()
    return Path(workshop_root) if workshop_root else detect_workshop_dir()


def default_workshop_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "Steam/steamapps/workshop/content" / str(C.WALLPAPER_ENGINE_APPID)


def flatpak_workshop_dir() -> Path:
    return _home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/workshop/content" / str(C.WALLPAPER_ENGINE_APPID)


def detect_workshop_dir() -> Path:
    """Prefer whichever workshop dir actually exists (native, then Flatpak); else native default."""
    native = default_workshop_dir()
    if native.is_dir():
        return native
    flat = flatpak_workshop_dir()
    if flat.is_dir():
        return flat
    return native


def default_steam_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "Steam"


def flatpak_steam_dir() -> Path:
    return _home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam"


def detect_steam_dir() -> Path:
    """Steam install root, native then Flatpak - the same split detect_workshop_dir() uses.

    STEAM_DIR is a detected default plus a user override,
    exactly the WALLPAPERS_DIR pattern. The workshop root is derived from a Steam install,
    so the two detections must agree about which install this box has.
    """
    native = default_steam_dir()
    if native.is_dir():
        return native
    flat = flatpak_steam_dir()
    if flat.is_dir():
        return flat
    return native


def matugen_colors_file() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / C.DEFAULT_MATUGEN_PATH


def default_settings() -> dict:
    """Spec defaults from constants, with the location keys resolved to absolute strings.

    ENGINE_BIN stays empty: it is resolved at use time by
    engine.daemon_unit.resolve_engine_bin(), which probes the install target and
    PATH. Seeding it here would persist a guess into settings.conf, and a stored
    path is treated as an explicit choice.
    """
    out = {k: v["default"] for k, v in C.SETTINGS_SCHEMA.items()}
    out["ASSETS_DIR"] = str(default_assets_dir())
    out["WALLPAPERS_DIR"] = str(default_wallpapers_dir())
    out["WORKSHOP_DIR"] = str(detect_workshop_dir())
    out["STEAM_DIR"] = str(detect_steam_dir())
    return out


def ensure_dirs() -> None:
    for d in (config_dir(), wp_dir(), playlists_dir(), state_dir(), objindex_dir(),
              propindex_dir(), records_dir(), draft_dir()):
        d.mkdir(parents=True, exist_ok=True)
