"""The record a config store declares so the backup can carry it.

Lives apart from registry.py so a store module can declare its record without importing
the list that collects them.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import Any, Callable

Receipt = dict[str, Any]
Plan = dict[str, Any]


@dataclass(frozen=True)
class Store:
    """One store's whole part in a backup.

    `owns` names the files the store writes under paths.config_dir(), relative, literal or
    a glob whose * stays within one path segment (registry.matches); test_store_ownership
    asserts every config file is claimed by exactly one Store or one NOT_BACKED_UP entry. `preflight` fills `plan[name]` with what the
    import would write and `apply` writes it; both return False to abandon the whole
    import, which only a store the archive is meaningless without ever does.
    """
    name: str
    owns: tuple[str, ...]
    export: Callable[[zipfile.ZipFile, Receipt], None]
    preflight: Callable[[zipfile.ZipFile, Receipt, Plan, dict[str, Any]], bool]
    apply: Callable[[Plan, Receipt], bool]
