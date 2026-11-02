"""Tests: per-agent persistent history, token budget, tool-result cap (#132)."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import agent as agent_mod  # noqa: E402
from kravein import audit, config, llm, registry  # noqa: E402
from kravein.agents import history  # noqa: E402
from conftest import _agent_with  # noqa: E402


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)


@pytest.fixture
def events(monkeypatch):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda event, **f: seen.append((event, f)))
    return seen


def setting(monkeypatch, **values):
    real = config.get

    def fake(dotted, default=None):
        return values.get(dotted.replace(".", "__"), real(dotted, default))

    monkeypatch.setattr(config, "get", fake)


def say(text):
    return llm.Reply(text=text, provider="stub")


# -- persistence ------------------------------------------------------------

def test_history_survives_a_new_agent_instance():
    a, _ = _agent_with([say("first")], thread_id="t1")
    a.ask("hello")
    b, stub = _agent_with([say("second")], thread_id="t1")
    assert [m["content"] for m in b.messages] == ["hello", "first"]
    b.ask("again")
    assert [m["content"] for m in b.messages][-2:] == ["again", "second"]
    # The model really saw the earlier turn.
    c, _ = _agent_with([say("x")], thread_id="t1")
    assert len(c.messages) == 4


def test_threads_and_profiles_are_separate():
    a, _ = _agent_with([say("a")], thread_id="t1")
    a.ask("one")
    other_thread, _ = _agent_with([say("b")], thread_id="t2")
    other_profile, _ = _agent_with([say("c")], thread_id="t1", profile="_example")
    assert other_thread.messages == [] and other_profile.messages == []
    other_profile.ask("two")
    again, _ = _agent_with([say("d")], thread_id="t1")
    assert [m["content"] for m in again.messages] == ["one", "a"]


def test_without_thread_id_nothing_is_stored(tmp_path):
    a, _ = _agent_with([say("a")])
    a.ask("hi")
    assert not (tmp_path / "agents.db").exists()


def test_reset_forgets_the_stored_thread():
    a, _ = _agent_with([say("a")], thread_id="t1")
    a.ask("hi")
    a.reset()
    b, _ = _agent_with([say("b")], thread_id="t1")
    assert b.messages == [] and b.summary == ""


def test_summary_persists_with_the_messages():
    history.save("default", "t", [{"role": "user", "content": "q"}], "earlier: X")
    a, stub = _agent_with([say("ok")], thread_id="t")
    assert a.summary == "earlier: X"


def test_cancelled_turn_is_saved_too():
    stop = threading.Event()
    stop.set()
    a, _ = _agent_with([say("x")], thread_id="t1")
    a.ask("hi", stop)
    b, _ = _agent_with([say("x")], thread_id="t1")
    assert b.messages and b.messages[0]["content"] == "hi"


def test_concurrent_writers_do_not_corrupt_or_fail():
    errors: list[Exception] = []

    def writer(n):
        try:
            for i in range(15):
                msgs = [{"role": "user", "content": f"{n}-{i}-{k}"} for k in range(5)]
                history.save("p", f"t{n % 3}", msgs, f"s{n}")   # threads share keys
        except Exception as exc:                                # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    for key in ("t0", "t1", "t2"):
        msgs, summary = history.load("p", key)
        assert len(msgs) == 5                      # one writer's full set, never a mix
        owners = {m["content"].rsplit("-", 2)[0] for m in msgs}
        stamps = {m["content"].rsplit("-", 1)[0] for m in msgs}
        assert len(stamps) == 1 and summary == f"s{owners.pop()}"


def test_concurrent_agents_on_different_threads():
    # `client()` is a shared LLM, so one echoing stub serves every agent.
    errors: list[Exception] = []
    first, stub = _agent_with([say("x")])

    def echo(messages, **kw):
        last = [m for m in messages if m["role"] == "user"][-2]["content"]
        return say("r" + last[1:])

    stub.complete = echo

    def run(n):
        try:
            from kravein.agent import Agent
            Agent(source="test", thread_id=f"c{n}").ask(f"q{n}")
        except Exception as exc:                                # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    for n in range(8):
        msgs, _ = history.load("default", f"c{n}")
        assert [m["content"] for m in msgs] == [f"q{n}", f"r{n}"]


def test_leading_orphan_tool_message_is_dropped_on_load():
    history.save("default", "o", [
        {"role": "tool", "name": "x", "tool_call_id": "1", "content": "stale"},
        {"role": "user", "content": "q"},
    ], "")
    msgs, _ = history.load("default", "o")
    assert [m["role"] for m in msgs] == ["user"]


def test_unreadable_store_does_not_stop_the_agent(tmp_path, events):
    (tmp_path / "agents.db").write_bytes(b"this is not sqlite" * 50)
    a, _ = _agent_with([say("fine")], thread_id="t")
    turn = a.ask("hi")
    assert turn.reply == "fine"
    names = {e for e, _ in events}
    assert "agent.history_load_failed" in names and "agent.history_save_failed" in names


# -- token budget -----------------------------------------------------------

def _turn(i, size=400):
    return [
        {"role": "user", "content": f"q{i} " + "u" * size},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "name": "get_time", "arguments": {}, "signature": None}]},
        {"role": "tool", "name": "get_time", "tool_call_id": f"c{i}", "content": "t" * size},
        {"role": "assistant", "content": f"a{i} " + "r" * size},
    ]


def _fill(agent, turns):
    for i in range(turns):
        agent.messages.extend(_turn(i))


def _assert_paired(messages):
    assert messages[0]["role"] == "user"
    open_ids: set[str] = set()
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            open_ids = {c["id"] for c in m["tool_calls"]}
        elif m["role"] == "tool":
            assert m["tool_call_id"] in open_ids


def test_under_budget_nothing_changes(monkeypatch):
    setting(monkeypatch, llm__history_token_budget=100000)
    a, stub = _agent_with([say("s")])
    _fill(a, 3)
    before = list(a.messages)
    a._trim_history()
    assert a.messages == before and a.summary == "" and stub.calls == 0


def test_over_budget_folds_old_turns_into_a_summary(monkeypatch, events):
    setting(monkeypatch, llm__history_token_budget=1500)
    a, stub = _agent_with([say("SUMMARY of earlier turns")])
    _fill(a, 12)
    a.messages.append({"role": "user", "content": "current question"})
    a._trim_history()
    assert a.summary == "SUMMARY of earlier turns"
    assert stub.calls == 1
    _assert_paired(a.messages)
    assert a.messages[-1]["content"] == "current question"
    assert sum(agent_mod.estimate_tokens(m) for m in a.messages) <= 1500
    # The summary leads the next request.
    sent = a._with_context()
    assert sent[0]["content"].startswith("[summary of the earlier conversation]")
    trimmed = [f for e, f in events if e == "agent.history_trimmed"][0]
    assert trimmed["summarized"] is True


def test_rolling_summary_feeds_the_previous_one(monkeypatch):
    setting(monkeypatch, llm__history_token_budget=1500)
    a, stub = _agent_with([say("new summary")])
    a.summary = "OLD SUMMARY"
    seen = {}
    real = stub.complete

    def spy(messages, **kw):
        seen["prompt"] = messages[0]["content"]
        return real(messages, **kw)

    stub.complete = spy
    _fill(a, 12)
    a.messages.append({"role": "user", "content": "now"})
    a._trim_history()
    assert "OLD SUMMARY" in seen["prompt"] and a.summary == "new summary"


def test_summary_failure_falls_back_to_plain_trim(monkeypatch, events):
    setting(monkeypatch, llm__history_token_budget=1500)
    a, stub = _agent_with([say("unused")])

    def boom(*a, **k):
        raise llm.LLMError("all providers down")

    stub.complete = boom
    _fill(a, 12)
    a.messages.append({"role": "user", "content": "current"})
    a._trim_history()                       # must not raise
    assert a.summary == ""
    _assert_paired(a.messages)
    assert a.messages[-1]["content"] == "current"
    assert sum(agent_mod.estimate_tokens(m) for m in a.messages) <= 1500
    assert [e for e, _ in events if e == "agent.summary_failed"]


@pytest.mark.parametrize("reply", [say(""), llm.Reply(
    tool_calls=[llm.ToolCall("get_time", {}, "z")], provider="stub")])
def test_empty_or_tool_calling_summary_counts_as_failure(monkeypatch, reply):
    setting(monkeypatch, llm__history_token_budget=1500)
    a, _ = _agent_with([reply])
    _fill(a, 12)
    a.messages.append({"role": "user", "content": "now"})
    a._trim_history()
    assert a.summary == ""
    _assert_paired(a.messages)


def test_a_single_huge_turn_is_never_cut(monkeypatch):
    setting(monkeypatch, llm__history_token_budget=500)
    a, stub = _agent_with([say("s")])
    a.messages.extend(_turn(0, size=20000))
    a._trim_history()
    assert len(a.messages) == 4 and stub.calls == 0


def test_message_cap_still_applies_when_given(monkeypatch):
    setting(monkeypatch, llm__history_token_budget=10**9)
    a, _ = _agent_with([say("s")], max_history=8)
    _fill(a, 6)
    a.messages.append({"role": "user", "content": "now"})
    a._trim_history()
    assert len(a.messages) <= 8
    _assert_paired(a.messages)


def test_end_to_end_ask_trims_and_persists(monkeypatch):
    setting(monkeypatch, llm__history_token_budget=1500)
    a, _ = _agent_with([say("SUM"), say("answer")], thread_id="big")
    _fill(a, 12)
    a.ask("final")
    b, _ = _agent_with([say("x")], thread_id="big")
    assert b.summary == "SUM"
    assert b.messages[-1]["content"] == "answer"
    _assert_paired(b.messages)


# -- tool-result truncation -------------------------------------------------

def test_large_tool_result_is_capped_in_history_and_marked(monkeypatch, events):
    setting(monkeypatch, llm__tool_result_max_chars=1000)
    big = "x" * 5000
    monkeypatch.setattr(registry, "dispatch", lambda *a, **k: big)
    script = [
        llm.Reply(tool_calls=[llm.ToolCall("get_time", {}, "c1")], provider="stub"),
        llm.Reply(text="done", provider="stub"),
    ]
    a, _ = _agent_with(script)
    seen_by_ui = []
    a.observer = lambda kind, text, data: seen_by_ui.append((kind, text))
    a.ask("go")
    tool = [m for m in a.messages if m["role"] == "tool"][0]["content"]
    assert tool.startswith("x" * 1000) and len(tool) < 1300
    assert "cut: 4000 of 5000" in tool and "audit log" in tool
    ev = [f for e, f in events if e == "agent.tool_result_truncated"][0]
    assert ev["chars"] == 5000 and ev["kept"] == 1000
    # The live UI still gets the whole result.
    assert [t for k, t in seen_by_ui if k == "tool_result"] == [big]


def test_small_result_is_untouched(monkeypatch):
    setting(monkeypatch, llm__tool_result_max_chars=1000)
    assert agent_mod.Agent._cap_result("t", "short") == "short"


def test_full_result_stays_in_the_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit, "_fd", None)
    full = "y" * 30000
    with audit.action("some_skill", {}, source="test") as ctx:
        ctx["result"] = full
    lines = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    ok = [l for l in lines if l["event"] == "skill.ok"][0]
    assert ok["result"] == full
    # Other fields keep the small cap.
    audit.record("other", note="z" * 30000)
    last = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert len(last["note"]) < 2100
    monkeypatch.setattr(audit, "_fd", None)


# --- #154: Gemini thought_signature must survive the history store -----------

def _sig_message(sig):
    return [
        {"role": "user", "content": "list my workspaces"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "list_workspaces", "arguments": {}, "signature": sig}]},
        {"role": "tool", "name": "list_workspaces", "tool_call_id": "c1", "content": "ok"},
    ]


def test_bytes_signature_round_trips_as_bytes():
    sig = b"\x12`\xff\x00opaque"
    history.save("p", "t", _sig_message(sig), "")
    msgs, _ = history.load("p", "t")
    got = msgs[1]["tool_calls"][0]["signature"]
    assert isinstance(got, bytes) and got == sig


def test_legacy_repr_signature_is_dropped_and_audited(events):
    msgs = _sig_message("b'\\x12`abc'")  # what default=str used to store
    history.save("p", "t", msgs, "")
    loaded, _ = history.load("p", "t")
    assert loaded[1]["tool_calls"][0]["signature"] is None
    assert [e for e, _ in events] == ["agent.history_signature_dropped"]


def test_two_turn_gemini_shaped_agent_replays_bytes_signature(monkeypatch):
    sig = b"\x12`sig"
    first, _ = _agent_with(
        [llm.Reply(provider="stub", text="", tool_calls=[
            llm.ToolCall(name="nope", arguments={}, id="c1", signature=sig)]),
         say("done")],
        thread_id="g1", profile="_example",
    )
    first.ask("list my workspaces")
    second, stub = _agent_with([say("ok")], thread_id="g1", profile="_example")
    second.ask("open kitty")
    replayed = [c for m in second.messages for c in (m.get("tool_calls") or [])]
    assert replayed and all(c["signature"] == sig for c in replayed)
