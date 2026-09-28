"""wallpaper <w> hide, unhide and blink: the entries of objects.WALLPAPER_WORDS that change what a scene
shows, called in process with picks from select.

hide and unhide edit only the SKIP line of a hand-written conf (a comment, other keys and CRLF endings
stay) through one call of the change runner, recorded in the push module; an unterminated quote is
refused with the file unchanged; a part the scene lacks, a non-digit id, the 256 cap and an emptied
SKIP each end as objects.py defines them. blink runs against api_client.status, set_skip and available replaced by a
scripted recorder, with time.sleep patched: the requests of one blink, each refusal before anything is
sent, no restore once another wallpaper is on screen, a restore after a signal in the wait, and a sync
lock another thread holds past the wait.

Run: PYTHONPATH=src python3 tests/test_cli_objects_live.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import difflib
import io
import os
import shutil
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _cli_env

_TMP = tempfile.TemporaryDirectory(prefix="lwe-objects-live-")
_ROOT = Path(_TMP.name)
_cli_env.scratch_home(_ROOT)
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "src"))

from lwe_ui import api_client, version  # noqa: E402
from lwe_ui.cli import Context, report, select  # noqa: E402
from lwe_ui.cli.verbs.objects import WALLPAPER_WORDS  # noqa: E402
from lwe_ui.engine import push  # noqa: E402
from lwe_ui.storage import lock, paths, settings, tags  # noqa: E402

FIXTURE = TESTS / "fixtures" / "cli" / "scene-objects"
LIB = _ROOT / "lib"
SCENE, OTHER, PLAIN = "1200000001", "1200000002", "1200000003"
LABEL = f"Night Harbor ({SCENE})"
HAND = b"# my parts\r\nVOLUME=40\r\nSKIP=\"4 x\"\r\nMOUSE=true\r\n"
OK = {"ok": True, "status": "done"}


def setUpModule() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(_ROOT / "workshop")})
    shutil.copytree(FIXTURE, LIB / SCENE)
    tags.set_state(SCENE, "Night Harbor", "good")
    (LIB / PLAIN).mkdir(parents=True)
    (LIB / PLAIN / "project.json").write_text('{"title": "Plain", "type": "scene", "file": "scene.json"}')
    (LIB / PLAIN / "scene.json").write_text('{"objects": [{"id": 11, "image": "a.json"}]}')
    tags.set_state(PLAIN, "Plain", "good")


def _pending(subject: str) -> str:
    """The receipt line of a change saved while the engine is away."""
    return f"{subject}: saved; the service is not running or is busy, so it is not applied yet. {report.OPPORTUNITIES}\n"


def _run(word: str, wid: str, *args: str) -> tuple[int, str, str]:
    ctx = Context(False, io.StringIO(), io.StringIO(), None, False)
    code = WALLPAPER_WORDS[word](ctx, select.wallpaper(wid), list(args))
    return code, ctx.out.getvalue(), ctx.err.getvalue()


def _status(on_screen: str = SCENE, **extra) -> dict:
    return {"version": version.panel_stamp(), "current": {"ui_id": on_screen}, "outputs": {"state": "held"},
            **extra}


class _Runner:
    """Records each call of the change runner, then runs it."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.real = push.run_change

    def __call__(self, locks, write, rows, **kwargs):
        self.calls.append((tuple(locks), list(rows), kwargs.get("wid"), kwargs.get("run")))
        return self.real(locks, write, rows, **kwargs)


class _Engine:
    """Scripted status replies in order, the last one repeated; every request recorded."""

    def __init__(self, statuses: list, listening: bool = True, skip_reply: dict | None = None) -> None:
        self.statuses = statuses
        self.listening = listening
        self.skip_reply = OK if skip_reply is None else skip_reply
        self.requests: list[tuple] = []

    def status(self, sock=None):
        n = sum(1 for request in self.requests if request[0] == "status")
        self.requests.append(("status",))
        return self.statuses[min(n, len(self.statuses) - 1)]

    def set_skip(self, ids, sock=None):
        self.requests.append(("set-skip", list(ids)))
        return self.skip_reply

    def available(self, sock=None):
        return self.listening


class HideTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conf = paths.wp_file(SCENE)
        self.conf.write_bytes(HAND)
        self.runner = _Runner()
        patcher = mock.patch.object(push, "run_change", self.runner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _changed(self, before: bytes) -> list[str]:
        old = before.decode().splitlines(keepends=True)
        new = self.conf.read_bytes().decode().splitlines(keepends=True)
        return [line for line in difflib.ndiff(old, new) if line[:2] in ("- ", "+ ")]

    def test_hide_and_unhide_change_only_the_skip_line(self) -> None:
        self.assertEqual(_run("hide", SCENE, "3"),
                         (0, f"{LABEL} part 3 hidden\n" + _pending("Light Rain of Night Harbor"), ""))
        self.assertEqual(self._changed(HAND), ['- SKIP="4 x"\r\n', '+ SKIP="4 x 3"\r\n'])
        hidden = self.conf.read_bytes()
        self.assertEqual(_run("unhide", SCENE, "3"),
                         (0, f"{LABEL} part 3 shown again\n" + _pending("part 3 of Night Harbor"), ""))
        self.assertEqual(self._changed(hidden), ['- SKIP="4 x 3"\r\n', '+ SKIP="4 x"\r\n'])
        self.assertEqual(self.conf.read_bytes(), HAND)

    def test_an_unterminated_quote_is_refused_with_the_file_unchanged(self) -> None:
        text = b'VOLUME=40\nSKIP="4\n'
        self.conf.write_bytes(text)
        code, out, err = _run("hide", SCENE, "3")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("its value opens a quote that its line does not close", err)
        self.assertEqual(self.conf.read_bytes(), text)

    def test_each_change_calls_the_change_runner_once(self) -> None:
        _run("hide", SCENE, "3")
        _run("unhide", SCENE, "3")
        self.assertEqual(self.runner.calls, [(("overrides",), [("wp_live", "SKIP")], SCENE, "command")] * 2)

    def test_a_part_the_scene_lacks_is_refused(self) -> None:
        self.assertEqual(_run("hide", SCENE, "42"), (1, "", f"lwe: {LABEL} has no part 42\n"))
        self.assertEqual((self.conf.read_bytes(), self.runner.calls), (HAND, []))

    def test_an_id_that_is_not_ascii_digits_in_range_is_a_usage_error(self) -> None:
        for word in ("hide", "unhide", "blink"):
            for arg in ("x", "-1", "1000001", "9" * 5000, "\u0663"):
                self.assertEqual(_run(word, SCENE, arg),
                                 (3, "", "lwe: a part id is a whole number from 0 to 1000000\n"), (word, arg))
        self.assertEqual((self.conf.read_bytes(), self.runner.calls), (HAND, []))

    def test_at_most_256_parts_can_be_hidden(self) -> None:
        full = ("SKIP=\"" + " ".join(str(1000 + n) for n in range(256)) + "\"\n").encode()
        self.conf.write_bytes(full)
        self.assertEqual(_run("hide", SCENE, "3"), (1, "", "lwe: at most 256 parts can be hidden\n"))
        self.assertEqual((self.conf.read_bytes(), self.runner.calls), (full, []))
        self.conf.write_bytes(("SKIP=\"" + " ".join(str(1000 + n) for n in range(255)) + "\"\n").encode())
        self.assertEqual(_run("hide", SCENE, "3")[0], 0)
        self.assertTrue(self.conf.read_bytes().endswith(b" 1254 3\"\n"))

    def test_an_emptied_skip_deletes_the_key(self) -> None:
        self.conf.write_bytes(b"VOLUME=40\nSKIP=3\n")
        self.assertEqual(_run("unhide", SCENE, "3"),
                         (0, f"{LABEL} part 3 shown again\n" + _pending("part 3 of Night Harbor"), ""))
        self.assertEqual(self.conf.read_bytes(), b"VOLUME=40\n")

    def test_the_receipt_names_a_part_without_a_name_by_its_id(self) -> None:
        self.assertEqual(_run("hide", PLAIN, "11"),
                         (0, f"Plain ({PLAIN}) part 11 hidden\n" + _pending("part 11 of Plain"), ""))

    def test_a_version_mismatch_inside_sync_prints_its_text(self) -> None:
        engine = _Engine([_status(), _status(version="0.0.1")])
        with mock.patch.object(api_client, "status", engine.status), \
                mock.patch.object(api_client, "set_skip", engine.set_skip):
            code, out, err = _run("hide", SCENE, "3")
        refusal = f"The running engine is 0.0.1 but {version.panel_stamp()} is installed; run lwe service restart."
        self.assertEqual((code, out, err), (1, f"{LABEL} part 3 hidden\n"
                                               f"Light Rain of Night Harbor: saved, but nothing was sent. {refusal}\n",
                                            ""))
        self.assertEqual(engine.requests, [("status",), ("status",)])


class BlinkTest(unittest.TestCase):
    def setUp(self) -> None:
        paths.wp_file(SCENE).write_bytes(b"SKIP=4\n")
        self.sleeps: list[float] = []

    def _blink(self, engine: _Engine, part: str = "3", sleep=None) -> tuple[int, str, str]:
        with mock.patch.object(api_client, "status", engine.status), \
                mock.patch.object(api_client, "set_skip", engine.set_skip), \
                mock.patch.object(api_client, "available", engine.available), \
                mock.patch("time.sleep", sleep or self.sleeps.append):
            return _run("blink", SCENE, part)

    def test_a_blink_hides_the_part_waits_and_shows_it_again(self) -> None:
        engine = _Engine([_status()])
        self.assertEqual(self._blink(engine), (0, f"Blinked part 3 (Light Rain) of {LABEL}\n", ""))
        self.assertEqual(engine.requests, [("status",), ("status",), ("set-skip", [4, 3]), ("status",),
                                           ("set-skip", [4])])
        self.assertEqual(self.sleeps, [2.0])
        self.assertEqual(paths.wp_file(SCENE).read_bytes(), b"SKIP=4\n")

    def test_refusals_before_anything_is_sent(self) -> None:
        cases = [
            (_Engine([_status(OTHER)]), (1, "", f"lwe: {LABEL} is not on screen\n")),
            (_Engine([None], listening=False), (2, "", "lwe: the service is not running\n")),
            (_Engine([None], listening=True), (1, "", "lwe: the service is not answering\n")),
            (_Engine([_status(version="0.0.1")]),
             (1, "", f"lwe: The running engine is 0.0.1 but {version.panel_stamp()} is installed; "
                     "run lwe service restart.\n")),
            (_Engine([_status(outputs={"state": "released"})]),
             (1, "", "lwe: the wallpaper is off the screens; lwe on puts it back\n")),
        ]
        for engine, expected in cases:
            self.assertEqual(self._blink(engine), expected)
            self.assertEqual(engine.requests, [("status",)])
        for part in ("4", "5"):
            engine = _Engine([_status()])
            self.assertEqual(self._blink(engine, part), (1, "", f"lwe: part {part} is already hidden\n"))
            self.assertEqual(engine.requests, [("status",)])
        self.assertEqual(self.sleeps, [])

    def test_no_restore_once_another_wallpaper_is_on_screen(self) -> None:
        engine = _Engine([_status(), _status(), _status(OTHER)])
        self.assertEqual(self._blink(engine), (0, f"Blinked part 3 (Light Rain) of {LABEL}\n", ""))
        self.assertEqual(engine.requests, [("status",), ("status",), ("set-skip", [4, 3]), ("status",)])

    def test_a_signal_in_the_wait_restores_then_exits_1(self) -> None:
        engine = _Engine([_status()])
        code, out, err = self._blink(engine, sleep=lambda seconds: os.kill(os.getpid(), signal.SIGINT))
        self.assertEqual((code, out, err), (1, "", "lwe: blink stopped; part 3 is shown again\n"))
        self.assertEqual(engine.requests, [("status",), ("status",), ("set-skip", [4, 3]), ("status",),
                                           ("set-skip", [4])])

    def test_a_sync_lock_held_past_the_wait_refuses_as_busy(self) -> None:
        held, release = threading.Event(), threading.Event()

        def hold() -> None:
            with lock.held("sync"):
                held.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(held.wait(5))
            engine = _Engine([_status()])
            with mock.patch.object(api_client, "status", engine.status), \
                    mock.patch.object(api_client, "set_skip", engine.set_skip), \
                    mock.patch.object(api_client, "available", engine.available):
                code, out, err = _run("blink", SCENE, "3")
        finally:
            release.set()
            holder.join()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Store busy", err)
        self.assertEqual(engine.requests, [("status",)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
