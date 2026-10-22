"""Tests: unenforced tripwire.

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


# Unenforced-skill tripwire
# ---------------------------------------------------------------------------

def _register_temp_skills(monkeypatch):
    """Register two throwaway DESTRUCTIVE skills; one guards itself, one forgets."""
    registry.load_skills()
    monkeypatch.setattr(registry, "_registry", dict(registry._registry))

    @registry.skill(name="tmp_guarded", danger=Danger.DESTRUCTIVE, category="tmp")
    def tmp_guarded() -> str:
        """Guarded."""
        safety.enforce(
            safety.Verdict(danger=Danger.SAFE), "guarded", skill="tmp_guarded"
        )
        return "done"

    @registry.skill(name="tmp_forgetful", danger=Danger.DESTRUCTIVE, category="tmp")
    def tmp_forgetful() -> str:
        """Forgets to call safety."""
        return "done"


def _unenforced_events(monkeypatch):
    from kravein import audit

    events: list[dict] = []
    real = audit.record
    monkeypatch.setattr(
        audit, "record",
        lambda kind, **kw: (events.append({"kind": kind, **kw}), real(kind, **kw))[1],
    )
    return events


def test_destructive_skill_that_skips_safety_is_flagged(monkeypatch):
    _register_temp_skills(monkeypatch)
    events = _unenforced_events(monkeypatch)

    assert registry.dispatch("tmp_forgetful", {}) == "done"  # behaviour unchanged

    flagged = [e for e in events if e["kind"] == "skill.unenforced"]
    assert [e["skill"] for e in flagged] == ["tmp_forgetful"]


def test_destructive_skill_that_calls_enforce_is_not_flagged(monkeypatch):
    _register_temp_skills(monkeypatch)
    events = _unenforced_events(monkeypatch)

    assert registry.dispatch("tmp_guarded", {}) == "done"

    assert not [e for e in events if e["kind"] == "skill.unenforced"]


# ---------------------------------------------------------------------------
