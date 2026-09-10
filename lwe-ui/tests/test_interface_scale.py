"""Interface scale, the startup path: the stored percent becomes Qt's scale factor before the
QApplication exists, 100 sets nothing, and a hand-set QT_SCALE_FACTOR wins over the store."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from lwe_ui import constants as C  # noqa: E402
from lwe_ui.app import interface_scale_factor  # noqa: E402


def main() -> None:
    spec = C.SETTINGS_SCHEMA["INTERFACE_SCALE"]
    assert (spec["type"], spec["default"], spec["min"], spec["max"]) == ("int", 100, 75, 150)

    assert interface_scale_factor({}, {}) is None, "an absent key is the default, 100, nothing set"
    assert interface_scale_factor({"INTERFACE_SCALE": 100}, {}) is None
    assert interface_scale_factor({"INTERFACE_SCALE": 125}, {}) == "1.25"
    assert interface_scale_factor({"INTERFACE_SCALE": 150}, {}) == "1.5"
    assert interface_scale_factor({"INTERFACE_SCALE": 75}, {}) == "0.75"
    assert interface_scale_factor({"INTERFACE_SCALE": "110"}, {}) == "1.1", "the store hands strings"
    assert interface_scale_factor({"INTERFACE_SCALE": 300}, {}) == "1.5", "clamped to the schema range"
    assert interface_scale_factor({"INTERFACE_SCALE": "x"}, {}) is None, "garbage sets nothing"
    assert interface_scale_factor({"INTERFACE_SCALE": 150}, {"QT_SCALE_FACTOR": "2"}) is None, \
        "a hand-set factor wins"
    assert interface_scale_factor({"INTERFACE_SCALE": 150}, {"QT_SCALE_FACTOR": ""}) == "1.5", \
        "an empty variable is no factor"
    print("OK interface scale: schema, startup factor, hand-set factor wins")


if __name__ == "__main__":
    main()
