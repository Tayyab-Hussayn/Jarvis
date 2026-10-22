"""Tests: registry.

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


# Registry
# ---------------------------------------------------------------------------

def test_skills_load():
    skills = registry.all_skills()
    assert len(skills) > 50, "expected the full skill set to register"


def test_core_skills_exist():
    for name in (
        "system_status", "list_files", "delete_path", "run_command",
        "open_app", "list_windows", "set_volume", "kill_process",
        "take_screenshot", "remember", "web_search",
    ):
        assert registry.get_skill(name) is not None, f"missing skill {name}"


def test_every_skill_has_a_description():
    for entry in registry.all_skills().values():
        assert entry.description, f"{entry.name} has no description"
        assert entry.description != entry.name


def test_schema_derivation_marks_required_parameters():
    entry = registry.get_skill("run_command")
    assert "command" in entry.parameters["required"]
    assert "timeout_seconds" not in entry.parameters["required"]
    assert entry.parameters["properties"]["timeout_seconds"]["type"] == "integer"


def test_injected_parameters_are_hidden_from_the_model():
    entry = registry.get_skill("kill_process")
    properties = entry.parameters["properties"]
    assert "_source" not in properties
    assert "_confirm" not in properties


def test_destructive_skills_are_marked():
    for name in ("delete_path", "kill_process", "run_command"):
        assert registry.get_skill(name).danger >= Danger.DESTRUCTIVE


def test_dispatch_rejects_unknown_skills():
    assert "Unknown skill" in registry.dispatch("no_such_skill")


def test_dispatch_reports_missing_required_arguments():
    result = registry.dispatch("run_command", {})
    assert "needs" in result and "command" in result


def test_dispatch_coerces_string_arguments():
    # Models routinely send integers and booleans as strings.
    entry = registry.get_skill("list_processes")
    coerced = registry._coerce("15", entry.parameters["properties"]["limit"])
    assert coerced == 15 and isinstance(coerced, int)
    assert registry._coerce("true", {"type": "boolean"}) is True
    assert registry._coerce("no", {"type": "boolean"}) is False


def test_dispatch_returns_a_string_on_failure():
    # A blocked action must come back as text, not raise into the agent loop.
    result = registry.dispatch("delete_path", {"path": "/etc/passwd"})
    assert isinstance(result, str)
    assert "Refused" in result


# ---------------------------------------------------------------------------
