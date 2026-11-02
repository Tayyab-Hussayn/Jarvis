"""Engine-suite fixtures.

The owner's real ``~/.config/kravein/settings.json`` must never decide a test's
outcome (same principle as #88, which isolated tests from a live server). The
file leads with whatever provider/model the owner chose that week, and tests
that assert defaults (``test_gemini_uses_exactly_one_model``, the Zen breaker
tests) failed on that machine only.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

# Engine tests do ``from conftest import _agent_with, ...`` against the shared
# tests/conftest.py (owned by qa). This file has the same module name and is
# first on sys.path for tests/engine, so it would hide those helpers. Load the
# shared module under another name and re-export it rather than edit it.
_spec = importlib.util.spec_from_file_location(
    "_shared_tests_conftest", Path(__file__).resolve().parent.parent / "conftest.py"
)
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)
globals().update({k: v for k, v in vars(_shared).items() if not k.startswith("__")})


@pytest.fixture(autouse=True)
def _defaults_not_owner_settings(monkeypatch):
    """Start every engine test from the code defaults, not the user's file.

    Only the in-process cache is replaced: tests that need a settings file set
    ``_settings_cache = None`` and point at their own, and path-protection
    tests still see the real ``settings_path()`` location.
    """
    from kravein import config

    monkeypatch.setattr(config, "_settings_cache", copy.deepcopy(config.DEFAULT_SETTINGS))
