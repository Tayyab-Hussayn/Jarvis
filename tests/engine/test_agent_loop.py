"""Tests: agent loop.

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
from conftest import _StubProvider, _agent_with  # noqa: E402,F401


# Agent loop
# ---------------------------------------------------------------------------

def test_agent_returns_a_plain_answer():
    agent, stub = _agent_with([llm.Reply(text="All good.", provider="stub")])
    turn = agent.ask("how are things?")
    assert turn.reply == "All good."
    assert turn.iterations == 1
    assert turn.ok


def test_agent_runs_tools_then_answers():
    script = [
        llm.Reply(tool_calls=[llm.ToolCall("get_volume", {}, "c1")], provider="stub"),
        llm.Reply(text="Volume reported.", provider="stub"),
    ]
    agent, _ = _agent_with(script, confirmer=lambda *_: True)
    turn = agent.ask("what is the volume?")
    assert turn.tool_calls == ["get_volume"]
    assert turn.reply == "Volume reported."
    tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
    assert tool_messages and "Volume" in tool_messages[0]["content"]


def test_agent_surfaces_a_safety_refusal_as_a_tool_result():
    script = [
        llm.Reply(
            tool_calls=[llm.ToolCall("delete_path", {"path": "/etc/passwd"}, "c1")],
            provider="stub",
        ),
        llm.Reply(text="I could not do that.", provider="stub"),
    ]
    agent, _ = _agent_with(script, confirmer=lambda *_: True)
    turn = agent.ask("delete passwd")
    tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
    assert "Refused" in tool_messages[0]["content"]
    assert turn.ok, "a refusal must not abort the whole turn"


def test_agent_receives_tools_and_system_prompt():
    agent, stub = _agent_with([llm.Reply(text="hi", provider="stub")])
    agent.ask("hello")
    assert stub.seen_tools and len(stub.seen_tools) > 50
    assert "Linux system assistant" in stub.seen_system


def test_agent_stops_at_the_iteration_budget():
    looping = llm.Reply(
        tool_calls=[llm.ToolCall("get_volume", {}, "c1")], provider="stub"
    )
    agent, _ = _agent_with([looping], confirmer=lambda *_: True)
    turn = agent.ask("loop forever")
    assert not turn.ok
    assert "Stopped after" in turn.error


def test_agent_reset_clears_history():
    agent, _ = _agent_with([llm.Reply(text="hi", provider="stub")])
    agent.ask("hello")
    assert agent.messages
    agent.reset()
    assert not agent.messages


# ---------------------------------------------------------------------------
