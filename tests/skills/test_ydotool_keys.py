"""Tests: ydotool keys.

Split out of the legacy tests/test_linux_port.py with no change to any test body.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import llm, registry, safety  # noqa: E402,F401
from kravein.safety import Danger  # noqa: E402,F401


# ydotool key codes
# ---------------------------------------------------------------------------
#
# The keymap shipped symbolic names ("ENTER"). ydotool silently ignores those
# and exits 0, so every press_keys call and every hotkey did nothing while
# reporting success. Only numeric Linux input event codes are honoured.
# Measured: `ydotool key 28:1 28:0` delivers Enter; `ENTER:1` and `KEY_ENTER:1`
# do not.


def test_ydotool_key_codes_are_numeric():
    from kravein.platform import inputs

    for name in ("enter", "return", "ctrl", "a", "f1", "tab", "escape"):
        code = inputs._map_key(name, "ydotool")
        assert code.isdigit(), f"{name!r} maps to {code!r}, which ydotool ignores"


@pytest.mark.parametrize("name,code", [
    ("enter", "28"), ("return", "28"), ("esc", "1"), ("tab", "15"),
    ("space", "57"), ("backspace", "14"), ("ctrl", "29"), ("shift", "42"),
    ("alt", "56"), ("super", "125"), ("up", "103"), ("down", "108"),
    ("a", "30"), ("z", "44"), ("q", "16"), ("1", "2"), ("0", "11"),
    ("f1", "59"), ("f10", "68"), ("f11", "87"), ("f12", "88"),
])
def test_known_key_codes(name, code):
    from kravein.platform import inputs

    assert inputs._map_key(name, "ydotool") == code


def test_every_letter_and_digit_maps():
    from kravein.platform import inputs

    for char in "abcdefghijklmnopqrstuvwxyz0123456789":
        assert inputs._map_key(char, "ydotool").isdigit()


# ---------------------------------------------------------------------------
