"""#249: the top-level loop degrades instead of failing, and a loop guard."""

from __future__ import annotations

import pytest

from conftest import _agent_with
from kravein import audit, llm, registry, runtime
from kravein.registry import skill
from kravein.safety import Danger

CALLS: list[dict] = []


@pytest.fixture(autouse=True)
def zz_tool():
    CALLS.clear()

    def zz_echo(word: str) -> str:
        """Echo.

        Args:
            word: A word.
        """
        CALLS.append({"word": word})
        if word == "boom":
            raise RuntimeError("failed")
        return f"echo {word}"

    skill(name="zz_echo", danger=Danger.SAFE)(zz_echo)
    yield
    registry._registry.pop("zz_echo", None)


def call(**args):
    return llm.Reply(tool_calls=[llm.ToolCall("zz_echo", args, "c")], provider="stub")


@pytest.fixture
def events(monkeypatch):
    seen = []
    real = audit.record
    monkeypatch.setattr(audit, "record", lambda e, **f: (seen.append((e, f)), real(e, **f)))
    return seen


def test_the_third_identical_call_is_refused_and_audited(events):
    agent, _ = _agent_with([call(word="a"), call(word="a"), call(word="a"),
                            llm.Reply(text="done", provider="stub")])
    turn = agent.ask("go")
    assert len(CALLS) == 2 and turn.reply == "done"
    results = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert results[2].startswith("Not run: you already called zz_echo with these exact arguments 2 times")
    assert any(e == "agent.loop_guard" and f["tool"] == "zz_echo" for e, f in events)


def test_other_arguments_are_not_affected():
    agent, _ = _agent_with([call(word="a"), call(word="a"), call(word="b"), call(word="c"),
                            llm.Reply(text="done", provider="stub")])
    agent.ask("go")
    assert [c["word"] for c in CALLS] == ["a", "a", "b", "c"]


def test_a_failed_call_counts_too():
    agent, _ = _agent_with([call(word="boom"), call(word="boom"), call(word="boom"),
                            llm.Reply(text="done", provider="stub")])
    agent.ask("go")
    assert len(CALLS) == 2


def test_the_count_is_per_request():
    agent, _ = _agent_with([call(word="a"), call(word="a"), llm.Reply(text="one", provider="stub"),
                            call(word="a"), call(word="a"), llm.Reply(text="two", provider="stub")])
    agent.ask("first")
    agent.ask("second")
    assert len(CALLS) == 4


def test_the_top_level_cap_degrades_to_a_partial_run(events, monkeypatch):
    monkeypatch.setattr(llm.client(), "complete", lambda messages, **kw: (
        llm.Reply(text="I checked a, b and c; d is left.", provider="stub") if kw.get("tools") is None
        else call(word=str(len(CALLS)))))
    agent = runtime.new_agent(source="web", confirmer=lambda *a: True, max_rounds=3)
    turn = runtime.ask(agent, "check everything", trigger="chat")
    assert not turn.error and turn.status == "partial"
    assert turn.reply == "I checked a, b and c; d is left." and turn.budget_exhausted
    assert any(e == "agent.budget_exhausted" and f.get("wrap_up") for e, f in events)


def test_a_silent_wrap_up_still_answers_the_owner(monkeypatch):
    monkeypatch.setattr(llm.client(), "complete", lambda messages, **kw: (
        llm.Reply(text="", provider="stub") if kw.get("tools") is None
        else call(word=str(len(CALLS)))))
    agent = runtime.new_agent(source="web", confirmer=lambda *a: True, max_rounds=2)
    turn = runtime.ask(agent, "check", trigger="chat")
    assert turn.status == "partial" and "stopped before finishing" in turn.reply
    assert "zz_echo" in turn.reply and not turn.error
