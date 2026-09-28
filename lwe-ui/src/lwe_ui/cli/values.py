"""The value grammars of the lwe commands, and the formatters that print a value so it types back.

Each parser returns the value or raises UsageError (exit 3), whose message completes "<setting> ...",
for example "takes on, off or toggle; got maybe". No parser clamps: a value outside its range is
refused. parse_number, parse_factor, parse_ms and parse_color accept exactly the text the engine's
launch flags accept (FlagValues.cpp).
"""
from __future__ import annotations

import math
import os
import re


class UsageError(Exception):
    """A value typed wrong; the command exits 3 before any lock or request."""


class Refused(Exception):
    """A value that cannot be used as given; the command exits 1."""


_PLAIN_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_WHOLE = re.compile(r"[0-9]+")
_STEP = re.compile(r"[+-][0-9]+")
_DURATION = re.compile(r"([0-9]+)([smh]?)")
# [0-9], not \d: \d also matches the digits of other scripts.
_TIME = re.compile(r"([0-9]{1,2}):([0-9]{2})|([0-9]{1,2})([0-9]{2})")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
_UNIT_NAMES = {"s": "seconds", "m": "minutes", "h": "hours"}


def _plain_number(text: str) -> float | None:
    """An optional sign, digits with at most one point, an optional exponent, finite and nothing
    else, as FlagValues.cpp plainNumber reads it; zero comes back as positive zero."""
    if not _PLAIN_NUMBER.fullmatch(text):
        return None
    value = float(text)
    if not math.isfinite(value):
        return None
    return 0.0 if value == 0.0 else value


def _int(digits: str) -> int | None:
    """int() of a matched digit string, or None past the interpreter's limit on digits."""
    try:
        return int(digits)
    except ValueError:
        return None


def parse_switch(word: str, toggle: bool = True) -> str:
    """on or off, or toggle where allowed; returns the word."""
    if word in (("on", "off", "toggle") if toggle else ("on", "off")):
        return word
    forms = "on, off or toggle" if toggle else "on or off"
    raise UsageError(f"takes {forms}; got {word}")


def parse_whole(word: str, lo: int, hi: int) -> int:
    """ASCII digits only, then the range."""
    value = _int(word) if _WHOLE.fullmatch(word) else None
    if value is None or not lo <= value <= hi:
        raise UsageError(f"takes a whole number from {lo} to {hi}; got {word}")
    return value


def parse_step(word: str) -> int:
    """+N or -N with ASCII digits; returns the signed step."""
    value = _int(word) if _STEP.fullmatch(word) else None
    if value is None:
        raise UsageError(f"takes +N or -N, N a whole number; got {word}")
    return value


def parse_number(word: str, lo: float, hi: float) -> float:
    """A plain number (FlagValues.cpp plainNumber), then the range, both ends included."""
    value = _plain_number(word)
    if value is None or not lo <= value <= hi:
        raise UsageError(f"takes a number from {format_number(lo)} to {format_number(hi)}; got {word}")
    return value


def parse_factor(word: str) -> float:
    """FlagValues.cpp clampFactor: a plain number up to 4, where 0 or below means 0 (no cap)."""
    value = _plain_number(word)
    if value is None or value > 4.0:
        raise UsageError(f"takes a number up to 4, where 0 or below is no cap; got {word}")
    return 0.0 if value <= 0.0 else value


def parse_ms(word: str) -> float:
    """FlagValues.cpp milliseconds: a plain number from 0 to 500, with or without ms."""
    value = _plain_number(word[:-2] if word.endswith("ms") else word)
    if value is None or not 0.0 <= value <= 500.0:
        raise UsageError(f"takes milliseconds from 0 to 500; got {word}")
    return value


def parse_color(word: str) -> tuple[float, float, float, float]:
    """FlagValues.cpp color: one argument of four plain numbers split by spaces, brightness,
    contrast and saturation 0 to 4 and hue -180 to 180; returns them with the hue in degrees."""
    refusal = UsageError('takes "brightness contrast saturation hue", each 0 to 4 and hue -180 to 180 '
                         f"degrees; got {word}")
    if not word or word[0] == " " or word[-1] == " ":
        raise refusal
    numbers = [_plain_number(part) for part in word.split(" ") if part]
    if len(numbers) != 4 or None in numbers:
        raise refusal
    brightness, contrast, saturation, hue = numbers
    if not all(0.0 <= v <= 4.0 for v in (brightness, contrast, saturation)) or not -180.0 <= hue <= 180.0:
        raise refusal
    return brightness, contrast, saturation, hue


def parse_duration(word: str, bare: str, lo_s: int, hi_s: int) -> int:
    """A whole number with an optional lowercase s, m or h and nothing else; a bare number is in
    the unit bare names. Returns seconds, then the range."""
    match = _DURATION.fullmatch(word)
    count = _int(match[1]) if match else None
    seconds = count * _UNIT_SECONDS[match[2] or bare] if count is not None else None
    if seconds is None or not lo_s <= seconds <= hi_s:
        raise UsageError(f"takes a whole number with s, m or h from {format_duration(lo_s)} to "
                         f"{format_duration(hi_s)} (a bare number is {_UNIT_NAMES[bare]}); got {word}")
    return seconds


def parse_time(word: str) -> int:
    """H:MM, HH:MM, HMM or HHMM with ASCII digits, hour 0-23 and minute 00-59, outer whitespace
    trimmed; returns minutes after midnight."""
    match = _TIME.fullmatch(word.strip())
    if match:
        hour, minute = (match[1], match[2]) if match[1] is not None else (match[3], match[4])
        if int(hour) <= 23 and int(minute) <= 59:
            return int(hour) * 60 + int(minute)
    raise UsageError(f"takes a time as H:MM, HH:MM, HMM or HHMM, such as 7:30 or 0730; got {word}")


def resolve_path(word: str, cwd_entered: bool) -> str:
    """~ expanded; an absolute path as it is; a relative one joined to the working folder, but only
    when the process entered the sender's folder."""
    if not word:
        raise UsageError("takes a path; got an empty value")
    path = os.path.expanduser(word)
    if os.path.isabs(path):
        return path
    if not cwd_entered:
        raise Refused("give a full path")
    return os.path.join(os.getcwd(), path)


def format_duration(seconds: int) -> str:
    """The largest unit that divides the value exactly, with its suffix: 900 is 15m, 90 is 90s."""
    for suffix, size in (("h", 3600), ("m", 60)):
        if seconds and seconds % size == 0:
            return f"{seconds // size}{suffix}"
    return f"{seconds}s"


def format_time(minutes: int) -> str:
    """HH:MM."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_number(x: float) -> str:
    """The shortest decimal that reads back as the same number, without a trailing .0."""
    text = repr(float(x) + 0.0)
    return text[:-2] if text.endswith(".0") else text
