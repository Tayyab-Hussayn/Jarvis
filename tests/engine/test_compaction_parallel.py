"""#251: tool-result clearing without a mid-turn model call; parallel SAFE rounds."""

from __future__ import annotations

import threading
import time

import pytest

from conftest import _agent_with
from kravein import audit, config, llm, registry, safety
from kravein.registry import skill
from kravein.safety import Danger, Verdict

RAN: list[str] = []
THREADS: set[str] = set()


@pytest.fixture(autouse=True)
def skills():
    RAN.clear()
    THREADS.clear()

    def zz_slow(name: str) -> str:
        """Slow read.

        Args:
            name: Name.
        """
        THREADS.add(threading.current_thread().name)
        time.sleep(0.3)
        RAN.append(name)
        return f"read {name} " + "x" * 400

    def zz_page(url: str) -> str:
        """Web page.

        Args:
            url: Url.
        """
        time.sleep(0.1)
        return "outside text"

    def zz_change(name: str, _source: str = "local") -> str:
        """A change.

        Args:
            name: Name.
        """
        safety.enforce(Verdict(danger=Danger.MODERATE), f"change {name}", skill="zz_change",
                       source=_source)
        THREADS.add(threading.current_thread().name)
        RAN.append(f"change:{name}")
        return "changed"

    def zz_secretish(path: str, _source: str = "local") -> str:
        """SAFE by declaration, asks at run time (like a secret read).

        Args:
            path: Path.
        """
        safety.enforce(Verdict(danger=Danger.GUARDED, secret_read=True), f"read '{path}'",
                       skill="zz_secretish", source=_source,
                       targets=safety.describe_targets(f"secret:{path}"))
        RAN.append(f"secret:{path}")
        return "secret"

    skill(name="zz_slow", danger=Danger.SAFE)(zz_slow)
    skill(name="zz_page", danger=Danger.SAFE, category="web")(zz_page)
    skill(name="zz_change", danger=Danger.MODERATE)(zz_change)
    skill(name="zz_secretish", danger=Danger.SAFE)(zz_secretish)
    yield
    for n in ("zz_slow", "zz_page", "zz_change", "zz_secretish"):
        registry._registry.pop(n, None)


def calls(*specs):
    return llm.Reply(tool_calls=[llm.ToolCall(n, a, f"c{i}") for i, (n, a) in enumerate(specs)],
                     provider="stub")


def say(text):
    return llm.Reply(text=text, provider="stub")


# -- parallel ------------------------------------------------------------------------------

def test_an_all_safe_round_runs_in_parallel_in_call_order():
    agent, _ = _agent_with([calls(("zz_slow", {"name": "a"}), ("zz_slow", {"name": "b"}),
                                  ("zz_slow", {"name": "c"})), say("done")])
    started = time.monotonic()
    agent.ask("read three")
    took = time.monotonic() - started
    assert took < 0.75, took                                   # ~0.3 s, not 0.9 s
    results = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert [r.split()[1] for r in results] == ["a", "b", "c"]  # call order kept
    assert all(t.startswith("kravein-tool") for t in THREADS)


def test_a_round_with_anything_above_safe_runs_serially():
    agent, _ = _agent_with([calls(("zz_slow", {"name": "a"}), ("zz_change", {"name": "x"}),
                                  ("zz_slow", {"name": "b"})), say("done")],
                           confirmer=lambda *a: True)
    started = time.monotonic()
    agent.ask("mixed")
    assert time.monotonic() - started >= 0.6
    assert RAN == ["a", "change:x", "b"]
    assert not any(t.startswith("kravein-tool") for t in THREADS)


def test_parallel_untrusted_results_still_taint_the_run():
    agent, _ = _agent_with([calls(("zz_page", {"url": "u"}), ("zz_slow", {"name": "a"})), say("ok")])
    agent.ask("read")
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert tool_msgs[0]["untrusted"] == ["tool:zz_page"] and "untrusted" not in tool_msgs[1]
    assert agent._transcript_taint() == {"tool:zz_page"}


def test_the_loop_guard_counts_parallel_calls():
    agent, _ = _agent_with([calls(("zz_slow", {"name": "a"}), ("zz_slow", {"name": "a"}),
                                  ("zz_slow", {"name": "a"})), say("done")])
    agent.ask("same thing thrice")
    assert RAN == ["a", "a"]
    assert sum(m["content"].startswith("Not run:") for m in agent.messages if m.get("role") == "tool") == 1


def test_a_run_time_card_in_a_parallel_round_reaches_the_owner_and_shares_the_denial():
    cards = []
    agent, _ = _agent_with([calls(("zz_secretish", {"path": "k"}), ("zz_slow", {"name": "a"})),
                            say("done")],
                           confirmer=lambda a, d, g: cards.append(a) or False)
    memory = safety.TurnMemory()
    agent._memory = memory
    agent.ask("read")
    assert cards == ["read 'k'"] and "secret:k" not in RAN       # asked, declined, not run
    assert "secret:k" in memory.targets                           # the denial is in the shared memory


def test_the_same_target_twice_in_one_round_is_asked_once():
    cards = []

    def slow_no(a, d, g):
        cards.append(a)
        time.sleep(0.1)
        return False

    agent, _ = _agent_with([calls(("zz_secretish", {"path": "k"}), ("zz_secretish", {"path": "k"})),
                            say("done")], confirmer=slow_no)
    agent.ask("read twice")
    assert cards == ["read 'k'"] and "secret:k" not in RAN


def test_unattended_parallel_rounds_still_refuse():
    agent, _ = _agent_with([calls(("zz_secretish", {"path": "k"}), ("zz_slow", {"name": "a"})),
                            say("done")])
    agent.confirmer = None
    agent.ask("read")
    msgs = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert "no way to ask" in msgs[0] and "secret:k" not in RAN


def test_parallel_can_be_switched_off(monkeypatch):
    real = config.get
    monkeypatch.setattr(config, "get", lambda k, d=None: False if k == "llm.parallel_safe_tools"
                        else real(k, d))
    agent, _ = _agent_with([calls(("zz_slow", {"name": "a"}), ("zz_slow", {"name": "b"})), say("x")])
    agent.ask("go")
    assert not any(t.startswith("kravein-tool") for t in THREADS)


# -- clearing -----------------------------------------------------------------------------

def budget(monkeypatch, tokens, keep=4):
    real = config.get
    monkeypatch.setattr(config, "get", lambda k, d=None: tokens if k == "llm.history_token_budget"
                        else keep if k == "llm.keep_tool_results" else real(k, d))


def test_old_tool_results_are_cleared_mid_turn_without_a_model_call(monkeypatch):
    budget(monkeypatch, 600, keep=2)
    script = [calls(("zz_slow", {"name": str(i)})) for i in range(6)] + [say("done")]
    agent, stub = _agent_with(list(script))
    summaries = []
    monkeypatch.setattr(agent, "_summarize", lambda dropped: summaries.append(1) or "s")
    agent.ask("read six things")
    tools = [m for m in agent.messages if m.get("role") == "tool"]
    assert summaries == []                                         # no summariser mid-turn
    flags = [bool(m.get("cleared")) for m in tools]
    assert any(flags) and not any(flags[-2:])                      # the newest 2 stay verbatim
    first_kept = flags.index(False)
    assert all(flags[:first_kept]) and not any(flags[first_kept:])  # the oldest go first
    assert tools[0]["content"].startswith("[earlier zz_slow result cleared")
    assert all(m.get("tool_call_id") for m in tools)               # the pairing stays
    user = [m for m in agent.messages if m.get("role") == "user"]
    assert user[0]["content"] == "read six things"                 # non-tool messages verbatim


def test_the_summary_runs_only_at_a_turn_boundary(monkeypatch):
    budget(monkeypatch, 60, keep=0)
    agent, _ = _agent_with([calls(("zz_slow", {"name": "a"})), say("first"),
                            calls(("zz_slow", {"name": "b"})), say("second")])
    when = []
    monkeypatch.setattr(agent, "_summarize", lambda dropped: when.append(len(agent.messages)) or "s")
    agent.ask("one")
    agent.ask("two")
    assert when and all(n > 0 for n in when)
    # Each summary happened before that turn's first model call, never between tool rounds.
    assert len(when) <= 2


def test_cleared_results_keep_pairing_for_every_provider_shape():
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "zz_slow", "arguments": {"name": "a"}, "signature": None}]},
        {"role": "tool", "tool_call_id": "c1", "name": "zz_slow", "cleared": True,
         "content": "[earlier zz_slow result cleared to save space: read a… the full text is in the audit log]"},
    ]
    wire = llm._to_openai_messages(msgs, "")
    assert wire[1]["tool_calls"][0]["id"] == wire[2]["tool_call_id"] == "c1"
    gem = llm._to_gemini_contents(msgs)
    assert len(gem) == 3                                           # call + result (flattened text)
    ant = llm._to_anthropic_messages(msgs)
    assert ant[1]["role"] == "assistant" and ant[2]["role"] == "user"
    # Flattened for Claude (no stored blocks): the result is a text, not an orphan tool_result.
    assert all(b["type"] == "text" for b in ant[2]["content"])
