"""The receipt a setting change prints: the setting, the value, saved or unchanged, and when it
applies, as one line of text or, under -j, one JSON object with the keys of RECEIPT_KEYS.

A change the engine takes at once ends in one of four outcomes: applied, pending, refused or
uncertain. A receipt that is not applied names the next chances to apply, never a promise. A change
with no engine request (restart, panel and panel-start reach) has no outcome and says when it
applies.
"""
from __future__ import annotations

import json
from typing import Any

APPLIED = "applied"
PENDING = "pending"
REFUSED = "refused"
UNCERTAIN = "uncertain"

RECEIPT_KEYS = ("setting", "value", "saved", "outcome", "applies", "reason")

OPPORTUNITIES = ("It can apply the next time the panel window opens or polls, an lwe command saves a setting "
                 "the engine uses, lwe reload or a backup import runs, or lwe service start or restart starts "
                 "the engine.")

_WHEN = {
    "now": "applies now",
    "next wallpaper": "applies to the next wallpaper",
    "restart": "takes effect at the next service restart (lwe service restart applies it)",
    "panel": "the panel reads it at its next scan",
    "panel start": "a running panel picks it up when it next starts",
}

_NOT_APPLIED = {
    PENDING: "; the service is not running or is busy, so it is not applied yet",
    REFUSED: ", but the engine refused it: {reason}",
    UNCERTAIN: "; the engine did not answer in time, so it may have applied",
}


def receipt(setting: str, value: str, saved: bool, applies: str, outcome: str | None = None,
            reason: str = "") -> dict[str, Any]:
    """applies is the row's reach; outcome is None for a change with no engine request."""
    return {"setting": setting, "value": value, "saved": saved, "outcome": outcome, "applies": applies,
            "reason": reason}


def text(r: dict[str, Any]) -> str:
    """The receipt as one line."""
    state = "saved" if r["saved"] else "unchanged"
    if r["outcome"] in (None, APPLIED):
        return f"{r['setting']} {r['value']}: {state}; {_WHEN[r['applies']]}."
    tail = _NOT_APPLIED[r["outcome"]].format(reason=r["reason"])
    return f"{r['setting']} {r['value']}: {state}{tail}. {OPPORTUNITIES}"


def emit(ctx: Any, r: dict[str, Any]) -> None:
    """Print the receipt on ctx.out: text, or one compact JSON line under -j."""
    if ctx.json:
        print(json.dumps(r, ensure_ascii=False, separators=(",", ":")), file=ctx.out)
    else:
        print(text(r), file=ctx.out)
