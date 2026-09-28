"""lwe compress: measured sizes, the report layout, and the texture cache the engine reads.

The formatter fed the fixture's figures reproduces its lines. encode_scene reports the textures it
could encode and the bytes it read and wrote, and its return value keeps its three keys. Every
command runs in a child whose PySide6 import raises and whose environment is built from nothing,
with the stub encoder and a scratch state tree: a 128x128 ARGB8888 and a 128x128 R8 texture give
known byte counts, a truncated texture counts as failed and in neither figure, a second run is
already compressed, a video has nothing to compress and is never encoded, a scene with nothing
eligible and a pool row without files say so, --all covers the pool only, a missing encoder
refuses with no cache written, and a preset's textures are owned by its base.

Run: PYTHONPATH=src python3 tests/test_cli_compress.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory(prefix="lwe-compress-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from _scene_pkg import STUB_ENCODER, _pkg, _tex  # noqa: E402
from lwe_ui import texcomp, version  # noqa: E402
from lwe_ui.library.catalog import Row  # noqa: E402
from lwe_ui.library.compress import Result, result_line, total_line  # noqa: E402
from lwe_ui.storage import paths, settings, tags  # noqa: E402

COMPRESS = (Path(__file__).resolve().parent / "fixtures" / "cli" / "compress.txt").read_text(encoding="utf-8").split("\n")
LIB = _ROOT / "lib"
WORKSHOP = _ROOT / "workshop"
CACHE = _ROOT / "s" / "lwe" / "engine" / "texcache"
BIN = _ROOT / "bin"
ARGB_AFTER = 16384 + 4096 + 1024 + 256 + 64 + 16 + 16 + 16
_RUNS: dict[str, subprocess.CompletedProcess] = {}
_CACHES: dict[str, dict[str, tuple[int, str]]] = {}


def _pixels(seed: int, n: int) -> bytes:
    return bytes((i * 7 + seed) % 256 for i in range(n))


def _scene(folder: Path, title: str, textures: dict[str, bytes] | None, kind: str = "scene") -> None:
    folder.mkdir(parents=True)
    file = "video.mp4" if kind == "video" else "scene.json"
    (folder / "project.json").write_text(json.dumps({"title": title, "type": kind, "file": file}),
                                         encoding="utf-8")
    if kind == "video":
        (folder / file).write_bytes(b"v" * 16)
    elif textures is not None:
        (folder / "scene.pkg").write_bytes(_pkg(textures))


def _good(wid: str, title: str, textures: dict[str, bytes] | None, kind: str = "scene") -> None:
    _scene(LIB / wid, title, textures, kind)
    tags.set_state(wid, title, "good")


def _cache() -> dict[str, tuple[int, str]]:
    """{file name: (size, owner)}; owner is the meta's wallpaper field, or the .bc's meta's."""
    if not CACHE.is_dir():
        return {}
    owners = {p.stem: json.loads(p.read_text())["wallpaper"] for p in CACHE.glob("*.meta")}
    return {p.name: (p.stat().st_size, owners.get(p.stem, "")) for p in CACHE.iterdir()}


def _child(words: list[str], encoder: Path) -> subprocess.CompletedProcess:
    env = {"HOME": str(_ROOT), "XDG_CONFIG_HOME": str(_ROOT / "c"), "XDG_STATE_HOME": str(_ROOT / "s"),
           "XDG_DATA_HOME": str(_ROOT / "d"), "XDG_RUNTIME_DIR": str(_ROOT / "rt"),
           "LWE_SOCKET": str(_ROOT / "rt" / "engine.sock"), "PATH": str(BIN),
           "PYTHONPATH": os.pathsep.join([str(_ROOT / "poison"), str(SRC)]), "LWE_SANDBOX": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "LWE_BC7ENC": str(encoder)}
    return subprocess.run([sys.executable, "-m", "lwe_ui", "--lwe", version.panel_stamp(), str(_ROOT / "cwd"),
                           *words], env=env, cwd=_ROOT / "cwd", capture_output=True, encoding="utf-8",
                          timeout=300)


def setUpModule() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(WORKSHOP)})
    side = 128 * 128
    _good("1100000001", "Alpha", {"materials/argb.tex": _tex(0, 128, 128, _pixels(1, side * 4)),
                                  "materials/r8.tex": _tex(9, 128, 128, _pixels(2, side))})
    _good("1100000002", "Bravo", {"materials/good.tex": _tex(0, 128, 128, _pixels(3, side * 4)),
                                  "materials/cut.tex": _tex(0, 128, 128, _pixels(4, side * 4)[:1000])})
    _good("1100000003", "Charlie", None, "video")
    _good("1100000005", "Delta Base", {"materials/base.tex": _tex(0, 128, 128, _pixels(5, side * 4))})
    _good("1100000004", "Echo Preset", None)
    paths.wp_file("1100000004").write_text("BG=1100000005\n", encoding="utf-8")
    _good("1100000007", "Golf", {"materials/golf.tex": _tex(8, 128, 128, _pixels(6, side * 2))})
    _good("1100000010", "India", {"materials/small.tex": _tex(0, 64, 64, _pixels(8, 64 * 64 * 4))})
    tags.set_state("1100000011", "Juliet", "good")
    _scene(WORKSHOP / "1100000008", "Foxtrot", {"materials/fox.tex": _tex(0, 128, 128, _pixels(7, side * 4))})
    tags.set_state("1100000009", "Hotel Gone", "bad")
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
    _RUNS["no-encoder"] = _child(["compress", "--all"], BIN / "absent")
    _CACHES["no-encoder"] = _cache()
    _RUNS["video"] = _child(["compress", "1100000003"], stub)
    _CACHES["video"] = {"exists": CACHE.exists()}
    _RUNS["first"] = _child(["-j", "compress", "1100000001", "1100000002", "1100000003"], stub)
    _CACHES["first"] = _cache()
    _RUNS["preset"] = _child(["compress", "1100000004"], stub)
    _CACHES["preset"] = _cache()
    _RUNS["again"] = _child(["compress", "1100000001"], stub)
    _RUNS["all"] = _child(["compress", "--all"], stub)
    _CACHES["all"] = _cache()
    _RUNS["trashed"] = _child(["compress", "1100000009"], stub)
    for name, words in (("bare", ["compress"]), ("both", ["compress", "--all", "1"]),
                        ("option", ["compress", "--fast", "1"])):
        _RUNS[name] = _child(words, stub)


class FormatterTest(unittest.TestCase):
    def test_compress_figures_give_the_compress_lines(self) -> None:
        rows = [Row(1, "1275921440", "Aurora Lake", "", "scene", "pool", False),
                Row(2, "1505438974", "Deep Space", "", "scene", "pool", False),
                Row(3, "2105138680", "Rain City", "", "scene", "pool", False),
                Row(4, "2270289901", "Neon Drive", "", "video", "pool", False)]
        results = [Result("compressed", 412 * 10**6, 96 * 10**6, 0, 100 * 10**6),
                   Result("compressed", 1200 * 10**6, 288 * 10**6, 2, 271 * 10**6),
                   Result("already", 0, 0, 0, 0), Result("nothing", 0, 0, 0, 0)]
        self.assertEqual([result_line(r, x) for r, x in zip(rows, results)] + [total_line(results)], COMPRESS[1:6])
        self.assertEqual(result_line(rows[1], Result("compressed", 1200 * 10**6, 288 * 10**6, 0, 1)), COMPRESS[8])

    def test_other_texts(self) -> None:
        row = Row(1, "7", "A title of exactly twenty-four", "", "", "pool", False)
        self.assertEqual(result_line(row, Result("compressed", 10**9, 999_400_000, 1, 1)),
                         "A title of exactly twenty-four (7) textures 1.00 GB before, 999 MB after; 1 texture failed")
        short = Row(2, "8", "Short", "", "", "pool", False)
        self.assertEqual(result_line(short, Result("nothing", 0, 0, 0, 0))[29:], "scene, nothing to compress")
        self.assertEqual(result_line(short, Result("missing", 0, 0, 0, 0))[29:], "files missing, nothing to compress")


class MeasureTest(unittest.TestCase):
    def test_encode_scene_measures_what_it_encoded(self) -> None:
        scene = _ROOT / "measure"
        side = 128 * 128
        _scene(scene, "Measured", {"a.tex": _tex(0, 128, 128, _pixels(11, side * 4)),
                                   "b.tex": _tex(9, 128, 128, _pixels(12, side)),
                                   "cut.tex": _tex(0, 128, 128, _pixels(13, side * 4)[:1000]),
                                   "small.tex": _tex(0, 64, 64, _pixels(14, 64 * 64 * 4))})
        cache = _ROOT / "measure-cache"
        saved = texcomp.CACHE, os.environ.get("LWE_BC7ENC")
        texcomp.CACHE = str(cache)
        os.environ["LWE_BC7ENC"] = str(BIN / "stub_bc7enc")
        try:
            m: dict = {}
            done = texcomp.encode_scene(str(scene), "Measured", measure=m)
            self.assertEqual(done, {"encoded": 2, "failed": 1, "total": 3})
            disk = sum(p.stat().st_size for p in cache.iterdir())
            self.assertEqual(m, {"eligible": 3, "bytes_before": side * 4 + side,
                                 "bytes_after": ARGB_AFTER + 16384, "disk_bytes": disk})
            again: dict = {}
            self.assertEqual(texcomp.encode_scene(str(scene), "Measured", measure=again),
                             {"encoded": 0, "failed": 1, "total": 1})
            self.assertEqual(again, {"eligible": 3, "bytes_before": 0, "bytes_after": 0, "disk_bytes": 0})
        finally:
            texcomp.CACHE = saved[0]
            if saved[1] is None:
                os.environ.pop("LWE_BC7ENC", None)
            else:
                os.environ["LWE_BC7ENC"] = saved[1]


class CompressTest(unittest.TestCase):
    def test_a_missing_encoder_refuses_and_writes_no_cache(self) -> None:
        r = _RUNS["no-encoder"]
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertEqual(r.stderr.splitlines(), [f"lwe: the texture encoder {BIN / 'absent'} is missing; "
                                                 "nothing was compressed"])
        self.assertEqual(_CACHES["no-encoder"], {})

    def test_measured_bytes_failures_and_a_video(self) -> None:
        r = _RUNS["first"]
        self.assertEqual(r.returncode, 0, r.stderr)
        cache = _CACHES["first"]

        def owned(wid: str, suffix: str = "") -> int:
            return sum(size for name, (size, owner) in cache.items() if owner == wid and name.endswith(suffix))

        disk_a, disk_b = owned("1100000001"), owned("1100000002")
        self.assertEqual(owned("1100000001", ".bc"), ARGB_AFTER + 16384)
        self.assertEqual(json.loads(r.stdout), {
            "wallpapers": [
                {"id": "1100000001", "title": "Alpha", "result": "compressed", "bytes_before": 128 * 128 * 5,
                 "bytes_after": ARGB_AFTER + 16384, "failed": 0, "disk_bytes": disk_a},
                {"id": "1100000002", "title": "Bravo", "result": "compressed", "bytes_before": 128 * 128 * 4,
                 "bytes_after": ARGB_AFTER, "failed": 1, "disk_bytes": disk_b},
                {"id": "1100000003", "title": "Charlie", "result": "nothing", "bytes_before": 0,
                 "bytes_after": 0, "failed": 0, "disk_bytes": 0}],
            "total": {"compressed": 2, "named": 3, "bytes_before": 128 * 128 * 9,
                      "bytes_after": 2 * ARGB_AFTER + 16384, "disk_bytes": disk_a + disk_b}})

    def test_a_presets_textures_are_owned_by_its_base(self) -> None:
        r = _RUNS["preset"]
        self.assertEqual((r.returncode, r.stdout), (0, "Echo Preset (1100000004)     textures 0 MB before, 0 MB after\n"))
        new = {name: owner for name, (_size, owner) in _CACHES["preset"].items() if name not in _CACHES["first"]}
        self.assertEqual(len(new), 2)
        self.assertEqual(set(new.values()), {"1100000005"})

    def test_a_video_has_nothing_to_compress_and_is_never_encoded(self) -> None:
        r = _RUNS["video"]
        self.assertEqual((r.returncode, r.stdout), (0, "Charlie (1100000003)         video, nothing to compress\n"))
        self.assertEqual(_CACHES["video"], {"exists": False})

    def test_a_second_run_is_already_compressed(self) -> None:
        r = _RUNS["again"]
        self.assertEqual((r.returncode, r.stdout), (0, "Alpha (1100000001)           already compressed\n"))

    def test_all_covers_the_pool_only(self) -> None:
        r = _RUNS["all"]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "Alpha (1100000001)           already compressed",
            "Bravo (1100000002)           textures 0 MB before, 0 MB after; 1 texture failed",
            "Charlie (1100000003)         video, nothing to compress",
            "Delta Base (1100000005)      already compressed",
            "Echo Preset (1100000004)     already compressed",
            "Golf (1100000007)            textures 0 MB before, 0 MB after",
            "India (1100000010)           scene, nothing to compress",
            "Juliet (1100000011)          files missing, nothing to compress",
            "Compressed 1 of 8 wallpapers. Textures 0 MB before, 0 MB after. The cache adds 0 MB on disk."])
        self.assertNotIn("1100000008", {owner for _size, owner in _CACHES["all"].values()})

    def test_refusals(self) -> None:
        r = _RUNS["trashed"]
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertEqual(r.stderr.splitlines(), ["lwe: Hotel Gone (1100000009) is in the trash; untrash it first"])
        for name in ("bare", "both", "option"):
            with self.subTest(run=name):
                self.assertEqual((_RUNS[name].returncode, _RUNS[name].stdout), (3, ""))
                self.assertEqual(_RUNS[name].stderr.splitlines(), ["lwe: compress takes wallpapers or --all"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
