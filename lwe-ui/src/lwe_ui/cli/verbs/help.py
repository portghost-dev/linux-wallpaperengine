"""lwe help: the fixed help screens, a page made from the vocabulary rows for every other command,
setting and per-wallpaper word, and the engine's own switch list for help --debug."""
from __future__ import annotations

import subprocess

from .. import DONE, REFUSED, USAGE, Context, help_pages, help_text
from ..registry import Verb

_PAGES = {
    "--all": help_text.ALL,
    "schedule": help_text.SCHEDULE,
    "files": help_text.FILES,
    "resclamp": help_text.RESCLAMP,
    "resolution": help_text.RESCLAMP,
    "sharpness": help_text.RESCLAMP,
    "blur": help_text.RESCLAMP,
    "video memory": help_text.RESCLAMP,
}

DEBUG_TIMEOUT_S = 10


def _debug(ctx: Context) -> int:
    from ...engine import daemon_unit

    engine = daemon_unit.resolve_engine_bin()
    if not engine:
        ctx.error("lwe help --debug needs the engine, which was not found")
        return REFUSED
    try:
        result = subprocess.run([engine, "--help-debug"], capture_output=True, timeout=DEBUG_TIMEOUT_S,
                                encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0 or not result.stdout.startswith(help_text.ENGINE_DEBUG_HEADER):
        ctx.error("lwe help --debug: the engine did not print its switch list")
        return REFUSED
    ctx.out.write(help_text.DEBUG_HEADER + result.stdout[len(help_text.ENGINE_DEBUG_HEADER):])
    return DONE


def run(ctx: Context, args: list[str]) -> int:
    if not args:
        ctx.out.write(help_text.MAIN)
        return DONE
    topic = " ".join(args)
    if topic == "--debug":
        return _debug(ctx)
    page = _PAGES.get(topic) or help_pages.page(topic)
    if page is None:
        ctx.error(f"lwe help: no page for {topic}; lwe help lists every command and lwe help --all every setting")
        return USAGE
    ctx.out.write(page)
    return DONE


VERBS = (Verb("help", run, "Show help for lwe, a command or a setting", "help"),)
