"""Tests: platform adapters.

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


# Platform adapters (headless-safe)
# ---------------------------------------------------------------------------

def test_environment_detection_reports_linux():
    from kravein.platform import detect

    env = detect.environment()
    assert env.distro
    assert env.session_type in ("wayland", "x11", "tty")
    assert env.package_manager in ("pacman", "apt", "dnf", "zypper", "xbps", "portage", "unknown")


def test_shell_runner_captures_output():
    from kravein.platform import shell

    result = shell.run(["echo", "kravein"])
    assert result.ok
    assert result.stdout.strip() == "kravein"


def test_shell_runner_handles_a_missing_binary():
    from kravein.platform import shell

    result = shell.run(["definitely-not-a-real-binary-xyz"])
    assert not result.ok
    assert result.returncode == 127


def test_shell_runner_times_out():
    from kravein.platform import shell

    result = shell.run(["sleep", "5"], timeout=0.5)
    assert result.timed_out


def test_desktop_entries_are_indexed():
    from kravein.platform import apps

    entries = apps.index()
    assert entries, "no .desktop entries found"
    assert all(e.name and e.exec_cmd for e in entries)


def test_config_resolves_user_directories():
    from kravein import config

    assert config.resolve_user_path("home") == Path.home()
    assert config.resolve_user_path("~/x") == Path.home() / "x"
    assert config.resolve_user_path("/tmp/x") == Path("/tmp/x")


# ---------------------------------------------------------------------------
