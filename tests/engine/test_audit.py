"""Tests: audit.

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


# Audit
# ---------------------------------------------------------------------------

def test_audit_redacts_secrets(tmp_path, monkeypatch):
    from kravein import audit, config

    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit, "_fd", None)

    audit.record("test.event", api_key="super-secret", note="visible")

    written = (tmp_path / "audit.jsonl").read_text()
    assert "super-secret" not in written
    assert "***redacted***" in written
    assert "visible" in written


def test_audit_writes_valid_json(tmp_path, monkeypatch):
    from kravein import audit, config

    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit, "_fd", None)

    audit.record("test.one", value=1)
    audit.record("test.two", value=2)

    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        entry = json.loads(line)
        assert entry["event"].startswith("test.")
        assert "ts" in entry and "session" in entry


# ---------------------------------------------------------------------------
