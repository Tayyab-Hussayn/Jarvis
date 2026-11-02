"""Tests: per-principal web threads, store hardening, alias reset (#132)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import config, llm, registry  # noqa: E402
from kravein.agents import history  # noqa: E402
from conftest import _agent_with  # noqa: E402


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)


def say(text):
    return llm.Reply(text=text, provider="stub")


def test_principals_get_separate_stable_hashed_threads():
    ids = {p: history.principal_thread_id("web", p) for p in ("browser", "device:abc", "device:abd")}
    assert len(set(ids.values())) == 3
    assert ids["browser"] == history.principal_thread_id("web", "browser")      # stable
    assert all("abc" not in v and "browser" not in v for v in ids.values())
    assert history.principal_thread_id("phone", "browser") != ids["browser"]


def test_two_principals_do_not_share_a_history():
    a, _ = _agent_with([say("for browser")], thread_id=history.principal_thread_id("web", "browser"))
    a.ask("browser question")
    b, _ = _agent_with([say("for phone")], thread_id=history.principal_thread_id("web", "device:p1"))
    assert b.messages == []
    b.ask("phone question")
    again, _ = _agent_with([say("x")], thread_id=history.principal_thread_id("web", "browser"))
    assert [m["content"] for m in again.messages] == ["browser question", "for browser"]


def test_web_session_uses_its_principals_thread(monkeypatch):
    import asyncio
    from kravein import events, server

    loop = asyncio.new_event_loop()
    try:
        store = events.SessionStore() if hasattr(events, "SessionStore") else None
        held = server._Held("s1", "device:p1", loop, store)
    finally:
        loop.close()
    assert held.agent.thread_id == history.principal_thread_id("web", "device:p1")
    other_loop = asyncio.new_event_loop()
    try:
        other = server._Held("s2", "browser", other_loop, store)
    finally:
        other_loop.close()
    assert other.agent.thread_id != held.agent.thread_id


def test_dangling_tool_calls_are_closed_on_load():
    history.save("default", "d", [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "get_time", "arguments": {}, "signature": None}]},
    ], "")
    msgs, _ = history.load("default", "d")
    assert msgs[-1]["role"] == "tool" and msgs[-1]["tool_call_id"] == "c1"


def test_history_db_is_owner_only(tmp_path):
    history.save("default", "p", [{"role": "user", "content": "secret"}], "")
    assert (tmp_path / "agents.db").stat().st_mode & 0o777 == 0o600


def test_plain_alias_does_not_inherit_an_old_preset():
    from kravein.registry import skill

    @skill(name="zz_first", category="zz_test", aliases={"zz_shared": {"action": "pause"}})
    def zz_first(action: str = "") -> str:
        """First."""
        return f"first:{action}"

    @skill(name="zz_second", category="zz_test", aliases=["zz_shared"])
    def zz_second(action: str = "") -> str:
        """Second."""
        return f"second:{action}"

    try:
        assert registry.dispatch("zz_shared", {"action": "cancel"}) == "second:cancel"
    finally:
        for n in ("zz_first", "zz_second"):
            registry._registry.pop(n, None)
        registry._aliases.pop("zz_shared", None)
        registry._alias_presets.pop("zz_shared", None)
