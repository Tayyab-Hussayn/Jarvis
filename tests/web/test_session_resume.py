"""Persistent, resumable sessions (#110).

A dropped socket detaches a session instead of ending it; a socket that
presents the id (and is the same principal) picks it up again: same agent
history, the transcript replayed, a pending confirmation re-sent, a voice
session kept for a grace period.

These use `with TestClient(app)` so one event loop outlives every socket, as
the real server's does; without it each socket gets its own loop and the
expiry timers die with it.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from kravein import events, live, voice, webauth

TOKEN = "test-token-resume"
HEADERS = {"Host": "127.0.0.1:8787"}


@pytest.fixture
def app(monkeypatch):
    from kravein.server import create_app

    webauth.clear_lan_hosts()
    webauth.clear_ui_tokens()
    return create_app(TOKEN)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://127.0.0.1:8787") as shared:
        yield shared


def _connect(client, resume="", token=TOKEN):
    query = f"token={token}" + (f"&resume={resume}" if resume else "")
    return client.websocket_connect(f"/ws?{query}", headers=HEADERS)


def _frame(ws):
    return json.loads(ws.receive_text())


def _until(ws, want, limit=60):
    seen = []
    for _ in range(limit):
        frame = _frame(ws)
        seen.append(frame)
        if want(frame):
            return seen
    raise AssertionError(f"never saw the frame; got {seen[-5:]}")


def _t(name):
    return lambda frame: frame["t"] == name


def _stub_llm(monkeypatch, script):
    from kravein import llm as _llm

    calls = {"n": 0, "seen": []}

    class Stub:
        name = "cline"

        def available(self):
            return True

        def complete(self, messages, **kwargs):
            calls["seen"].append(json.dumps(messages, default=str))
            reply = script[min(calls["n"], len(script) - 1)]
            calls["n"] += 1
            return reply

    facade = _llm.LLM()
    facade.cline = Stub()
    facade.gemini = Stub()
    facade.gemini.name = "gemini"
    facade.openrouter = Stub()
    facade.openrouter.name = "openrouter"
    monkeypatch.setattr(_llm, "_client", facade)
    monkeypatch.setattr(_llm, "client", lambda: facade)
    return calls


def _answer(text):
    from kravein.llm import Reply

    reply = Reply(provider="cline", model="stub")
    reply.text = text
    return reply


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_hello_carries_a_fresh_unguessable_session_id(client):
    with _connect(client) as one:
        first = _frame(one)
    with _connect(client) as two:
        second = _frame(two)
    assert first["resumed"] is False and second["resumed"] is False
    assert first["session_id"] != second["session_id"]
    assert len(first["session_id"]) >= 32
    assert first["session_ttl_minutes"] == 240


def test_resume_replays_the_chat_and_the_agent_context_continues(client, monkeypatch):
    calls = _stub_llm(monkeypatch, [_answer("first answer"), _answer("second answer")])
    with _connect(client) as ws:
        sid = _frame(ws)["session_id"]
        ws.send_text(json.dumps({"t": "ask", "id": "t1", "text": "remember pineapple"}))
        _until(ws, _t("turn_end"))

    with _connect(client, sid) as ws:
        hello = _frame(ws)
        replay = _frame(ws)
        assert hello["resumed"] is True and hello["session_id"] == sid
        assert replay["t"] == "replay"
        kinds = [(f["t"], f.get("text")) for f in replay["frames"]]
        assert ("user_turn", "remember pineapple") in kinds
        assert ("answer", "first answer") in kinds
        assert replay["busy"] is False and replay["voice"]["active"] is False

        ws.send_text(json.dumps({"t": "ask", "id": "t2", "text": "what did I say?"}))
        _until(ws, _t("turn_end"))
    # The second model call saw the first turn: the Agent object survived.
    assert "remember pineapple" in calls["seen"][-1]


def test_an_unknown_id_is_a_clean_new_session_not_an_error(client):
    with _connect(client, "no-such-session") as ws:
        hello = _frame(ws)
    assert hello["t"] == "hello" and hello["resumed"] is False
    assert hello["session_id"] != "no-such-session"


def test_another_principal_cannot_resume_a_session(client):
    with _connect(client) as ws:
        sid = _frame(ws)["session_id"]

    ui_token, _, _ = webauth.issue_ui_token("phone-1")
    with _connect(client, sid, token=ui_token) as ws:
        hello = _frame(ws)
    assert hello["resumed"] is False and hello["session_id"] != sid

    # ...and the owner can still resume their own, untouched by the attempt.
    with _connect(client, sid) as ws:
        assert _frame(ws)["resumed"] is True


def test_a_phone_resumes_its_own_session_but_not_another_phones(client):
    one, _, _ = webauth.issue_ui_token("phone-1")
    other, _, _ = webauth.issue_ui_token("phone-2")
    again, _, _ = webauth.issue_ui_token("phone-1")  # a refreshed token
    with _connect(client, token=one) as ws:
        sid = _frame(ws)["session_id"]
    with _connect(client, sid, token=other) as ws:
        assert _frame(ws)["resumed"] is False
    with _connect(client, sid, token=again) as ws:
        assert _frame(ws)["resumed"] is True


def test_a_session_expires_after_the_ttl(client, monkeypatch):
    from kravein import server

    monkeypatch.setattr(server, "_session_ttl", lambda: 0.2)
    with _connect(client) as ws:
        sid = _frame(ws)["session_id"]
    assert _wait(lambda: len(client.app.state.sessions) == 0)
    with _connect(client, sid) as ws:
        hello = _frame(ws)
    assert hello["resumed"] is False


def test_a_pending_confirmation_is_sent_again_on_resume(client, monkeypatch, tmp_path):
    from kravein.llm import Reply, ToolCall

    doomed = tmp_path / "doomed.txt"
    doomed.write_text("keep me")
    wants = Reply(provider="cline", model="stub")
    wants.tool_calls = [ToolCall(
        name="delete_path",
        arguments={"path": str(doomed), "permanent": True}, id="tc1",
    )]
    _stub_llm(monkeypatch, [wants, _answer("Not deleted.")])

    with _connect(client) as ws:
        sid = _frame(ws)["session_id"]
        ws.send_text(json.dumps({"t": "ask", "id": "t1", "text": "delete it"}))
        asked = _until(ws, _t("confirm_request"))[-1]

    with _connect(client, sid) as ws:
        assert _frame(ws)["resumed"] is True
        replay = _frame(ws)
        again = _frame(ws)
        assert replay["busy"] is True
        assert again["t"] == "confirm_request"
        assert again["confirm_id"] == asked["confirm_id"]
        assert 0 < again["timeout"] <= 120
        ws.send_text(json.dumps({
            "t": "confirm_reply", "confirm_id": asked["confirm_id"], "approve": False,
        }))
        done = _until(ws, _t("turn_end"))
    assert doomed.exists(), "a denial after a resume must still deny"
    assert any(f["t"] == "confirm_resolved" and f["outcome"] == "denied" for f in done)


def test_an_expired_session_denies_its_pending_confirmation(client, monkeypatch, tmp_path):
    from kravein import server
    from kravein.llm import Reply, ToolCall

    doomed = tmp_path / "doomed.txt"
    doomed.write_text("keep me")
    wants = Reply(provider="cline", model="stub")
    wants.tool_calls = [ToolCall(
        name="delete_path",
        arguments={"path": str(doomed), "permanent": True}, id="tc1",
    )]
    _stub_llm(monkeypatch, [wants, _answer("ok")])
    monkeypatch.setattr(server, "_session_ttl", lambda: 0.3)

    with _connect(client) as ws:
        _frame(ws)
        ws.send_text(json.dumps({"t": "ask", "id": "t1", "text": "delete it"}))
        _until(ws, _t("confirm_request"))
    assert _wait(lambda: not events.open_confirms())
    assert doomed.exists()


class FakeVoice:
    """A voice session that runs until stopped."""

    instances: list["FakeVoice"] = []

    def __init__(self, observer=None, confirmer=None, **_):
        self.stopped = threading.Event()
        FakeVoice.instances.append(self)

    def stop(self):
        self.stopped.set()

    def run(self):
        self.stopped.wait(10)
        return 0


@pytest.fixture
def voice_stub(monkeypatch):
    FakeVoice.instances = []
    monkeypatch.setattr(voice, "live_available", lambda: (True, ""))
    monkeypatch.setattr(voice, "readiness", lambda: (False, ["no stt"]))
    monkeypatch.setattr(live, "LiveSession", FakeVoice)


def _start_voice(ws):
    ws.send_text(json.dumps({"t": "voice_start", "mode": "live", "audio": "laptop"}))
    _until(ws, lambda f: f["t"] == "voice_state" and f["phase"] == "started")


def test_voice_survives_a_short_drop_and_is_reported_on_resume(client, voice_stub, monkeypatch):
    from kravein import server

    monkeypatch.setattr(server, "_voice_grace", lambda: 5.0)
    with _connect(client) as ws:
        sid = _frame(ws)["session_id"]
        _start_voice(ws)
    time.sleep(0.2)
    assert not FakeVoice.instances[0].stopped.is_set()

    with _connect(client, sid) as ws:
        assert _frame(ws)["resumed"] is True
        replay = _frame(ws)
        assert replay["voice"] == {"active": True, "mode": "live", "audio": "laptop"}
        ws.send_text(json.dumps({"t": "voice_stop"}))
        _until(ws, lambda f: f["t"] == "voice_state" and f["phase"] == "stopped")
    assert FakeVoice.instances[0].stopped.is_set()


def test_voice_is_stopped_when_the_grace_runs_out(client, voice_stub, monkeypatch):
    from kravein import server

    monkeypatch.setattr(server, "_voice_grace", lambda: 0.2)
    with _connect(client) as ws:
        sid = _frame(ws)["session_id"]
        _start_voice(ws)
    assert FakeVoice.instances[0].stopped.wait(3)
    # The session itself is still resumable; only the microphone went.
    with _connect(client, sid) as ws:
        assert _frame(ws)["resumed"] is True
        assert _frame(ws)["voice"]["active"] is False


def test_a_second_socket_takes_the_session_over(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    _stub_llm(monkeypatch, [_answer("hi")])
    with _connect(client) as first:
        sid = _frame(first)["session_id"]
        with _connect(client, sid) as second:
            assert _frame(second)["resumed"] is True
            _frame(second)  # replay
            # The old socket is closed, not left to compete for the queue.
            with pytest.raises(WebSocketDisconnect) as gone:
                first.receive_text()
            assert gone.value.code == 4001
            second.send_text(json.dumps({"t": "ping"}))
            assert _until(second, _t("pong"))


def test_the_oldest_detached_session_is_evicted_past_the_cap(client, monkeypatch):
    monkeypatch.setattr(events, "MAX_HELD_SESSIONS", 2)
    ids = []
    for _ in range(3):
        with _connect(client) as ws:
            ids.append(_frame(ws)["session_id"])
    assert len(client.app.state.sessions) == 2
    with _connect(client, ids[0]) as ws:
        assert _frame(ws)["resumed"] is False
    with _connect(client, ids[2]) as ws:
        assert _frame(ws)["resumed"] is True


def test_reset_clears_what_a_resume_would_replay(client, monkeypatch):
    _stub_llm(monkeypatch, [_answer("hello")])
    with _connect(client) as ws:
        sid = _frame(ws)["session_id"]
        ws.send_text(json.dumps({"t": "ask", "id": "t1", "text": "hi"}))
        _until(ws, _t("turn_end"))
        ws.send_text(json.dumps({"t": "reset"}))
        _until(ws, _t("reset_ok"))
    with _connect(client, sid) as ws:
        _frame(ws)
        assert _frame(ws)["frames"] == []


def test_panel_runs_are_not_part_of_the_replayed_chat(client):
    with _connect(client) as ws:
        sid = _frame(ws)["session_id"]
        ws.send_text(json.dumps({"t": "run", "id": "p1", "skill": "get_time", "args": {}}))
        _until(ws, _t("turn_end"))
    with _connect(client, sid) as ws:
        _frame(ws)
        assert all(not str(f.get("id", "")).startswith("p") for f in _frame(ws)["frames"])
