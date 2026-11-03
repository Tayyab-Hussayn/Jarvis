"""#212 + #211 end to end: a real Agent as `_context` through registry.dispatch.

The unit tests in test_file_tools.py use a stand-in context; this proves the
real `Agent.tool_state`, the dispatcher's `_context` injection and the real
`safety.check_read` work together, with no confirmer attached.
"""

from __future__ import annotations

import os

import pytest

from kravein import registry
from kravein.agent import Agent
from kravein.skills import files  # noqa: F401  (registers the skills)


@pytest.fixture
def agent():
    return Agent(source="test")


def call(agent, name, **params):
    return registry.dispatch(name, params, source="test", context=agent)


def test_edit_before_read_is_refused(tmp_path, agent):
    f = tmp_path / "a.txt"
    f.write_text("alpha\n")
    out = call(agent, "edit_file", path=str(f), old_text="alpha", new_text="beta")
    assert "Read" in out and "first" in out
    assert f.read_text() == "alpha\n"


def test_read_then_edit_works_and_second_edit_needs_no_reread(tmp_path, agent):
    f = tmp_path / "a.txt"
    # Long enough that a one-word edit stays far below the 50% rewrite escalation.
    f.write_text("alpha\nbeta\n" + "filler line\n" * 20)
    assert "1\talpha" in call(agent, "read_file", path=str(f))
    # No confirmer is attached, so a MODERATE edit must not need one.
    out = call(agent, "edit_file", path=str(f), old_text="alpha", new_text="A")
    assert out.startswith("Edited") and "+A" in out
    assert call(agent, "edit_file", path=str(f), old_text="beta", new_text="B").startswith("Edited")
    assert f.read_text().startswith("A\nB\n")


def test_stale_read_is_refused(tmp_path, agent):
    f = tmp_path / "a.txt"
    f.write_text("alpha\n")
    call(agent, "read_file", path=str(f))
    f.write_text("alpha, changed by the owner\n")
    os.utime(f, ns=(1, 1))
    out = call(agent, "edit_file", path=str(f), old_text="alpha", new_text="beta")
    assert "changed since you read it" in out
    assert f.read_text() == "alpha, changed by the owner\n"


def test_state_is_per_agent(tmp_path, agent):
    f = tmp_path / "a.txt"
    f.write_text("alpha\n")
    call(agent, "read_file", path=str(f))
    other = Agent(source="test")  # e.g. a delegated child: must not inherit the read
    out = call(other, "edit_file", path=str(f), old_text="alpha", new_text="beta")
    assert "Read" in out and f.read_text() == "alpha\n"


def test_secret_read_is_denied_without_a_confirmer(tmp_path, agent):
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=abc123\n")
    out = call(agent, "read_file", path=str(secret))
    assert "abc123" not in out
    assert agent.tool_state.get("files_read", {}) == {}  # a denied read is not a read
    # ...and so the file can neither be edited nor overwritten from it.
    out = call(agent, "edit_file", path=str(secret), old_text="abc123", new_text="x")
    assert "Read" in out and secret.read_text() == "TOKEN=abc123\n"


def test_search_does_not_leak_a_secret_read(tmp_path, agent):
    (tmp_path / ".env").write_text("TOKEN=abc123\n")
    (tmp_path / "creds.conf").write_text("TOKEN=abc123 in a plain file\n")
    out = call(agent, "search_text", query="abc123", directory=str(tmp_path))
    assert ".env" not in out
    assert "creds.conf" in out  # an ordinary file is still found
