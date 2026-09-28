"""lwe add --playlist, lwe remove and lwe trash: playlist changes through the change runner.

Every command runs in a child whose PySide6 import raises and whose environment is built from
nothing. The engine is tests/_fake_engine.py on a scratch socket, or away (no socket). With the
engine answering, add --playlist appends and the engine gets the playlist and the lane; an imported
id that an engine-held playlist already lists leads to one sync_all; a trash leaves every playlist,
deletes LWE's copy and says the Steam download stays; an engine refusal is saved and exits 1;
another build's engine refuses before anything is written. With the engine away the change is
saved with its marker and the receipt is pending, exit 0. Already in a playlist, not in a playlist
and already in the trash say so; an unknown playlist leaves every file byte-identical; remove
without --playlist and the other forms typed wrong exit 3; trash then untrash is a round trip. In
process, each command passes run="command" and its one status read to its change, and the change
receipt's texts are checked.

Run: PYTHONPATH=src python3 tests/test_cli_library_trash.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import hashlib
import json
import os
import subprocess
import sys
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.TemporaryDirectory(prefix="lwe-cli-trash-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from _fake_engine import FakeEngine, fail  # noqa: E402
from lwe_ui import version  # noqa: E402
from lwe_ui.cli import report  # noqa: E402
from lwe_ui.engine import marker, push  # noqa: E402
from lwe_ui.storage import paths, playlists, records, settings, tags  # noqa: E402

LIB = _ROOT / "lib"
WORKSHOP = _ROOT / "workshop"
FAKE_SOCK = _ROOT / "rt" / "fake.sock"
AWAY_SOCK = _ROOT / "rt" / "away.sock"
_RUNS: dict[str, subprocess.CompletedProcess] = {}
_SAME: dict[str, bool] = {}
_CALLS: dict[str, list] = {}
_FACTS: dict[str, object] = {}
OPPORTUNITIES = report.OPPORTUNITIES


def _scene(folder: Path, title: str, extra: dict | None = None) -> None:
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({"title": title, "type": "scene", "file": "scene.json",
                                                     **(extra or {})}), encoding="utf-8")
    (folder / "scene.json").write_text("{}", encoding="utf-8")


def _tree() -> dict[str, str]:
    """{path: sha256} of every file in the stores, the state tree, the library and the Workshop."""
    out = {}
    for base in (_ROOT / "c", _ROOT / "s", _ROOT / "d", LIB, WORKSHOP):
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(_ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _child(words: list[str], sock: Path) -> subprocess.CompletedProcess:
    env = {"HOME": str(_ROOT), "XDG_CONFIG_HOME": str(_ROOT / "c"), "XDG_STATE_HOME": str(_ROOT / "s"),
           "XDG_DATA_HOME": str(_ROOT / "d"), "XDG_RUNTIME_DIR": str(_ROOT / "rt"), "LWE_SOCKET": str(sock),
           "PATH": str(_ROOT / "bin"), "PYTHONPATH": os.pathsep.join([str(_ROOT / "poison"), str(SRC)]),
           "LWE_SANDBOX": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "lwe_ui", "--lwe", version.panel_stamp(), str(_ROOT / "cwd"),
                           *words], env=env, cwd=_ROOT / "cwd", capture_output=True, encoding="utf-8",
                          timeout=120)


def _run(name: str, words: list[str], engine: FakeEngine | None = None) -> None:
    before = _tree()
    if engine is not None:
        engine.calls.clear()
    _RUNS[name] = _child(words, FAKE_SOCK if engine is not None else AWAY_SOCK)
    _SAME[name] = _tree() == before
    if engine is not None:
        _CALLS[name] = [(cmd, args) for cmd, args in engine.calls]


def _stores() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.update({"WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(WORKSHOP), "ACTIVE_PLAYLIST": "chill"})
    for wid, title in (("1600000001", "Alpha"), ("1600000002", "Bravo"), ("1600000003", "Charlie"),
                       ("1600000004", "Delta"), ("1600000005", "Echo Base")):
        _scene(LIB / wid, title)
        tags.set_state(wid, title, "good")
    _scene(WORKSHOP / "1600000004", "Delta")
    _scene(WORKSHOP / "1600000006", "Foxtrot Preset", {"dependency": "1600000005"})
    _scene(WORKSHOP / "1600000009", "India")
    for slug, name, members in (("chill", "Chill", "1600000001 1600000004 1600000009"),
                                ("work", "Work", "1600000002 1600000004"), ("spare", "Spare", "1600000099")):
        playlists.save(slug, {"NAME": name, "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min", "MEMBERS": members})


def setUpModule() -> None:
    _stores()
    poison = _ROOT / "poison" / "PySide6"
    poison.mkdir(parents=True)
    (poison / "__init__.py").write_text('raise ImportError("PySide6 is unavailable in this test")\n',
                                        encoding="utf-8")
    for sub in ("rt", "cwd", "bin"):
        (_ROOT / sub).mkdir()
    bite = subprocess.run([sys.executable, "-c", "import PySide6"], capture_output=True, text=True,
                          env={"PYTHONPATH": str(_ROOT / "poison"), "PATH": str(_ROOT / "bin")}, timeout=60)
    if bite.returncode == 0 or "ImportError" not in bite.stderr:
        raise AssertionError(f"the PySide6 poison did not bite: {bite.returncode} {bite.stderr}")
    for name, words in (("unknown-playlist", ["add", "1600000003", "--playlist", "nosuch"]),
                        ("add-no-value", ["add", "1600000003", "--playlist"]),
                        ("remove-bare", ["remove", "1600000001"]),
                        ("remove-no-word", ["remove", "--playlist", "chill"]),
                        ("remove-option", ["remove", "1600000001", "--playlist", "chill", "--now"]),
                        ("trash-bare", ["trash"]), ("trash-option", ["trash", "--now", "1600000001"])):
        _run(name, words)
    with FakeEngine(FAKE_SOCK) as engine:
        _run("applied-add", ["add", "1600000002", "--playlist", "chill"], engine)
        _FACTS["applied-add"] = (playlists.members("chill"), marker.read()["classes"])
        _run("sync", ["add", "1600000009"], engine)
        _FACTS["sync"] = tags.load()
        _run("trash-applied", ["trash", "1600000004"], engine)
        _FACTS["trash-applied"] = (playlists.members("chill"), playlists.members("work"),
                                   next(r["state"] for r in tags.load() if r["id"] == "1600000004"),
                                   (records.head("1600000004") or {}).get("action"), (LIB / "1600000004").exists(),
                                   (WORKSHOP / "1600000004").is_dir(), marker.read()["classes"])
        engine.script("playlist-set", fail("nope"))
        _run("refused", ["remove", "1600000002", "--playlist", "chill"], engine)
        _FACTS["refused"] = (playlists.members("chill"), marker.read()["classes"])
    with FakeEngine(FAKE_SOCK, version="0.0.1") as engine:
        _run("version", ["trash", "1600000003"], engine)
    _run("away-add", ["-j", "add", "1600000003", "--playlist", "work"])
    _FACTS["away-add"] = (playlists.members("work"), marker.read()["classes"])
    _run("already", ["add", "1600000001", "--playlist", "chill"])
    _run("away-remove", ["remove", "1600000002", "--playlist", "work"])
    _FACTS["away-remove"] = playlists.members("work")
    _run("not-in", ["remove", "1600000001", "--playlist", "spare"])
    _run("member-only", ["-j", "remove", "1600000099", "--playlist", "spare"])
    _FACTS["member-only"] = playlists.members("spare")
    _run("trash-base", ["trash", "1600000005"])
    _run("trash-again", ["trash", "1600000005"])
    _run("trash-json", ["-j", "trash", "1600000003"])
    _FACTS["trash-json"] = playlists.members("work")
    _run("untrash", ["untrash", "1600000004"])
    _run("workshop", ["workshop"])


def _line(label: str, text: str) -> str:
    return f"{label:<28} {text}"


def _cmds(name: str) -> list[str]:
    return [cmd for cmd, _args in _CALLS[name]]


class RefusalTest(unittest.TestCase):
    def assertRefused(self, name: str, message: str, code: int) -> None:
        r = _RUNS[name]
        self.assertEqual((r.returncode, r.stdout, r.stderr), (code, "", f"lwe: {message}\n"))
        self.assertTrue(_SAME[name], "a file changed")

    def test_an_unknown_playlist_changes_nothing_at_all(self) -> None:
        self.assertRefused("unknown-playlist", "no playlist matches nosuch", 1)

    def test_typed_wrong(self) -> None:
        cases = (("add-no-value", "add takes wallpapers"),
                 ("remove-bare", "remove only takes wallpapers out of playlists; use trash to take one out of "
                                 "the pool"),
                 ("remove-no-word", "remove takes wallpapers and --playlist <playlist>"),
                 ("remove-option", "remove takes wallpapers and --playlist <playlist>"),
                 ("trash-bare", "trash takes wallpapers"), ("trash-option", "trash takes wallpapers"))
        for name, message in cases:
            with self.subTest(run=name):
                self.assertRefused(name, message, 3)

    def test_another_builds_engine_refuses_before_anything_is_written(self) -> None:
        self.assertRefused("version", f"The running engine is 0.0.1 but {version.panel_stamp()} is installed; "
                                      "run lwe service restart.", 1)
        self.assertEqual(_cmds("version"), ["status"])


class EngineTest(unittest.TestCase):
    def test_add_with_a_playlist_appends_and_the_engine_gets_it(self) -> None:
        r = _RUNS["applied-add"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout.splitlines(), [_line("Bravo (1600000002)", "already in the pool"),
                                                 _line("Bravo (1600000002)", "appended to Chill"),
                                                 "Playlist Chill: saved; applies now."])
        self.assertEqual(_FACTS["applied-add"], (["1600000001", "1600000004", "1600000009", "1600000002"], []))
        self.assertEqual(_cmds("applied-add"), ["status", "status", "playlist-set", "lanes-set"])
        self.assertEqual(_CALLS["applied-add"][2][1]["slug"], "chill")
        self.assertEqual(_CALLS["applied-add"][3][1]["lanes"], [{"id": "all", "enabled": True}])

    def test_an_imported_id_an_engine_held_playlist_lists_leads_to_one_sync_all(self) -> None:
        r = _RUNS["sync"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout.splitlines(), [
            _line("India (1600000009)", "imported into the pool; scene, nothing to compress"),
            "Playlist Chill: saved; applies now."])
        cmds = _cmds("sync")
        self.assertEqual((cmds.count("status"), cmds.count("schedule-set"), cmds.count("lanes-set")), (1, 1, 1))
        self.assertEqual([args["slug"] for cmd, args in _CALLS["sync"] if cmd == "playlist-set"], ["chill"])
        self.assertEqual(next(t["state"] for t in _FACTS["sync"] if t["id"] == "1600000009"), "good")

    def test_a_trash_leaves_every_playlist_and_deletes_lwes_copy(self) -> None:
        r = _RUNS["trash-applied"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout.splitlines(), [
            _line("Delta (1600000004)",
                  "trashed; LWE's copy was deleted; the Steam download stays; left Chill, Work"),
            "Playlists Chill, Work: saved; applies now."])
        self.assertEqual(_FACTS["trash-applied"], (["1600000001", "1600000009", "1600000002"], ["1600000002"],
                                                   "bad", "deleted", False, True, []))
        self.assertEqual(_cmds("trash-applied"), ["status", "status", "playlist-set", "lanes-set"])
        self.assertEqual(_CALLS["trash-applied"][2][1]["slug"], "chill")


    def test_an_engine_refusal_is_saved_and_exits_1(self) -> None:
        r = _RUNS["refused"]
        self.assertEqual((r.returncode, r.stderr), (1, ""))
        self.assertEqual(r.stdout.splitlines(), [
            _line("Bravo (1600000002)", "taken out of Chill"),
            "Playlist Chill: saved, but the engine refused it: nope. " + OPPORTUNITIES])
        self.assertEqual(_FACTS["refused"], (["1600000001", "1600000009"], ["BUNDLE"]))
        self.assertEqual(_cmds("refused"), ["status", "status", "playlist-set"])


class AwayTest(unittest.TestCase):
    def test_with_the_engine_away_the_change_is_saved_pending_with_its_marker(self) -> None:
        r = _RUNS["away-add"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(json.loads(r.stdout), {"results": [
            {"id": "1600000003", "title": "Charlie", "result": "already"},
            {"slug": "work", "name": "Work", "id": "1600000003", "result": "appended", "title": "Charlie"}],
            "receipt": {"subject": "Playlist Work", "saved": True, "outcome": "pending", "reason": "away",
                        "message": ""}})
        self.assertEqual(_FACTS["away-add"], (["1600000002", "1600000003"], ["BUNDLE"]))

    def test_remove_takes_a_wallpaper_out_and_says_pending(self) -> None:
        r = _RUNS["away-remove"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout.splitlines(), [
            _line("Bravo (1600000002)", "taken out of Work"),
            "Playlist Work: saved; the service is not running or is busy, so it is not applied yet. "
            + OPPORTUNITIES])
        self.assertEqual(_FACTS["away-remove"], ["1600000003"])

    def test_already_in_a_playlist_and_not_in_one_say_so(self) -> None:
        r = _RUNS["already"]
        self.assertEqual((r.returncode, r.stdout), (0, _line("Alpha (1600000001)", "already in the pool") + "\n"
                                                    + _line("Alpha (1600000001)", "already in Chill") + "\n"))
        r = _RUNS["not-in"]
        self.assertEqual((r.returncode, r.stdout), (0, _line("Alpha (1600000001)", "not in Spare") + "\n"))
        self.assertTrue(_SAME["not-in"])

    def test_remove_reaches_an_id_only_a_playlist_lists(self) -> None:
        r = _RUNS["member-only"]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"results": [
            {"slug": "spare", "name": "Spare", "id": "1600000099", "result": "removed", "title": "1600000099"}],
            "receipt": {"subject": "Playlist Spare", "saved": True, "outcome": "pending", "reason": "away",
                        "message": ""}})
        self.assertEqual(_FACTS["member-only"], [])

    def test_trash_texts_and_json(self) -> None:
        r = _RUNS["trash-base"]
        self.assertEqual((r.returncode, r.stdout), (0, _line(
            "Echo Base (1600000005)", "trashed; LWE's copy was deleted; 1 other downloads use it as their base")
            + "\n"))
        r = _RUNS["trash-again"]
        self.assertEqual((r.returncode, r.stdout),
                         (0, _line("Echo Base (1600000005)", "already in the trash") + "\n"))
        r = _RUNS["trash-json"]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"results": [
            {"id": "1600000003", "title": "Charlie", "result": "trashed", "copy_deleted": True,
             "steam_download": False, "left": ["Work"], "dependents": 0}],
            "receipt": {"subject": "Playlist Work", "saved": True, "outcome": "pending", "reason": "away",
                        "message": ""}})
        self.assertEqual(_FACTS["trash-json"], [])

    def test_trash_then_untrash_is_a_round_trip(self) -> None:
        r = _RUNS["untrash"]
        self.assertEqual((r.returncode, r.stdout),
                         (0, _line("Delta (1600000004)", "can be imported again") + "\n"))
        listed = _RUNS["workshop"].stdout.splitlines()
        self.assertTrue(any(line.endswith("  Delta (1600000004)") for line in listed), listed)


class CommandTest(unittest.TestCase):
    """The verbs in process, with the actions and the status read replaced: each command passes
    run="command" and its one status read to its change."""

    def _call(self, verb, words: list[str], name: str, value) -> tuple[int, mock.MagicMock]:
        from lwe_ui.cli import Context
        from lwe_ui.cli.verbs import library
        from lwe_ui.library import actions
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(push, "read_status", return_value=("away", None)), \
                mock.patch.object(actions, name, return_value=value) as change:
            code = verb(library)(Context(False, out, err, None, False), words)
        return code, change

    def test_each_command_passes_run_command_and_its_status_read(self) -> None:
        applied = push.Outcome("applied")
        code, trash = self._call(lambda lib: lib._trash, ["1600000001"], "trash", ([{
            "id": "1600000001", "title": "Alpha", "already": True, "left": [], "copy_deletable": False,
            "dependents": 0}], applied))
        self.assertEqual(code, 0)
        trash.assert_called_once_with([("1600000001", "Alpha")], record=True, run="command", status=("away", None))
        code, remove = self._call(lambda lib: lib._remove, ["1600000001", "--playlist", "chill"],
                                  "remove_from_playlists", ([], applied))
        remove.assert_called_once_with(["chill"], ["1600000001"], run="command", status=("away", None))
        code, add = self._call(lambda lib: lib._add, ["1600000001", "--playlist", "chill"], "add_to_playlists",
                               ([], applied))
        add.assert_called_once_with(["chill"], ["1600000001"], run="command", status=("away", None))


class ReceiptTest(unittest.TestCase):
    def test_the_change_receipt_texts(self) -> None:
        cases = ((push.Outcome("applied"), "Playlist Chill: saved; applies now."),
                 (push.Outcome("applied", warning="the sync marker could not be cleared: disk full"),
                  "Playlist Chill: saved; applies now, but the sync marker could not be cleared: disk full."),
                 (push.Outcome("pending", reason="busy"),
                  "Playlist Chill: saved; the service is not running or is busy, so it is not applied yet. "
                  + OPPORTUNITIES),
                 (push.Outcome("refused", message="no such playlist"),
                  "Playlist Chill: saved, but the engine refused it: no such playlist. " + OPPORTUNITIES),
                 (push.Outcome("uncertain"),
                  "Playlist Chill: saved; the engine did not answer in time, so it may have applied. "
                  + OPPORTUNITIES),
                 (push.Outcome("pending", reason="version", message="The running engine is 0.0.1 but 1.1.0 is "
                               "installed; run lwe service restart."),
                  "Playlist Chill: saved, but nothing was sent. The running engine is 0.0.1 but 1.1.0 is "
                  "installed; run lwe service restart."))
        for outcome, text in cases:
            with self.subTest(outcome=outcome):
                r = report.change_receipt("Playlist Chill", outcome)
                self.assertEqual(tuple(r), report.CHANGE_KEYS)
                self.assertEqual(report.change_text(r), text)
        self.assertEqual(report.change_receipt("Playlists Chill, Work", push.Outcome("refused", message="no")),
                         {"subject": "Playlists Chill, Work", "saved": True, "outcome": "refused", "reason": "",
                          "message": "no"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
