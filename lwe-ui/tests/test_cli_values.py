"""cli/values.py: the time, duration, switch, number and path grammars and the formatters, and their
parity with the engine's flag grammars through the vectors of Testing/Cases/FlagValues.cpp.

Everything runs in this process; HOME and the XDG folders point at scratch first (_cli_env).

Run: PYTHONPATH=src python3 tests/test_cli_values.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import _cli_env

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-values-"))
HOME = _cli_env.scratch_home(ROOT)

MALFORMED = ("", "abc", "1x", " 1", "nan", "inf")
ARABIC_INDIC_0730 = "".join(chr(0x0660 + int(c)) for c in "0730")
FULLWIDTH_0730 = "".join(chr(0xFF10 + int(c)) for c in "0730")


class ValuesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui.cli import values
        cls.v = values

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(ROOT, True)

    def refused(self, parse, words, message=None) -> None:
        for word in words:
            with self.subTest(word=word):
                with self.assertRaises(self.v.UsageError) as caught:
                    parse(word)
                if message is not None:
                    self.assertEqual(str(caught.exception), message.format(word=word))

    def test_times_take_four_spellings_in_ascii_digits(self) -> None:
        for word, minutes in (("7:30", 450), ("07:30", 450), ("730", 450), ("0730", 450), ("0:00", 0),
                              ("23:59", 1439), (" 7:30 ", 450)):
            with self.subTest(word=word):
                self.assertEqual(self.v.parse_time(word), minutes)
        self.refused(self.v.parse_time, ("24:00", "7:60", "7", "07300", "7.30", ARABIC_INDIC_0730, FULLWIDTH_0730),
                     "takes a time as H:MM, HH:MM, HMM or HHMM, such as 7:30 or 0730; got {word}")

    def test_durations_are_a_whole_number_with_one_lowercase_unit(self) -> None:
        def interval(word):
            return self.v.parse_duration(word, "m", 15, 599940)

        def detectevery(word):
            return self.v.parse_duration(word, "s", 15, 86400)

        self.assertEqual((interval("20"), interval("30s"), interval("1h"), interval("9999m")), (1200, 30, 3600, 599940))
        self.assertEqual((detectevery("20"), detectevery("24h")), (20, 86400))
        self.refused(interval, ("1.5h", "1h30m", "20M", "-5", "", "+5", "14s", "10000m"),
                     "takes a whole number with s, m or h from 15s to 9999m (a bare number is minutes); got {word}")
        self.refused(detectevery, ("1.5m", "25h", "+5"),
                     "takes a whole number with s, m or h from 15s to 24h (a bare number is seconds); got {word}")

    def test_format_duration_uses_the_largest_exact_unit(self) -> None:
        self.assertEqual([self.v.format_duration(s) for s in (900, 90, 3600, 5400, 0)],
                         ["15m", "90s", "1h", "90m", "0s"])
        for seconds in (15, 900, 5400, 86400, 599940):
            with self.subTest(seconds=seconds):
                self.assertEqual(self.v.parse_duration(self.v.format_duration(seconds), "m", 15, 599940), seconds)

    def test_resolve_path_joins_a_relative_path_only_in_the_senders_folder(self) -> None:
        with self.assertRaises(self.v.Refused) as caught:
            self.v.resolve_path("wallpapers", False)
        self.assertEqual(str(caught.exception), "give a full path")
        self.assertEqual(self.v.resolve_path("/srv/wallpapers", False), "/srv/wallpapers")
        self.assertEqual(self.v.resolve_path("~/wallpapers", False), str(HOME / "wallpapers"))
        start, there = os.getcwd(), ROOT / "there"
        there.mkdir()
        os.chdir(there)
        try:
            self.assertEqual(self.v.resolve_path("wallpapers", True), str(there / "wallpapers"))
        finally:
            os.chdir(start)
        self.refused(lambda word: self.v.resolve_path(word, True), ("",), "takes a path; got an empty value")

    def test_plain_numbers_match_the_engine_vectors(self) -> None:
        def plain(word):
            return self.v.parse_number(word, -math.inf, math.inf)

        for word, value in (("1", 1.0), ("+1", 1.0), ("-1", -1.0), ("1.5", 1.5), (".5", 0.5), ("5.", 5.0),
                            ("1e3", 1000.0), ("1E3", 1000.0), ("1e+3", 1000.0), ("1.5e-3", 1.5e-3)):
            with self.subTest(word=word):
                self.assertEqual(plain(word), value)
        for word in ("-0", "-0.0", "1e-999", "-1e-999"):
            with self.subTest(word=word):
                self.assertEqual(plain(word), 0.0)
                self.assertFalse(math.copysign(1.0, plain(word)) < 0)
        self.refused(plain, ("0x10", "0x1p2", "1e", "e3", "--1", "1.2.3", "inf", "nan", "", " 1", "1 ", "1e999",
                             "-1e999"))

    def test_watchdog_durations_match_the_engine_vectors(self) -> None:
        def watchdog(word):
            return self.v.parse_duration(word, "s", 0, 86400)

        for word, seconds in (("0", 0), ("300", 300), ("86400", 86400), ("30s", 30), ("86400s", 86400),
                              ("5m", 300), ("1440m", 86400), ("1h", 3600), ("24h", 86400)):
            with self.subTest(word=word):
                self.assertEqual(watchdog(word), seconds)
        self.refused(watchdog, (*MALFORMED, "-1", "86401s", "1441m", "25h", "86401"))

    def test_factor_milliseconds_color_and_ranges_match_the_engine_vectors(self) -> None:
        for word, value in (("4", 4.0), ("2.5", 2.5), ("0.5", 0.5), ("0", 0.0), ("-1", 0.0)):
            with self.subTest(factor=word):
                self.assertEqual(self.v.parse_factor(word), value)
        self.refused(self.v.parse_factor, (*MALFORMED, "4.5"),
                     "takes a number up to 4, where 0 or below is no cap; got {word}")
        for word, value in (("0", 0.0), ("90", 90.0), ("500", 500.0), ("0ms", 0.0), ("90ms", 90.0), ("2.5ms", 2.5),
                            ("500ms", 500.0)):
            with self.subTest(ms=word):
                self.assertEqual(self.v.parse_ms(word), value)
        self.refused(self.v.parse_ms, (*MALFORMED, "-1", "500.5", "ms"), "takes milliseconds from 0 to 500; got {word}")
        for word, value in (("1 1 1 0", (1.0, 1.0, 1.0, 0.0)), ("0 0 0 -180", (0.0, 0.0, 0.0, -180.0)),
                            ("4 4 4 180", (4.0, 4.0, 4.0, 180.0)), ("1.5  0.5   2 90", (1.5, 0.5, 2.0, 90.0))):
            with self.subTest(color=word):
                self.assertEqual(self.v.parse_color(word), value)
        self.refused(self.v.parse_color, (*MALFORMED, "-0.1 1 1 0", "4.1 1 1 0", "1 -0.1 1 0", "1 4.1 1 0",
                                          "1 1 -0.1 0", "1 1 4.1 0", "1 1 1 -180.5", "1 1 1 180.5", "1 1 1",
                                          "1 1 1 181", "1 1 1 0 0", " 1 1 1 0", "1 1 1 0 "),
                     'takes "brightness contrast saturation hue", each 0 to 4 and hue -180 to 180 degrees; got {word}')
        for lo, hi, words in ((0.0, 10.0, ("0", "2.5", "10")), (0.01, 1000.0, ("0.01", "16", "1000")),
                              (0.5, 6.0, ("0.5", "2", "6")), (0.1, 20.0, ("0.1", "1", "20"))):
            for word in words:
                with self.subTest(lo=lo, word=word):
                    self.assertEqual(self.v.parse_number(word, lo, hi), float(word))
        for lo, hi, words, message in (
                (0.0, 10.0, (*MALFORMED, "-0.1", "10.5", "0x1"), "takes a number from 0 to 10; got {word}"),
                (0.01, 1000.0, ("0", "1000.5", "0x10"), "takes a number from 0.01 to 1000; got {word}"),
                (0.5, 6.0, ("0.4", "6.5"), "takes a number from 0.5 to 6; got {word}"),
                (0.1, 20.0, ("0.05", "20.5"), "takes a number from 0.1 to 20; got {word}")):
            self.refused(lambda word, lo=lo, hi=hi: self.v.parse_number(word, lo, hi), words, message)
        speed = self.v.parse_number("-0", 0.0, 10.0)
        self.assertEqual(speed, 0.0)
        self.assertFalse(math.copysign(1.0, speed) < 0)

    def test_switch_whole_and_step(self) -> None:
        self.assertEqual([self.v.parse_switch(w) for w in ("on", "off", "toggle")], ["on", "off", "toggle"])
        self.refused(self.v.parse_switch, ("yes", "ON", ""), "takes on, off or toggle; got {word}")
        self.refused(lambda word: self.v.parse_switch(word, toggle=False), ("toggle",), "takes on or off; got {word}")
        self.assertEqual((self.v.parse_whole("0", 0, 128), self.v.parse_whole("128", 0, 128)), (0, 128))
        self.refused(lambda word: self.v.parse_whole(word, 0, 128), ("129", "-1", "+5", "1.0", "", " 1",
                                                                     chr(0xFF11) + chr(0xFF12)),
                     "takes a whole number from 0 to 128; got {word}")
        self.assertEqual((self.v.parse_step("+5"), self.v.parse_step("-5")), (5, -5))
        self.refused(self.v.parse_step, ("5", "+", "+5.5", "--5", ""), "takes +N or -N, N a whole number; got {word}")
        self.assertEqual(self.v.parse_number("-0.5", -1.0, 1.0), -0.5)

    def test_times_and_numbers_print_so_they_type_back(self) -> None:
        self.assertEqual([self.v.format_time(m) for m in (0, 450, 1439)], ["00:00", "07:30", "23:59"])
        for minutes in (0, 450, 1439):
            with self.subTest(minutes=minutes):
                self.assertEqual(self.v.parse_time(self.v.format_time(minutes)), minutes)
        self.assertEqual([self.v.format_number(x) for x in (16.0, 0.7, -0.0, 2.5, 3)], ["16", "0.7", "0", "2.5", "3"])
        for x in (0.1, 1 / 3, 1e-05, 123.456, 1e16):
            with self.subTest(x=x):
                self.assertEqual(self.v.parse_number(self.v.format_number(x), -math.inf, math.inf), x)


if __name__ == "__main__":
    unittest.main()
