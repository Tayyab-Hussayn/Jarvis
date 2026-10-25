"""Tests: one declined confirmation ends the destructive part of the turn (#114).

Found on a device: after a declined delete the model tried permanent=True and
then `rm` through run_command until the tool-round cap. Every retry was a fresh
prompt for the owner to refuse; a decline now stops the turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import llm, safety  # noqa: E402
from kravein.safety import Danger  # noqa: E402
from conftest import _agent_with  # noqa: E402

DESTRUCTIVE = safety.Verdict(danger=Danger.DESTRUCTIVE, reasons=["x"])


def _decliner(asked):
    def decline(action, detail, danger):
        asked.append(action)
        return False
    return decline


def _delete_then(victim, retry_calls):
    return [
        llm.Reply(tool_calls=[llm.ToolCall("delete_path", {"path": str(victim)}, "c1")], provider="stub"),
        llm.Reply(tool_calls=retry_calls, provider="stub"),
        llm.Reply(text="never reached", provider="stub"),
    ]


@pytest.mark.parametrize("retry", [
    lambda v: llm.ToolCall("delete_path", {"path": str(v), "permanent": True}, "c2"),
    lambda v: llm.ToolCall("run_command", {"command": f"rm {v}"}, "c2"),
    lambda v: llm.ToolCall("run_command", {"command": f"rm -f {v}"}, "c2"),
])
def test_retry_of_a_declined_path_is_silent_and_ends_the_turn(tmp_path, retry):
    victim = tmp_path / "keep.txt"
    victim.write_text("x")
    asked = []
    agent, _ = _agent_with(_delete_then(victim, [retry(victim)]), confirmer=_decliner(asked))
    turn = agent.ask("delete keep.txt")
    assert len(asked) == 1, asked
    assert victim.exists()
    assert turn.reply == "Okay, I won't do that."
    assert turn.iterations == 2
    assert [m["tool_call_id"] for m in agent.messages if m["role"] == "tool"] == ["c1", "c2"]


def test_rest_of_a_batch_is_not_run_after_a_retry(tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("x")
    retry = [
        llm.ToolCall("delete_path", {"path": str(victim), "permanent": True}, "c2"),
        llm.ToolCall("get_volume", {}, "c3"),
    ]
    agent, _ = _agent_with(_delete_then(victim, retry), confirmer=_decliner([]))
    turn = agent.ask("delete keep.txt")
    tools = [m for m in agent.messages if m["role"] == "tool"]
    assert tools[-1]["tool_call_id"] == "c3"
    assert tools[-1]["content"].startswith("Not run")
    assert "get_volume" not in turn.tool_calls


def test_a_different_target_is_still_asked_and_does_not_end_the_turn(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("x")
    b.write_text("x")
    asked = []
    script = [
        llm.Reply(tool_calls=[llm.ToolCall("delete_path", {"path": str(a)}, "c1")], provider="stub"),
        llm.Reply(tool_calls=[llm.ToolCall("delete_path", {"path": str(b)}, "c2")], provider="stub"),
        llm.Reply(text="done", provider="stub"),
    ]
    agent, _ = _agent_with(script, confirmer=_decliner(asked))
    turn = agent.ask("delete both")
    assert len(asked) == 2
    assert turn.reply == "done"


def test_next_turn_asks_normally_again(tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("x")
    asked = []
    retry = [llm.ToolCall("delete_path", {"path": str(victim), "permanent": True}, "c2")]
    script = _delete_then(victim, retry)[:2] + [
        llm.Reply(tool_calls=[llm.ToolCall("delete_path", {"path": str(victim)}, "c4")], provider="stub"),
        llm.Reply(text="ok", provider="stub"),
    ]
    agent, _ = _agent_with(script, confirmer=_decliner(asked))
    agent.ask("delete keep.txt")
    agent.ask("really delete keep.txt")
    assert len(asked) == 2


def test_safety_flags_a_retried_declined_target_only():
    asked = []
    with safety.confirmer_scope(_decliner(asked)):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(DESTRUCTIVE, "delete '/tmp/a'", targets=["/tmp/a"])
        assert not safety.retried_declined_target()
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(DESTRUCTIVE, "delete '/tmp/b'", targets=["/tmp/b"])
        assert not safety.retried_declined_target()
        with pytest.raises(safety.SafetyError):
            safety.enforce(DESTRUCTIVE, "rm '/tmp/a'", targets=["/tmp/a"])
        assert safety.retried_declined_target()
    with safety.confirmer_scope(_decliner(asked)):
        assert not safety.retried_declined_target()


def test_live_decline_carries_across_tool_calls_of_one_turn(tmp_path):
    from kravein import live

    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("x")
    b.write_text("x")
    asked = []
    session = live.LiveSession(source=None, sink=None, confirmer=_decliner(asked))
    first = session._run_tool("delete_path", {"path": str(a)})
    second = session._run_tool("run_command", {"command": f"rm -f {a}"})
    assert "Cancelled" in first
    assert "Refused" in second
    assert len(asked) == 1, asked
    # A different target in the same model turn is still asked.
    session._run_tool("delete_path", {"path": str(b)})
    assert len(asked) == 2
    session._reset_turn()  # the next model turn asks normally
    session._run_tool("delete_path", {"path": str(a)})
    assert len(asked) == 3
