"""Tests: denial memory.

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


# Denial memory beyond file paths
# ---------------------------------------------------------------------------
#
# Reported from a real session. The user asked to "close workspace 2", declined
# the confirmation, and was then asked twice more — once about a window on a
# different workspace entirely, then about the window they had just refused:
#
#   close_window(force=False)          -> CONFIRM 'foot'          -> n
#   close_window(force=True)           -> CONFIRM 'google-chrome' -> n
#   close_window(query='foot', True)   -> CONFIRM 'foot'          -> ...
#
# Cause: the denial memory only ever extracted file paths and pids from the
# action description, so "close window 'foot'" produced *no keys at all* and
# nothing was remembered.


def test_target_keys_cover_named_targets_not_only_paths():
    keys = safety._target_keys("close window 'foot'")
    assert "foot" in keys, f"named targets produce no denial key: {keys}"
    assert "nginx" in safety._target_keys("stop system service 'nginx'")


def test_declining_a_window_close_blocks_the_forced_retry():
    """Same window, more force — the escalation must not be re-asked."""
    asked = []

    def deny(action, detail, danger):
        asked.append(action)
        return False

    verdict = safety.Verdict(danger=Danger.DESTRUCTIVE, reasons=["closes it"])
    with safety.confirmer_scope(deny):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(
                verdict, "close window 'foot'", skill="close_window",
                targets=safety.describe_targets("window:0xAAA", "app:foot"),
            )
        with pytest.raises(safety.SafetyError) as caught:
            safety.enforce(
                verdict, "kill window 'foot'", skill="close_window",
                targets=safety.describe_targets("window:0xAAA", "app:foot"),
            )
    assert "already declined" in str(caught.value)
    assert len(asked) == 1, f"asked {len(asked)} times, not once"


def test_declining_an_implicit_close_blocks_the_next_guess():
    """The bug's worst symptom: a retry landing on a different window.

    `close_window()` with no query acts on whatever is focused, and focus can
    move between calls — which is how declining a `foot` window produced a
    prompt about an unrelated Chrome window on another workspace. Declining the
    guess has to stop the guessing.
    """
    asked = []

    def deny(action, detail, danger):
        asked.append(action)
        return False

    verdict = safety.Verdict(danger=Danger.DESTRUCTIVE, reasons=["closes it"])
    with safety.confirmer_scope(deny):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(
                verdict, "close window 'foot'", skill="close_window",
                targets=safety.describe_targets(
                    "window:0xAAA", "app:foot", "close_window:implicit"),
            )
        # Focus has moved; the skill now resolves a completely different
        # window. It is still the same rejected guess.
        with pytest.raises(safety.SafetyError):
            safety.enforce(
                verdict, "kill window 'google-chrome'", skill="close_window",
                targets=safety.describe_targets(
                    "window:0xBBB", "app:google-chrome", "close_window:implicit"),
            )
    assert len(asked) == 1, f"asked about a second window: {asked}"


def test_an_explicitly_named_different_window_is_still_allowed():
    """"Close A, then close B" must keep working when B is named."""
    asked = []

    def confirmer(action, detail, danger):
        asked.append(action)
        return "foot" not in action

    verdict = safety.Verdict(danger=Danger.DESTRUCTIVE, reasons=["closes it"])
    with safety.confirmer_scope(confirmer):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(
                verdict, "close window 'foot'", skill="close_window",
                targets=safety.describe_targets("window:0xAAA", "app:foot"),
            )
        # Explicitly named, so no implicit marker — a genuinely new request.
        safety.enforce(
            verdict, "close window 'kitty'", skill="close_window",
            targets=safety.describe_targets("window:0xCCC", "app:kitty"),
        )
    assert len(asked) == 2


def test_declared_targets_win_over_reading_the_sentence():
    """Parsing prose was always the fallback, never the design."""
    def deny(action, detail, danger):
        return False

    verdict = safety.Verdict(danger=Danger.DESTRUCTIVE, reasons=["stops it"])
    with safety.confirmer_scope(deny):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(verdict, "stop the thing", skill="control_service",
                           targets=safety.describe_targets("service:nginx"))
        # Worded completely differently; same declared target.
        with pytest.raises(safety.SafetyError):
            safety.enforce(verdict, "disable it instead", skill="control_service",
                           targets=safety.describe_targets("service:nginx"))


# ---------------------------------------------------------------------------
