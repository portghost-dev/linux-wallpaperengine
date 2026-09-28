"""lwe add and lwe untrash: bringing wallpapers into the pool and lifting trash blocks.

Every command runs in a child whose PySide6 import raises and whose environment is built from
nothing, with the stub encoder. A download is imported, approved, recorded and compressed; a
waiting item is approved; a pool item is already in the pool; a held preset whose base arrived is
wired and approved and names the base imported on the way, as does a preset download whose base is
a download too; a base already in the pool is not named, and a base named after its preset is
approved in its turn. A held item the wiring left held is not approved. A missing base, an
ambiguous word, a trashed pick, a pick only on screen or a refusal on a later word changes no file.
A failed import is reported on its line and the rest continue; so is a package that cannot be
read, and nothing is imported or approved for it, in text and under -j. untrash alone lists the trash;
untrash lifts a block, with or without files, and the wallpaper can be added again. No child
connects to the engine socket.

Run: PYTHONPATH=src python3 tests/test_cli_library_add.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import hashlib
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.TemporaryDirectory(prefix="lwe-add-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from _scene_pkg import STUB_ENCODER, _pkg, _tex  # noqa: E402
from lwe_ui import version  # noqa: E402
from lwe_ui.storage import importer, meta, paths, records, settings, tags, wp  # noqa: E402

LIB = _ROOT / "lib"
WORKSHOP = _ROOT / "workshop"
CACHE = _ROOT / "s" / "lwe" / "engine" / "texcache"
BIN = _ROOT / "bin"
SOCK = _ROOT / "rt" / "engine.sock"
ARGB_AFTER = 16384 + 4096 + 1024 + 256 + 64 + 16 + 16 + 16
NINE_BYTES = b"\x04\x00\x00\x00PKGV\x01"
_RUNS: dict[str, subprocess.CompletedProcess] = {}
_SAME: dict[str, bool] = {}
_FACTS: dict[str, dict] = {}
_CONNECTIONS: list[int] = []


def _scene(folder: Path, title: str, seed: int | None, extra: dict | None = None) -> None:
    """A scene folder; seed gives it one 128x128 ARGB8888 texture, None leaves it without a payload."""
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({"title": title, "type": "scene", "file": "scene.json",
                                                     **(extra or {})}), encoding="utf-8")
    if seed is not None:
        pixels = bytes((i * 7 + seed) % 256 for i in range(128 * 128 * 4))
        (folder / "scene.pkg").write_bytes(_pkg({"materials/t.tex": _tex(0, 128, 128, pixels)}))


def _preset(wid: str, title: str, base: str) -> None:
    _scene(WORKSHOP / wid, title, None, {"dependency": base, "preset": {"speed": 2}})


def _trashed(wid: str, title: str, record: bool = True) -> None:
    tags.set_state(wid, title, "bad")
    if record:
        records.append(wid, records.make_event("deleted", where="library", initiator="human"))


def _tree() -> dict[str, str]:
    """{path: sha256} of every file in the stores, the state tree, the library and the Workshop."""
    out = {}
    for base in (_ROOT / "c", _ROOT / "s", _ROOT / "d", LIB, WORKSHOP):
        for p in sorted(base.rglob("*")):
            if p.is_file() and os.access(p, os.R_OK):
                out[str(p.relative_to(_ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _tag(wid: str) -> str | None:
    return next((r["state"] for r in tags.load() if r["id"] == wid), None)


def _events(wid: str) -> list[tuple[str, str, str]]:
    return [(e["action"], e["where"], e["initiator"]) for e in records.read(wid)]


def _owners() -> set[str]:
    return {json.loads(p.read_text())["wallpaper"] for p in CACHE.glob("*.meta")} if CACHE.is_dir() else set()


def _child(words: list[str]) -> subprocess.CompletedProcess:
    env = {"HOME": str(_ROOT), "XDG_CONFIG_HOME": str(_ROOT / "c"), "XDG_STATE_HOME": str(_ROOT / "s"),
           "XDG_DATA_HOME": str(_ROOT / "d"), "XDG_RUNTIME_DIR": str(_ROOT / "rt"), "LWE_SOCKET": str(SOCK),
           "PATH": str(BIN), "PYTHONPATH": os.pathsep.join([str(_ROOT / "poison"), str(SRC)]),
           "LWE_SANDBOX": "1", "PYTHONDONTWRITEBYTECODE": "1", "LWE_BC7ENC": str(BIN / "stub_bc7enc")}
    return subprocess.run([sys.executable, "-m", "lwe_ui", "--lwe", version.panel_stamp(), str(_ROOT / "cwd"),
                           *words], env=env, cwd=_ROOT / "cwd", capture_output=True, encoding="utf-8",
                          timeout=300)


def _run(name: str, words: list[str]) -> None:
    before = _tree()
    _RUNS[name] = _child(words)
    _SAME[name] = _tree() == before


def _stores() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(WORKSHOP)})
    LIB.mkdir()
    WORKSHOP.mkdir()
    _scene(LIB / "1400000001", "Alpha", 1)
    tags.set_state("1400000001", "Alpha", "good")
    paths.wp_file("1400000001").write_text("BG=1400000001\n", encoding="utf-8")
    _scene(WORKSHOP / "1400000002", "Bravo", 2)
    _scene(LIB / "1400000003", "Charlie", 3)
    tags.set_state("1400000003", "Charlie", "review")
    _preset("1400000004", "Delta Preset", "1400000005")
    _preset("1400000007", "Golf Held", "1400000098")
    _preset("1400000018", "Romeo Held", "1400000019")
    for wid in ("1400000004", "1400000007", "1400000018"):
        if importer.import_one(wid)["action"] != "imported-missing-dep":
            raise AssertionError(f"{wid} was not held")
    _scene(WORKSHOP / "1400000005", "Echo Base", 5)
    _preset("1400000006", "Foxtrot Preset", "1400000099")
    _scene(LIB / "1400000008", "Twin", 8)
    tags.set_state("1400000008", "Twin", "good")
    _scene(WORKSHOP / "1400000009", "Twin", 9)
    _scene(WORKSHOP / "1400000010", "Hotel", 10)
    _trashed("1400000010", "Hotel")
    _trashed("1400000011", "India")
    _preset("1400000012", "Juliet Preset", "1400000013")
    _scene(WORKSHOP / "1400000013", "Kilo Base", 13)
    _scene(WORKSHOP / "1400000014", "Lima", 14)
    _trashed("1400000014", "Lima")
    _trashed("1400000015", "Mike", record=False)
    _scene(WORKSHOP / "1400000016", "Papa", 16)
    (WORKSHOP / "1400000016" / "locked.bin").write_bytes(b"x")
    (WORKSHOP / "1400000016" / "locked.bin").chmod(0)
    _scene(WORKSHOP / "1400000017", "Quebec", 17)
    _preset("1400000020", "Tango Preset", "1400000001")
    _preset("1400000021", "Uniform Preset", "1400000022")
    _scene(WORKSHOP / "1400000022", "Victor Base", 22)
    _scene(WORKSHOP / "1400000023", "Sierra\u0007Dunes", 23)
    _scene(WORKSHOP / "1400000031", "Whiskey", 31)
    _scene(WORKSHOP / "1400000032", "X-ray", None)
    _scene(LIB / "1400000033", "Yankee", None)
    tags.set_state("1400000033", "Yankee", "review")
    _scene(WORKSHOP / "1400000034", "Zulu", 34)
    _scene(WORKSHOP / "1400000035", "Oscar", 35)
    _scene(WORKSHOP / "1400000036", "Nine Bytes", None)
    for folder in (WORKSHOP / "1400000032", LIB / "1400000033", WORKSHOP / "1400000036"):
        (folder / "scene.pkg").write_bytes(NINE_BYTES)
    _scene(WORKSHOP / "1400000037", "Linked", None)
    (_ROOT / "outside").mkdir()
    pixels = bytes((i * 7 + 37) % 256 for i in range(128 * 128 * 4))
    (_ROOT / "outside" / "foreign.pkg").write_bytes(_pkg({"materials/t.tex": _tex(0, 128, 128, pixels)}))
    (_ROOT / "outside" / "loose.tex").write_bytes(_tex(0, 128, 128, pixels))
    (WORKSHOP / "1400000037" / "scene.pkg").symlink_to(_ROOT / "outside" / "foreign.pkg")
    (WORKSHOP / "1400000037" / "extra.tex").symlink_to(_ROOT / "outside" / "loose.tex")
    _scene(LIB / "1400000038", "Linked Pool", None)
    tags.set_state("1400000038", "Linked Pool", "good")
    _scene(WORKSHOP / "1400000039", "Linked Json", None)
    _scene(WORKSHOP / "1400000040", "Linked Newline", None)
    for folder in (LIB / "1400000038", WORKSHOP / "1400000039", WORKSHOP / "1400000040"):
        (folder / "scene.pkg").symlink_to(_ROOT / "outside" / "foreign.pkg")
    (WORKSHOP / "1400000040" / "a\nnot added: SPOOF.pkg").symlink_to(_ROOT / "outside" / "foreign.pkg")
    _scene(WORKSHOP / "1400000041", "Linked Controls", None)
    for name in ("scene.pkg", "a\tb.pkg", "c\x1bd.pkg", "e\x9bf.pkg", "g\u2028h.pkg", "i\u2029j.pkg"):
        (WORKSHOP / "1400000041" / name).symlink_to(_ROOT / "outside" / "foreign.pkg")
    for preset, base, title in (("1400000042", "1400000043", "Base Links"),
                                ("1400000044", "1400000045", "Base Links Json")):
        _preset(preset, f"Preset of {title}", base)
        (WORKSHOP / preset / "own.tex").symlink_to(_ROOT / "outside" / "loose.tex")
        _scene(WORKSHOP / base, title, int(base[-2:]))
        (WORKSHOP / base / "extra.tex").symlink_to(_ROOT / "outside" / "loose.tex")


def setUpModule() -> None:
    _stores()
    poison = _ROOT / "poison" / "PySide6"
    poison.mkdir(parents=True)
    (poison / "__init__.py").write_text('raise ImportError("PySide6 is unavailable in this test")\n',
                                        encoding="utf-8")
    for sub in ("rt", "cwd"):
        (_ROOT / sub).mkdir()
    BIN.mkdir()
    (BIN / "python3").symlink_to(sys.executable)
    stub = BIN / "stub_bc7enc"
    stub.write_text(STUB_ENCODER, encoding="utf-8")
    stub.chmod(0o755)
    bite = subprocess.run([sys.executable, "-c", "import PySide6"], capture_output=True, text=True,
                          env={"PYTHONPATH": str(_ROOT / "poison"), "PATH": str(BIN)}, timeout=60)
    if bite.returncode == 0 or "ImportError" not in bite.stderr:
        raise AssertionError(f"the PySide6 poison did not bite: {bite.returncode} {bite.stderr}")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(SOCK))
    listener.listen(64)
    listener.setblocking(False)
    try:
        _commands()
    finally:
        (WORKSHOP / "1400000016" / "locked.bin").chmod(stat.S_IRUSR | stat.S_IWUSR)
        count = 0
        while True:
            try:
                conn, _ = listener.accept()
            except BlockingIOError:
                break
            conn.close()
            count += 1
        listener.close()
        _CONNECTIONS.append(count)


def _commands() -> None:
    _run("list", ["untrash"])
    _run("list-json", ["-j", "untrash"])
    for name, words in (("base-download", ["add", "1400000006"]), ("base-held", ["add", "1400000007"]),
                        ("ambiguous", ["add", "Twin"]), ("trashed", ["add", "1400000011"]),
                        ("late-refusal", ["add", "1400000002", "1400000006"]), ("bare", ["add"]),
                        ("option", ["add", "--now", "1400000001"]),
                        ("untrash-both", ["untrash", "--all", "1400000010"]),
                        ("untrash-option", ["untrash", "--bogus"]), ("pool", ["add", "1400000001"])):
        _run(name, words)
    _run("download", ["-j", "add", "1400000002"])
    _FACTS["download"] = {"tag": _tag("1400000002"), "events": _events("1400000002"),
                          "copied": (LIB / "1400000002" / "scene.pkg").is_file(), "owners": _owners()}
    _run("linked", ["add", "1400000037"])
    _FACTS["linked"] = {"tag": _tag("1400000037"), "copied": sorted(p.name for p in (LIB / "1400000037").iterdir()),
                        "owners": _owners()}
    _run("linked-compress", ["compress", "1400000038"])
    _run("linked-compress-json", ["-j", "compress", "1400000038"])
    _run("linked-json", ["-j", "add", "1400000039"])
    _run("linked-newline", ["add", "1400000040"])
    _FACTS["linked-newline"] = {"owners": _owners()}
    _run("clean-title", ["add", "1400000023"])
    _FACTS["clean-title"] = {(r["state"], r["title"]) for r in tags.load() if r["id"] == "1400000023"}
    _run("waiting", ["add", "1400000003"])
    _FACTS["waiting"] = {"tag": _tag("1400000003"), "events": _events("1400000003"), "owners": _owners()}
    _run("held", ["add", "1400000004"])
    _FACTS["held"] = {"tag": _tag("1400000004"), "base": _tag("1400000005"), "events": _events("1400000004"),
                      "meta": meta.get("1400000004").get("depMissing"), "bg": wp.load("1400000004").get("BG"),
                      "owners": _owners()}
    _run("preset", ["add", "1400000012"])
    _FACTS["preset"] = {"tag": _tag("1400000012"), "base": _tag("1400000013"), "events": _events("1400000012"),
                        "bg": wp.load("1400000012").get("BG"), "owners": _owners()}
    _run("known-base", ["add", "1400000020"])
    _FACTS["known-base"] = {"tag": _tag("1400000020"), "bg": wp.load("1400000020").get("BG"), "owners": _owners()}
    _run("later-base", ["add", "1400000021", "1400000022"])
    _FACTS["later-base"] = {"tags": (_tag("1400000021"), _tag("1400000022")),
                            "events": (_events("1400000021"), _events("1400000022"))}
    _run("failure", ["add", "1400000016", "1400000017"])
    _FACTS["failure"] = {"tags": (_tag("1400000016"), _tag("1400000017")),
                         "staged": sorted(p.name for p in LIB.iterdir() if p.name.startswith("."))}
    unreadable = ("1400000031", "1400000032", "1400000033", "1400000034")
    _run("unreadable", ["add", *unreadable])
    _FACTS["unreadable"] = {"tags": [_tag(w) for w in unreadable], "events": [_events(w) for w in unreadable],
                            "copied": (LIB / "1400000032").exists(), "owners": _owners()}
    _run("unreadable-json", ["-j", "add", "1400000035", "1400000036"])
    _FACTS["unreadable-json"] = {"tags": [_tag("1400000035"), _tag("1400000036")],
                                 "copied": (LIB / "1400000036").exists()}
    _run("untrash", ["untrash", "1400000010"])
    _FACTS["untrash"] = {"tag": _tag("1400000010"), "events": _events("1400000010")}
    _run("workshop", ["workshop"])
    _run("readd", ["add", "1400000010"])
    _FACTS["readd"] = {"tag": _tag("1400000010"), "events": _events("1400000010")}
    _run("gone", ["untrash", "1400000011"])
    _FACTS["gone"] = {"tag": _tag("1400000011"), "events": _events("1400000011")}
    _run("all", ["-j", "untrash", "--all"])
    _FACTS["all"] = {"tags": (_tag("1400000014"), _tag("1400000015")),
                     "events": (_events("1400000014"), _events("1400000015"))}
    _run("empty", ["untrash"])
    _run("linked-controls", ["add", "1400000041"])
    _run("base-links", ["add", "1400000042"])
    _run("base-links-json", ["-j", "add", "1400000044"])


def _line(label: str, text: str) -> str:
    return f"{label:<28} {text}"


class RefusalTest(unittest.TestCase):
    def assertRefused(self, name: str, stderr: str, code: int = 1) -> None:
        r = _RUNS[name]
        self.assertEqual((r.returncode, r.stdout), (code, ""), r.stderr)
        self.assertEqual(r.stderr.splitlines()[0], stderr)
        self.assertTrue(_SAME[name], "a file changed")

    def test_a_missing_base_is_refused_and_named(self) -> None:
        self.assertRefused("base-download", "lwe: Foxtrot Preset (1400000006) needs its base 1400000099, "
                                            "which is not here")
        self.assertRefused("base-held", "lwe: Golf Held (1400000007) needs its base 1400000098, which is not here")

    def test_an_ambiguous_word_changes_nothing(self) -> None:
        self.assertRefused("ambiguous", "lwe: Twin matches more than one wallpaper; nothing was changed")

    def test_a_trashed_pick_is_refused(self) -> None:
        self.assertRefused("trashed", "lwe: India (1400000011) is in the trash; untrash it first")

    def test_a_refusal_on_a_later_word_changes_nothing(self) -> None:
        self.assertRefused("late-refusal", "lwe: Foxtrot Preset (1400000006) needs its base 1400000099, "
                                           "which is not here")

    def test_typed_wrong(self) -> None:
        self.assertRefused("bare", "lwe: add takes wallpapers", 3)
        self.assertRefused("option", "lwe: add takes wallpapers", 3)
        self.assertRefused("untrash-both", "lwe: untrash takes wallpapers or --all", 3)
        self.assertRefused("untrash-option", "lwe: untrash takes wallpapers or --all", 3)

    def test_a_pick_only_on_screen_is_refused(self) -> None:
        from lwe_ui.cli import Context, select
        from lwe_ui.cli.verbs import library
        pick = select.Pick(None, "1400000077", "1400000077", "Screen Only", "", "screen")
        out, err = io.StringIO(), io.StringIO()
        before = _tree()
        with mock.patch.object(select, "wallpapers", return_value=[pick]) as picked:
            code = library._add(Context(False, out, err, None, False), ["current"])
        picked.assert_called_once_with(["current"])
        self.assertEqual((code, out.getvalue()), (1, ""))
        self.assertEqual(err.getvalue(), "lwe: Screen Only (1400000077) is not in the pool or the Workshop list\n")
        self.assertEqual(_tree(), before)


class AddTest(unittest.TestCase):
    def test_a_pool_item_is_already_in_the_pool(self) -> None:
        r = _RUNS["pool"]
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, _line("Alpha (1400000001)", "already in the pool")
                                                               + "\n", ""))
        self.assertTrue(_SAME["pool"])

    def test_a_download_is_imported_approved_recorded_and_compressed(self) -> None:
        r = _RUNS["download"]
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        disk = result["results"][0]["compress"].pop("disk_bytes")
        self.assertGreater(disk, ARGB_AFTER)
        self.assertEqual(result, {"results": [{
            "id": "1400000002", "title": "Bravo", "result": "imported",
            "compress": {"result": "compressed", "bytes_before": 65536, "bytes_after": ARGB_AFTER, "failed": 0,
                         "links_not_read": []},
            "links_not_copied": [], "bases": []}], "receipt": None})
        facts = _FACTS["download"]
        self.assertEqual(facts["tag"], "good")
        self.assertEqual(facts["events"], [("approved", "workshop", "human")])
        self.assertTrue(facts["copied"])
        self.assertIn("1400000002", facts["owners"])

    def test_a_download_whose_package_is_a_link_compresses_nothing_from_the_link(self) -> None:
        r = _RUNS["linked"]
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, _line(
            "Linked (1400000037)", "imported into the pool; scene, nothing to compress; link not read: scene.pkg; "
            "links not copied: extra.tex, scene.pkg") + "\n", ""))
        facts = _FACTS["linked"]
        self.assertEqual((facts["tag"], facts["copied"]), ("good", ["project.json"]))
        self.assertNotIn("1400000037", facts["owners"], "a texture from the link's target reached the cache")

    def test_json_names_the_links_the_text_names_for_compress_and_add(self) -> None:
        r = _RUNS["linked-compress"]
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, _line(
            "Linked Pool (1400000038)", "scene, nothing to compress; link not read: scene.pkg") + "\n", ""))
        r = _RUNS["linked-compress-json"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(json.loads(r.stdout)["wallpapers"], [{
            "id": "1400000038", "title": "Linked Pool", "result": "nothing", "bytes_before": 0, "bytes_after": 0,
            "failed": 0, "disk_bytes": 0, "links_not_read": ["scene.pkg"]}])
        r = _RUNS["linked-json"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(json.loads(r.stdout), {"results": [{
            "id": "1400000039", "title": "Linked Json", "result": "imported",
            "compress": {"result": "nothing", "bytes_before": 0, "bytes_after": 0, "failed": 0, "disk_bytes": 0,
                         "links_not_read": ["scene.pkg"]},
            "links_not_copied": ["scene.pkg"], "bases": []}], "receipt": None})

    def test_a_link_name_with_a_newline_stays_on_its_wallpapers_one_line(self) -> None:
        r = _RUNS["linked-newline"]
        names = "a?not added: SPOOF.pkg, scene.pkg"
        self.assertEqual((r.returncode, r.stdout.splitlines(), r.stderr), (0, [_line(
            "Linked Newline (1400000040)", f"imported into the pool; scene, nothing to compress; links not read: "
            f"{names}; links not copied: {names}")], ""))
        self.assertNotIn("1400000040", _FACTS["linked-newline"]["owners"])

    def test_control_characters_in_link_names_stay_on_the_wallpapers_one_line(self) -> None:
        r = _RUNS["linked-controls"]
        names = "a?b.pkg, c?d.pkg, e?f.pkg, g?h.pkg, i?j.pkg, scene.pkg"
        self.assertEqual((r.returncode, r.stdout.splitlines(), r.stderr), (0, [_line(
            "Linked Controls (1400000041)", f"imported into the pool; scene, nothing to compress; links not read: "
            f"{names}; links not copied: {names}")], ""))

    def test_a_base_imported_for_a_preset_names_the_links_its_copy_left_out(self) -> None:
        r = _RUNS["base-links"]
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, _line(
            "Preset of Base Links (1400000042)", "imported into the pool; textures 0 MB before, 0 MB after; link not "
            "copied: own.tex; its base Base Links (1400000043) was imported and waits for review; link not copied: "
            "extra.tex") + "\n", ""))
        r = _RUNS["base-links-json"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        result = json.loads(r.stdout)["results"][0]
        self.assertEqual((result["links_not_copied"], result["bases"]), (["own.tex"], [{
            "id": "1400000045", "title": "Base Links Json", "state": "waiting", "links_not_copied": ["extra.tex"]}]))

    def test_approve_writes_the_title_the_importer_cleaned(self) -> None:
        self.assertEqual(_RUNS["clean-title"].returncode, 0, _RUNS["clean-title"].stderr)
        self.assertEqual(_FACTS["clean-title"], {("good", "SierraDunes")})

    def test_a_waiting_item_is_approved(self) -> None:
        r = _RUNS["waiting"]
        self.assertEqual((r.returncode, r.stdout), (0, _line(
            "Charlie (1400000003)", "in the pool; textures 0 MB before, 0 MB after") + "\n"))
        facts = _FACTS["waiting"]
        self.assertEqual((facts["tag"], facts["events"]), ("good", [("approved", "workshop", "human")]))
        self.assertIn("1400000003", facts["owners"])

    def test_a_held_item_whose_base_arrived_is_wired_and_approved(self) -> None:
        r = _RUNS["held"]
        self.assertEqual((r.returncode, r.stdout), (0, _line(
            "Delta Preset (1400000004)", "in the pool; textures 0 MB before, 0 MB after; its base Echo Base "
            "(1400000005) was imported and waits for review") + "\n"))
        facts = _FACTS["held"]
        self.assertEqual((facts["tag"], facts["base"], facts["meta"], facts["bg"]),
                         ("good", "review", False, "1400000005"))
        self.assertEqual(facts["events"], [("approved", "workshop", "human")])
        self.assertEqual(facts["owners"] - _FACTS["waiting"]["owners"], {"1400000005"})

    def test_a_preset_download_imports_its_base_on_the_way(self) -> None:
        r = _RUNS["preset"]
        self.assertEqual((r.returncode, r.stdout), (0, _line(
            "Juliet Preset (1400000012)", "imported into the pool; textures 0 MB before, 0 MB after; its base "
            "Kilo Base (1400000013) was imported and waits for review") + "\n"))
        facts = _FACTS["preset"]
        self.assertEqual((facts["tag"], facts["base"], facts["bg"]), ("good", "review", "1400000013"))
        self.assertEqual(facts["events"], [("approved", "workshop", "human")])
        self.assertEqual(facts["owners"] - _FACTS["held"]["owners"], {"1400000013"})

    def test_a_base_already_in_the_pool_is_not_named(self) -> None:
        r = _RUNS["known-base"]
        self.assertEqual((r.returncode, r.stdout), (0, _line(
            "Tango Preset (1400000020)", "imported into the pool; textures 0 MB before, 0 MB after") + "\n"))
        facts = _FACTS["known-base"]
        self.assertEqual((facts["tag"], facts["bg"]), ("good", "1400000001"))
        self.assertEqual(facts["owners"] - _FACTS["preset"]["owners"], {"1400000001"})

    def test_a_base_named_after_its_preset_is_approved_in_its_turn(self) -> None:
        r = _RUNS["later-base"]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            _line("Uniform Preset (1400000021)", "imported into the pool; textures 0 MB before, 0 MB after; its "
                  "base Victor Base (1400000022) was imported and waits for review"),
            _line("Victor Base (1400000022)", "in the pool; already compressed")])
        self.assertEqual(_FACTS["later-base"], {"tags": ("good", "good"), "events": (
            [("approved", "workshop", "human")], [("approved", "workshop", "human")])})

    def test_a_held_item_the_wiring_left_held_is_not_approved(self) -> None:
        from lwe_ui.cli import Context
        from lwe_ui.cli.verbs import library
        _scene(WORKSHOP / "1400000019", "Sierra Base", 19)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(importer, "resolve_missing_deps", return_value=0) as wiring:
            code = library._add(Context(False, out, err, None, False), ["1400000018"])
        wiring.assert_called_once_with()
        self.assertEqual((code, out.getvalue(), err.getvalue()), (1, _line(
            "Romeo Held (1400000018)", "not added: its settings file could not be written") + "\n", ""))
        self.assertEqual((_tag("1400000018"), _events("1400000018")), ("review", []))
        self.assertTrue(meta.get("1400000018").get("depMissing"))

    def test_a_failure_is_reported_on_its_line_and_the_rest_continue(self) -> None:
        r = _RUNS["failure"]
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            _line("Papa (1400000016)", "not added: the copy failed"),
            _line("Quebec (1400000017)", "imported into the pool; textures 0 MB before, 0 MB after")])
        self.assertEqual(_FACTS["failure"], {"tags": (None, "good"), "staged": []})

    def test_an_unreadable_package_is_named_and_skipped_and_the_rest_go_on(self) -> None:
        r = _RUNS["unreadable"]
        self.assertEqual((r.returncode, r.stderr), (1, ""))
        self.assertEqual(r.stdout.splitlines(), [
            _line("Whiskey (1400000031)", "imported into the pool; textures 0 MB before, 0 MB after"),
            _line("X-ray (1400000032)", "not added: its package could not be read"),
            _line("Yankee (1400000033)", "not added: its package could not be read"),
            _line("Zulu (1400000034)", "imported into the pool; textures 0 MB before, 0 MB after")])
        approved = [("approved", "workshop", "human")]
        facts = _FACTS["unreadable"]
        self.assertEqual((facts["tags"], facts["events"], facts["copied"]),
                         (["good", None, "review", "good"], [approved, [], [], approved], False))
        self.assertTrue({"1400000031", "1400000034"} <= facts["owners"])
        r = _RUNS["unreadable-json"]
        self.assertEqual((r.returncode, r.stderr), (1, ""))
        result = json.loads(r.stdout)
        self.assertGreater(result["results"][0]["compress"].pop("disk_bytes"), ARGB_AFTER)
        self.assertEqual(result, {"results": [
            {"id": "1400000035", "title": "Oscar", "result": "imported",
             "compress": {"result": "compressed", "bytes_before": 65536, "bytes_after": ARGB_AFTER, "failed": 0,
                          "links_not_read": []},
             "links_not_copied": [], "bases": []},
            {"id": "1400000036", "title": "Nine Bytes", "result": "failed",
             "reason": "its package could not be read"}], "receipt": None})
        self.assertEqual(_FACTS["unreadable-json"], {"tags": ["good", None], "copied": False})


class UntrashTest(unittest.TestCase):
    def test_untrash_alone_lists_the_trash(self) -> None:
        r = _RUNS["list"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout.splitlines(), ["1  Hotel (1400000010)", "2  India (1400000011)  (files gone)",
                                                 "3  Lima (1400000014)", "4  Mike (1400000015)  (files gone)"])
        self.assertEqual(json.loads(_RUNS["list-json"].stdout), [
            {"n": 1, "id": "1400000010", "title": "Hotel", "files": True},
            {"n": 2, "id": "1400000011", "title": "India", "files": False},
            {"n": 3, "id": "1400000014", "title": "Lima", "files": True},
            {"n": 4, "id": "1400000015", "title": "Mike", "files": False}])
        self.assertTrue(_SAME["list"] and _SAME["list-json"])

    def test_untrash_round_trip(self) -> None:
        r = _RUNS["untrash"]
        self.assertEqual((r.returncode, r.stdout),
                         (0, _line("Hotel (1400000010)", "can be imported again") + "\n"))
        self.assertEqual(_FACTS["untrash"], {"tag": None, "events": [("deleted", "library", "human"),
                                                                     ("bypassed", "workshop", "human")]})
        listed = _RUNS["workshop"].stdout.splitlines()
        self.assertTrue(any(line.endswith("  Hotel (1400000010)") for line in listed), listed)
        r = _RUNS["readd"]
        self.assertEqual((r.returncode, r.stdout), (0, _line("Hotel (1400000010)", "imported into the pool; "
                                                             "textures 0 MB before, 0 MB after") + "\n"))
        self.assertEqual(_FACTS["readd"]["tag"], "good")
        self.assertEqual(_FACTS["readd"]["events"][-1], ("approved", "workshop", "human"))

    def test_untrash_with_the_files_gone(self) -> None:
        r = _RUNS["gone"]
        self.assertEqual((r.returncode, r.stdout), (0, _line(
            "India (1400000011)", "can be imported again (its files are gone; a new download comes back in)")
            + "\n"))
        self.assertEqual(_FACTS["gone"]["tag"], None)
        self.assertEqual(_FACTS["gone"]["events"][-1], ("bypassed", "workshop", "human"))

    def test_untrash_all_in_json(self) -> None:
        r = _RUNS["all"]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"results": [
            {"id": "1400000014", "title": "Lima", "files": True},
            {"id": "1400000015", "title": "Mike", "files": False}], "receipt": None})
        self.assertEqual(_FACTS["all"]["tags"], (None, None))
        self.assertEqual(_FACTS["all"]["events"][1], [("bypassed", "workshop", "human")])
        self.assertEqual((_RUNS["empty"].returncode, _RUNS["empty"].stdout), (0, ""))


class EngineTest(unittest.TestCase):
    def test_no_child_connects_to_the_engine_socket(self) -> None:
        self.assertEqual(_CONNECTIONS, [0])
        self.assertGreater(len(_RUNS), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
