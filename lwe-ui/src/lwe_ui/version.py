"""The build's version stamp as the panel carries it, and the two refusals of the strict match: a
command sent by an lwe from another build, and a running engine from another build."""
from __future__ import annotations

from pathlib import Path

STAMP_FILE = Path(__file__).with_name("VERSION")


class StampError(Exception):
    """The panel's version file is missing, unreadable or blank."""


def panel_stamp() -> str:
    """The first line of VERSION beside this module, a leading BOM dropped, surrounding whitespace
    stripped."""
    try:
        text = STAMP_FILE.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    stamp = text.split("\n", 1)[0].removeprefix("\ufeff").strip(" \t\n\v\f\r")
    if not stamp:
        raise StampError(
            f"the panel's version file {STAMP_FILE} is missing or empty; reinstall with bash install.sh")
    return stamp


def sender_refusal(sender: str | None, panel: str) -> str | None:
    """None when the lwe that sent the command carries the panel's stamp, or sent none."""
    if sender is None or sender == panel:
        return None
    return f"The engine is {sender} but the panel is {panel}; install both from one build (bash install.sh)."


def running_refusal(status: dict, panel: str) -> str | None:
    """None when the running engine's status carries the panel's stamp."""
    if "version" not in status:
        return f"The running engine is older than 1.1.0 but {panel} is installed; run lwe service restart."
    if status["version"] == panel:
        return None
    return f"The running engine is {status['version']} but {panel} is installed; run lwe service restart."
