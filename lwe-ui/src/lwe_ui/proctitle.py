"""Name the process something a human recognizes in a process monitor.

Both panel processes are the Python interpreter, so without this they report as
`python3` and can only be told apart by reading their command lines - "lwe-ui
doesn't exist as a process" is the first thing anyone notices. `PR_SET_NAME`
sets the kernel's comm string, which is what `ps -o comm`, `top`, `btop`,
`pgrep -x` and journald's identifier read.

Stdlib only (ctypes), Linux only, and never fatal: a platform without prctl
just keeps the interpreter's name.
"""
from __future__ import annotations

import ctypes

#: kernel caps comm at 16 bytes including the terminator
_COMM_MAX = 15
_PR_SET_NAME = 15


def set_process_name(name: str) -> bool:
    """Set this process's comm to `name` (truncated to 15 bytes). True when applied."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        buf = ctypes.create_string_buffer(name.encode("utf-8", "replace")[:_COMM_MAX])
        return libc.prctl(_PR_SET_NAME, ctypes.byref(buf), 0, 0, 0) == 0
    except Exception:
        return False
