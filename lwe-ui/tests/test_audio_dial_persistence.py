"""The P0: audio dial persistence.

Three parts, all required, and this file asserts all three plus the sequencing law:

  1. real persisted keys in SETTINGS_SCHEMA, holding the ENGINE-NATIVE value
  2. the engine-env generator emits them (covered by test_daemon_unit.py)
  3. EditorBridge.setAudioDial persists after a successful set-tuning

Before this landed, dragging a dial pushed set-tuning and stored nothing, so a service
restart reverted the engine to whatever survived in a hand-edited env file. The
sequencing law says this lands BEFORE anything calls write_files(), because
the first regenerate against an unaware generator would have deleted the stored calibration.

Run: python3 tests/test_audio_dial_persistence.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = str(_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_BOOT_HOME = tempfile.mkdtemp(prefix="lwe-dial-boot-")
for _k, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                 ("XDG_DATA_HOME", "d")):
    os.environ[_k] = os.path.join(_BOOT_HOME, _sub) if _sub else _BOOT_HOME

from lwe_ui import constants as C  # noqa: E402
from lwe_ui import editor as editor_mod  # noqa: E402
from lwe_ui.storage import settings  # noqa: E402


class SchemaKeysTest(unittest.TestCase):
    """Part 1 - the keys exist, are float, and default to the calibrated numbers."""

    def test_three_keys_exist_with_calibrated_defaults(self) -> None:
        for field, key, calibrated in (("audio_gain", "ENGINE_AUDIO_GAIN", 3.0),
                                       ("classic_k", "ENGINE_CLASSIC_K", 0.7),
                                       ("classic_exp", "ENGINE_CLASSIC_EXP", 2.6)):
            spec = C.SETTINGS_SCHEMA.get(key)
            self.assertIsNotNone(spec, f"{key} missing from SETTINGS_SCHEMA")
            self.assertEqual(spec["type"], "float")
            self.assertEqual(spec["default"], calibrated)
            self.assertEqual(C.AUDIO_DIAL_KEYS[field], key)

    def test_defaults_match_the_editors_calibrated_table(self) -> None:
        """One fact, two doors, one store: the two tables may never drift apart."""
        for dial in editor_mod.AUDIO_DIALS.values():
            key = C.AUDIO_DIAL_KEYS[dial["field"]]
            self.assertEqual(C.SETTINGS_SCHEMA[key]["default"], dial["calibrated"],
                             f"{key} default must equal the editor's calibrated value")

    def test_values_round_trip_as_engine_native_floats(self) -> None:
        home = tempfile.mkdtemp(prefix="lwe-dial-")
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        settings.save({"ENGINE_AUDIO_GAIN": 7.25, "ENGINE_CLASSIC_K": 12.5,
                       "ENGINE_CLASSIC_EXP": 1.75})
        got = settings.load()
        self.assertEqual(got["ENGINE_AUDIO_GAIN"], 7.25)
        self.assertEqual(got["ENGINE_CLASSIC_K"], 12.5)
        self.assertEqual(got["ENGINE_CLASSIC_EXP"], 1.75)


class _StubBackend:
    """Records what the editor bridge persists, without touching a real store."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, object]] = []

    def setSetting(self, key: str, value: object) -> None:
        self.writes.append((key, value))


class SetAudioDialPersistsTest(unittest.TestCase):
    """Part 3 - a successful set-tuning is followed by a store write; a failed one is not."""

    def setUp(self) -> None:
        self._saved = (editor_mod.api_client.available, editor_mod.api_client.set_tuning)
        self.backend = _StubBackend()
        self.bridge = editor_mod.EditorBridge(backend=self.backend)

    def tearDown(self) -> None:
        editor_mod.api_client.available, editor_mod.api_client.set_tuning = self._saved

    def _arm(self, ok: bool) -> list[dict]:
        pushed: list[dict] = []
        editor_mod.api_client.available = lambda: True

        def _set_tuning(**kw):
            pushed.append(dict(kw))
            return {"ok": ok}

        editor_mod.api_client.set_tuning = _set_tuning
        return pushed

    def test_success_persists_the_engine_value(self) -> None:
        pushed = self._arm(ok=True)
        self.assertTrue(self.bridge.setAudioDial("GLOW_RADIUS", 0.5))
        self.assertEqual(len(pushed), 1, "exactly one partial set-tuning")
        engine_value = pushed[0]["classic_exp"]
        self.assertEqual(self.backend.writes, [("ENGINE_CLASSIC_EXP", engine_value)],
                         "the persisted value is the ENGINE-native one, not the 0..1 quality")

    def test_every_dial_persists_to_its_own_key(self) -> None:
        self._arm(ok=True)
        for dial_key, spec in editor_mod.AUDIO_DIALS.items():
            self.backend.writes.clear()
            self.assertTrue(self.bridge.setAudioDial(dial_key, 0.6))
            self.assertEqual(self.backend.writes[0][0], C.AUDIO_DIAL_KEYS[spec["field"]])

    def test_rejected_push_persists_nothing(self) -> None:
        """Verb first, persist on confirmation."""
        self._arm(ok=False)
        self.assertFalse(self.bridge.setAudioDial("RESPONSE_THRESHOLD", 0.6))
        self.assertEqual(self.backend.writes, [], "a rejected verb must persist nothing")

    def test_dead_socket_persists_nothing(self) -> None:
        editor_mod.api_client.available = lambda: False
        self.assertFalse(self.bridge.setAudioDial("RESPONSE_THRESHOLD", 0.6))
        self.assertEqual(self.backend.writes, [])


class SequencingLawTest(unittest.TestCase):
    """write_files() must not be reachable from a path that predates dial persistence.

    The hazard is latent, not theoretical: the moment a regenerate path exists, a
    generator that does not emit the dial lines destroys the calibration on the first
    Advanced-row commit. This test is the standing guard on that ordering.
    """

    def test_generator_emits_dials_wherever_write_files_is_called_from(self) -> None:
        from lwe_ui.engine import daemon_unit

        source = (_ROOT / "src/lwe_ui/engine/daemon_unit.py").read_text(encoding="utf-8")
        self.assertIn("AUDIO_DIAL_ENV", source,
                      "the generator must emit the dial lines before any caller exists")
        content = daemon_unit.build_env_content(outputs=[])
        for env_name in C.AUDIO_DIAL_ENV.values():
            self.assertIn(env_name + "=", content)

    def test_no_caller_of_write_files_predates_the_generator_change(self) -> None:
        callers = []
        for path in (_ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "write_files(" in text and path.name != "daemon_unit.py":
                callers.append(path.name)
        # Any caller is permitted ONLY because the generator above already emits the dials.
        # This assertion is a tripwire on the ordering, not a ban on the call.
        from lwe_ui.engine import daemon_unit
        content = daemon_unit.build_env_content(outputs=[])
        for env_name in C.AUDIO_DIAL_ENV.values():
            self.assertIn(env_name + "=", content,
                          f"write_files() is called from {callers} with a dial-blind generator")


if __name__ == "__main__":
    unittest.main(verbosity=1)
