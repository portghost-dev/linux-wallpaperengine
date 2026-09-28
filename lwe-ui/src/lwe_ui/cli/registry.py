"""The verb table. Each module in lwe_ui.cli.verbs lists its verbs in a VERBS tuple of Verb records;
discover() imports those modules in name order and refuses a verb name declared twice.
"""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass

from . import verbs


@dataclass(frozen=True)
class Verb:
    """One command word. run(ctx, args) returns the exit code; summary and group describe the verb
    for help."""
    name: str
    run: Callable[..., int]
    summary: str
    group: str


def discover() -> dict[str, Verb]:
    """Every verb by name. A name declared by two modules raises RuntimeError naming both."""
    table: dict[str, Verb] = {}
    owners: dict[str, str] = {}
    for info in sorted(pkgutil.iter_modules(verbs.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{verbs.__name__}.{info.name}")
        for verb in getattr(module, "VERBS", ()):
            if verb.name in table:
                raise RuntimeError(
                    f"verb {verb.name} is declared by {owners[verb.name]} and {module.__name__}")
            table[verb.name] = verb
            owners[verb.name] = module.__name__
    return table
