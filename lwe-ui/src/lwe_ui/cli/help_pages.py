"""Help pages made from the vocabulary rows, in the layout of the resclamp page: the usage line or
lines, a blank line, the row's text wrapped at 72 columns, and for a setting its values, default, when
it applies and its scope, with the one-wallpaper form where a wallpaper can set it. Every page ends in
one newline. The text is the rows' own.
"""
from __future__ import annotations

import re
import textwrap

from . import vocabulary

WIDTH = 72
_RANGE = re.compile(r"(\S+) to (\S+?)(?:,|\s|$)")


def _wrap(text: str) -> list[str]:
    return textwrap.wrap(text, WIDTH, break_on_hyphens=False, break_long_words=False)


def _plain(text: str) -> str:
    return " ".join(text.split())


def _token(values: str) -> str:
    """The value part of a usage line, from a row's values: alternatives or a range in angle brackets."""
    forms = values.split("  (")[0].strip()
    if " | " in forms:
        return "<" + forms.replace(" | ", "|") + ">"
    match = _RANGE.match(forms)
    if match:
        low, high = match[1], match[2]
        span = f"{low} to {high}" if low.startswith("-") else f"{low}-{high}"
        steps = [part for part in forms.split(", ")[1:] if re.fullmatch(r"[+-]N", part)]
        return "<" + "|".join([span, *steps]) + ">"
    return {"a folder": "<folder>", "a file path": "<path>"}.get(forms, "<value>")


def _page(usage: list[str], paragraphs: list[str]) -> str:
    lines = [*usage]
    for paragraph in paragraphs:
        if paragraph:
            lines += ["", *_wrap(paragraph)]
    return "\n".join(lines) + "\n"


def command_page(row: dict[str, str]) -> str:
    return _page([row["form"]], [row["what"]])


def setting_page(row: dict[str, str]) -> str:
    usage = row["typed"] or f"{row['name']} {_token(row['values'])}"
    facts = f"Values: {_plain(row['values'])}. Default {row['default'] or 'none'}. Applies: {row['when']}."
    if row["scope"] not in ("global", "global and per wallpaper"):
        facts += f" Scope: {row['scope']}."
    paragraphs = [row["what"], facts]
    if row["scope"] == "global and per wallpaper":
        paragraphs.append(f"One wallpaper:  wallpaper <wallpaper> {row['name']} <value>")
    return _page([usage], paragraphs)


def wallpaper_page(row: dict[str, str]) -> str:
    words = row["name"].split(" / ")
    forms = row["values"].split(" | ")
    if all(form.split(" ", 1)[0] in words for form in forms):
        return _page([f"wallpaper <wallpaper> {form}" for form in forms], [row["what"]])
    facts = f"Values: {_plain(row['values'])}." + (f" Default {row['default']}." if row["default"] else "")
    return _page([f"wallpaper <wallpaper> {row['name']} {_token(row['values'])}"], [row["what"], facts])


#: help topics that name a command whose page is under a longer name
_TOPIC_ALIASES = {"remove": "remove --playlist"}


def page(topic: str) -> str | None:
    """The page for a command name (or a topic alias of one), else a setting name, else a per-wallpaper word;
    None if none."""
    topic = _TOPIC_ALIASES.get(topic, topic)
    for row in vocabulary.COMMANDS:
        if row["name"] == topic:
            return command_page(row)
    for row in vocabulary.SETTINGS:
        if row["name"] == topic:
            return setting_page(row)
    for row in vocabulary.PER_WALLPAPER:
        if topic in row["name"].split(" / "):
            return wallpaper_page(row)
    return None
