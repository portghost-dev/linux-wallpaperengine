"""Module entry point: ``python -m lwe_ui`` runs the control panel; a ``--lwe`` start runs a command."""
from __future__ import annotations

from lwe_ui.launch import main

if __name__ == "__main__":
    raise SystemExit(main())
