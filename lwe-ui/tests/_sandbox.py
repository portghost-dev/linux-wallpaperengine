"""Import this before anything from lwe_ui. It points the engine socket at a private temp
path so a test can never talk to the daemon on this machine.

The client reads LWE_SOCKET at call time, so setting it once here covers every code path a
test may construct later, including a full Backend. XDG_RUNTIME_DIR is re-pointed as well so
the fallback path and the panel's own single-instance socket are private too; tests that
re-point it again in setUp keep working, because LWE_SOCKET takes precedence.
"""
import atexit
import os
import shutil
import tempfile

_DIR = tempfile.mkdtemp(prefix="lwe-sandbox-")
SOCKET = os.path.join(_DIR, "engine.sock")
RUNTIME_DIR = os.path.join(_DIR, "rt")

os.makedirs(RUNTIME_DIR, exist_ok=True)
os.environ["LWE_SOCKET"] = SOCKET
os.environ["XDG_RUNTIME_DIR"] = RUNTIME_DIR
atexit.register(shutil.rmtree, _DIR, True)
