"""Tests for the web transport: auth, the confirmation bridge, and redaction.

The confirmation tests matter most. ``events.Session.confirm`` blocks a worker
thread while a browser is asked whether to run something destructive, and every
way that can go wrong — timeout, dropped connection, unknown id — has to
resolve to *deny*. A bug here means a web page silently approving
``delete_path``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brahma import events, webauth  # noqa: E402
from brahma.safety import Danger  # noqa: E402


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    """Point the token file at a temp dir so tests never touch the real one."""
    monkeypatch.setattr(webauth.config, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(webauth, "TOKEN_FILE", tmp_path / "web-token")
    monkeypatch.setattr(webauth.config, "ensure_dirs", lambda: None)
    return tmp_path


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def test_issue_token_is_persisted_and_reused(runtime_dir):
    first = webauth.issue_token()
    assert first
    assert webauth.issue_token() == first, "a restart must not orphan open tabs"


def test_token_file_is_owner_only(runtime_dir):
    webauth.issue_token()
    mode = webauth.token_path().stat().st_mode & 0o777
    assert mode == 0o600


def test_force_rotates_the_token(runtime_dir):
    first = webauth.issue_token()
    assert webauth.issue_token(force=True) != first


def test_verify_token_rejects_wrong_and_empty(runtime_dir):
    token = webauth.issue_token()
    assert webauth.verify_token(token, token)
    assert not webauth.verify_token("wrong", token)
    assert not webauth.verify_token("", token)
    assert not webauth.verify_token(None, token)
    assert not webauth.verify_token(token, "")


def test_token_from_request_accepts_bearer_and_query():
    assert webauth.token_from_request("Bearer abc") == "abc"
    assert webauth.token_from_request("bearer abc") == "abc"
    assert webauth.token_from_request(None, "abc") == "abc"
    assert webauth.token_from_request(None, None) is None


# ---------------------------------------------------------------------------
# Origin and Host — the DNS-rebinding defence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "127.0.0.1:8787", "localhost:8787", "127.0.0.1", "[::1]:8787",
])
def test_loopback_hosts_allowed(host):
    assert webauth.host_allowed(host)


@pytest.mark.parametrize("host", [
    "evil.example.com:8787",     # a rebound name still carries its own Host
    "brahma.attacker.test",
    "192.168.1.10:8787",
])
def test_non_loopback_hosts_rejected(host):
    assert not webauth.host_allowed(host)


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:8787", "http://localhost:8787", "http://localhost:3000",
])
def test_loopback_origins_allowed(origin):
    assert webauth.origin_allowed(origin)


@pytest.mark.parametrize("origin", [
    "https://evil.example.com", "http://attacker.test:8787", "null",
])
def test_foreign_origins_rejected(origin):
    assert not webauth.origin_allowed(origin)


def test_missing_origin_is_allowed():
    # A plain page load is not a cross-origin fetch and sends no Origin.
    assert webauth.origin_allowed(None)
    assert webauth.origin_allowed("")


# ---------------------------------------------------------------------------
# The confirmation bridge
# ---------------------------------------------------------------------------

def _session(loop, timeout=5.0):
    return events.Session(loop, confirm_timeout=timeout)


@pytest.mark.asyncio
async def test_confirm_round_trip_approves():
    session = _session(asyncio.get_running_loop())
    result: dict[str, bool] = {}

    def worker():
        result["approved"] = session.confirm("Delete file", "/tmp/x", Danger.DESTRUCTIVE)

    thread = threading.Thread(target=worker)
    thread.start()

    frame = await asyncio.wait_for(session.next_frame(), timeout=2)
    assert frame["t"] == "confirm_request"
    assert frame["danger"] == "DESTRUCTIVE"
    assert frame["action"] == "Delete file"

    assert session.resolve_confirm(frame["confirm_id"], True)
    thread.join(timeout=2)
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_confirm_round_trip_denies():
    session = _session(asyncio.get_running_loop())
    result: dict[str, bool] = {}

    def worker():
        result["approved"] = session.confirm("Kill process", "pid 42", Danger.DESTRUCTIVE)

    thread = threading.Thread(target=worker)
    thread.start()
    frame = await asyncio.wait_for(session.next_frame(), timeout=2)
    session.resolve_confirm(frame["confirm_id"], False)
    thread.join(timeout=2)
    assert result["approved"] is False


@pytest.mark.asyncio
async def test_confirm_times_out_as_deny():
    """Silence is never approval."""
    session = _session(asyncio.get_running_loop(), timeout=0.2)
    result: dict[str, bool] = {}

    def worker():
        result["approved"] = session.confirm("Reboot", "now", Danger.CRITICAL)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=3)
    assert result["approved"] is False

    kinds = []
    while True:
        try:
            kinds.append((await asyncio.wait_for(session.next_frame(), 0.3))["t"])
        except asyncio.TimeoutError:
            break
    assert "confirm_request" in kinds
    assert "confirm_expired" in kinds, "the client must be told its card is dead"


@pytest.mark.asyncio
async def test_dropped_connection_denies_a_pending_confirm():
    session = _session(asyncio.get_running_loop(), timeout=30)
    result: dict[str, bool] = {}

    def worker():
        result["approved"] = session.confirm("Delete", "/tmp/y", Danger.DESTRUCTIVE)

    thread = threading.Thread(target=worker)
    thread.start()
    await asyncio.wait_for(session.next_frame(), timeout=2)

    session.close()          # client vanished mid-confirmation
    thread.join(timeout=3)
    assert not thread.is_alive(), "close() must release the blocked worker"
    assert result["approved"] is False


@pytest.mark.asyncio
async def test_confirm_on_a_closed_session_is_denied_immediately():
    session = _session(asyncio.get_running_loop())
    session.close()
    started = time.monotonic()
    assert session.confirm("Delete", "/tmp/z", Danger.DESTRUCTIVE) is False
    assert time.monotonic() - started < 1, "must not wait out the timeout"


@pytest.mark.asyncio
async def test_unknown_confirm_id_is_reported_not_approved():
    session = _session(asyncio.get_running_loop())
    assert session.resolve_confirm("does-not-exist", True) is False


@pytest.mark.asyncio
async def test_replying_twice_does_not_resurrect_a_confirmation():
    session = _session(asyncio.get_running_loop())

    def worker():
        session.confirm("Delete", "/tmp/a", Danger.DESTRUCTIVE)

    thread = threading.Thread(target=worker)
    thread.start()
    frame = await asyncio.wait_for(session.next_frame(), timeout=2)
    assert session.resolve_confirm(frame["confirm_id"], False)
    thread.join(timeout=2)
    assert session.resolve_confirm(frame["confirm_id"], True) is False


@pytest.mark.asyncio
async def test_queue_overflow_drops_frames_instead_of_blocking():
    """A wedged client must never stall the agent thread."""
    session = events.Session(asyncio.get_running_loop(), max_queue=4)
    for i in range(50):
        session.emit("tool_call", name=f"skill_{i}")
    await asyncio.sleep(0.05)
    assert (await asyncio.wait_for(session.next_frame(), 1))["t"] == "tool_call"


# ---------------------------------------------------------------------------
# Observer -> frames
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_observer_emits_structured_tool_frames():
    session = _session(asyncio.get_running_loop())
    session.turn_id = "t1"
    observe = events.observer_for(session)

    observe("tool", "disk_usage(path='/')",
            {"name": "disk_usage", "args": {"path": "/"}, "call_id": "c1"})

    frame = await asyncio.wait_for(session.next_frame(), timeout=1)
    assert frame["t"] == "tool_call"
    assert frame["name"] == "disk_usage"
    assert frame["args"] == {"path": "/"}
    assert frame["call_id"] == "c1"
    assert frame["id"] == "t1"


def test_two_argument_observers_still_work():
    """The REPL and voice session pass a 2-arg callback; it must not break."""
    from brahma.agent import _normalize_observer

    seen = []
    normalized = _normalize_observer(lambda kind, text: seen.append((kind, text)))
    normalized("tool", "x()", {"name": "x"})
    assert seen == [("tool", "x()")]


def test_three_argument_observers_receive_data():
    from brahma.agent import _normalize_observer

    seen = []
    normalized = _normalize_observer(
        lambda kind, text, data: seen.append((kind, text, data))
    )
    normalized("tool", "x()", {"name": "x"})
    assert seen == [("tool", "x()", {"name": "x"})]


def test_bound_method_observer_arity_is_detected():
    """`self` must not be counted — that's how a 2-arg method gets misread."""
    from brahma.agent import _normalize_observer

    class Repl:
        def __init__(self):
            self.seen = []

        def observe(self, kind, text):
            self.seen.append((kind, text))

    repl = Repl()
    _normalize_observer(repl.observe)("tool", "x()", {"name": "x"})
    assert repl.seen == [("tool", "x()")]


# ---------------------------------------------------------------------------
# Settings redaction
# ---------------------------------------------------------------------------

def test_settings_redaction_hides_key_material():
    from brahma.server import _redact

    out = _redact({
        "llm": {"provider": "cline", "api_key": "sk-secret", "model": "x"},
        "gemini_api_key": "AQ.abc123",
        "tokens": ["a", "b"],
        "safety": {"allow_destructive": True},
    })
    blob = json.dumps(out)
    assert "sk-secret" not in blob
    assert "AQ.abc123" not in blob
    assert out["llm"]["provider"] == "cline"
    assert out["safety"]["allow_destructive"] is True


def test_patching_settings_ignores_redaction_placeholders():
    """A client that GETs then POSTs settings must not overwrite a real key."""
    from brahma.server import _deep_merge

    base = {"llm": {"api_key": "real-key", "model": "old"}}
    merged = _deep_merge(base, {"llm": {"api_key": "***", "model": "new"}})
    assert merged["llm"]["api_key"] == "real-key"
    assert merged["llm"]["model"] == "new"


# ---------------------------------------------------------------------------
# HTTP surface — the guards as actually wired, not just their helpers
# ---------------------------------------------------------------------------

TOKEN = "test-token-value"

# Starlette's test client sends "Host: testserver" on websocket upgrades no
# matter what base_url says, which our loopback guard correctly rejects. Send a
# real loopback Host so these tests exercise the token path, not the Host path.
WS_HEADERS = {"Host": "127.0.0.1:8787"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from brahma.server import create_app

    return TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")


def test_rest_requires_a_token(client):
    assert client.get("/api/state").status_code == 401


def test_rest_rejects_a_wrong_token(client):
    got = client.get("/api/state", headers={"Authorization": "Bearer nope"})
    assert got.status_code == 401


def test_rest_accepts_the_right_token(client):
    got = client.get("/api/state", headers={"Authorization": f"Bearer {TOKEN}"})
    assert got.status_code == 200
    assert "skills" in got.json()


def test_rest_rejects_a_foreign_origin(client):
    got = client.get(
        "/api/state",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Origin": "https://evil.example.com"},
    )
    assert got.status_code == 403


def test_rest_rejects_a_rebound_host(client):
    got = client.get(
        "/api/state",
        headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.example.com"},
    )
    assert got.status_code == 400


def test_settings_endpoint_never_returns_key_material(client):
    got = client.get("/api/settings", headers={"Authorization": f"Bearer {TOKEN}"})
    assert got.status_code == 200
    blob = json.dumps(got.json())
    assert "eyJ" not in blob and "workos:" not in blob
    assert "AQ.Ab8" not in blob


def test_websocket_rejects_a_missing_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=WS_HEADERS) as ws:
            ws.receive_text()


def test_websocket_rejects_a_wrong_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=wrong", headers=WS_HEADERS) as ws:
            ws.receive_text()


def test_websocket_accepts_and_greets(client):
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        hello = json.loads(ws.receive_text())
    assert hello["t"] == "hello"
    assert hello["skills"] > 0
    assert hello["assistant_name"]


def test_websocket_rejects_unknown_frames(client):
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())          # hello
        ws.send_text(json.dumps({"t": "nonsense"}))
        frame = json.loads(ws.receive_text())
    assert frame["t"] == "error"


def test_websocket_ping_pongs(client):
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())          # hello
        ws.send_text(json.dumps({"t": "ping"}))
        assert json.loads(ws.receive_text())["t"] == "pong"


def test_stale_confirm_reply_is_reported(client):
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())          # hello
        ws.send_text(json.dumps(
            {"t": "confirm_reply", "confirm_id": "c999", "approve": True}))
        frame = json.loads(ws.receive_text())
    assert frame["t"] == "error"
    assert "expired" in frame["message"].lower()


# ---------------------------------------------------------------------------
# End to end: a real agent turn whose skill hits safety.enforce()
# ---------------------------------------------------------------------------

def _stub_llm(monkeypatch, script):
    """Drive the agent with a fixed sequence of replies instead of a model."""
    from brahma import llm as _llm

    calls = {"n": 0}

    class Stub:
        name = "cline"

        def available(self):
            return True

        def complete(self, messages, **kwargs):
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
    return facade


def test_destructive_skill_asks_the_browser_and_honours_a_denial(
    monkeypatch, tmp_path, client
):
    """The whole bridge: model -> skill -> enforce() -> browser -> back."""
    from brahma.llm import Reply, ToolCall

    victim = tmp_path / "victim.txt"
    victim.write_text("do not delete me")

    wants_delete = Reply(provider="cline", model="stub")
    wants_delete.tool_calls = [
        ToolCall(name="delete_path", arguments={"path": str(victim)}, id="tc1")
    ]
    answers = Reply(provider="cline", model="stub")
    answers.text = "I did not delete it."

    _stub_llm(monkeypatch, [wants_delete, answers])

    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
        ws.send_text(json.dumps({"t": "ask", "id": "turn-1", "text": "delete it"}))

        confirm = None
        seen = []
        for _ in range(12):
            frame = json.loads(ws.receive_text())
            seen.append(frame["t"])
            if frame["t"] == "confirm_request":
                confirm = frame
                break
        assert confirm is not None, f"no confirmation was requested; saw {seen}"
        assert confirm["danger"] in ("DESTRUCTIVE", "CRITICAL")
        assert str(victim) in confirm["detail"] or str(victim) in confirm["action"]

        ws.send_text(json.dumps({
            "t": "confirm_reply", "confirm_id": confirm["confirm_id"],
            "approve": False,
        }))

        for _ in range(12):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "turn_end":
                break

    assert victim.exists(), "a denied confirmation must not delete the file"
    assert victim.read_text() == "do not delete me"


def test_second_ask_while_busy_is_rejected_not_queued(monkeypatch, client):
    """Queuing would stack requests behind an unanswered confirmation."""
    from brahma.llm import Reply

    slow = Reply(provider="cline", model="stub")
    slow.text = "done"

    facade = _stub_llm(monkeypatch, [slow])
    original = facade.cline.complete

    def stall(messages, **kwargs):
        time.sleep(0.6)
        return original(messages, **kwargs)

    facade.cline.complete = stall

    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())          # hello
        ws.send_text(json.dumps({"t": "ask", "id": "a", "text": "one"}))
        ws.send_text(json.dumps({"t": "ask", "id": "b", "text": "two"}))

        errors = []
        for _ in range(10):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "error":
                errors.append(frame["message"])
            if frame["t"] == "turn_end":
                break
    assert any("already running" in m for m in errors), errors


def test_approved_confirmation_actually_runs_the_skill(
    monkeypatch, tmp_path, client
):
    """The mirror of the denial test — proves the bridge is bidirectional.

    Without this, a bridge that denied *everything* would still pass the
    denial test and look correct.
    """
    from brahma.llm import Reply, ToolCall

    doomed = tmp_path / "doomed.txt"
    doomed.write_text("delete me")

    wants_delete = Reply(provider="cline", model="stub")
    wants_delete.tool_calls = [
        # permanent=True because tmp_path is on tmpfs, which has no trash
        # directory — the trash path would fail for reasons unrelated to the
        # bridge under test.
        ToolCall(
            name="delete_path",
            arguments={"path": str(doomed), "permanent": True},
            id="tc1",
        )
    ]
    answers = Reply(provider="cline", model="stub")
    answers.text = "Deleted."

    _stub_llm(monkeypatch, [wants_delete, answers])

    result = ""
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())          # hello
        ws.send_text(json.dumps({"t": "ask", "id": "turn-1", "text": "delete it"}))

        confirm = None
        for _ in range(12):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "confirm_request":
                confirm = frame
                break
        assert confirm is not None

        ws.send_text(json.dumps({
            "t": "confirm_reply", "confirm_id": confirm["confirm_id"],
            "approve": True,
        }))

        for _ in range(12):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "tool_result":
                result = frame["result"]
            if frame["t"] == "turn_end":
                break

    assert not doomed.exists(), f"approval did not run the skill: {result!r}"


# ---------------------------------------------------------------------------
# The `run` frame — UI panels driving skills directly
# ---------------------------------------------------------------------------
#
# Panels invoke skills with no model in the loop. The confirmer must still be
# installed: without it safety.enforce() falls back to deny and every
# destructive panel button silently fails, and — worse — a future refactor
# could "fix" that by bypassing enforce entirely.


def test_run_frame_executes_a_safe_skill(client):
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())          # hello
        ws.send_text(json.dumps(
            {"t": "run", "id": "r1", "skill": "system_status", "args": {}}))

        frames, result = [], ""
        for _ in range(10):
            frame = json.loads(ws.receive_text())
            frames.append(frame["t"])
            if frame["t"] == "tool_result":
                result = frame["result"]
            if frame["t"] == "turn_end":
                break

    assert "tool_call" in frames and "tool_result" in frames
    assert result and "Unknown skill" not in result


def test_run_frame_reports_an_unknown_skill(client):
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps(
            {"t": "run", "id": "r1", "skill": "no_such_skill", "args": {}}))
        result = ""
        for _ in range(10):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "tool_result":
                result = frame["result"]
            if frame["t"] == "turn_end":
                break
    assert "Unknown skill" in result


def test_run_frame_still_asks_before_destroying(tmp_path, client):
    """A panel-issued delete must raise the same confirmation card."""
    victim = tmp_path / "panel-victim.txt"
    victim.write_text("keep me")

    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({
            "t": "run", "id": "r1", "skill": "delete_path",
            "args": {"path": str(victim), "permanent": True},
        }))

        confirm = None
        for _ in range(10):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "confirm_request":
                confirm = frame
                break
        assert confirm is not None, "a panel delete ran without asking"
        assert confirm["danger"] in ("DESTRUCTIVE", "CRITICAL")

        ws.send_text(json.dumps({
            "t": "confirm_reply", "confirm_id": confirm["confirm_id"],
            "approve": False,
        }))
        for _ in range(10):
            if json.loads(ws.receive_text())["t"] == "turn_end":
                break

    assert victim.exists(), "a denied panel confirmation still deleted the file"


def test_run_frame_honours_an_approval(tmp_path, client):
    """The mirror test — a bridge that denied everything would pass the above."""
    doomed = tmp_path / "panel-doomed.txt"
    doomed.write_text("delete me")

    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({
            "t": "run", "id": "r1", "skill": "delete_path",
            "args": {"path": str(doomed), "permanent": True},
        }))
        confirm = None
        for _ in range(10):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "confirm_request":
                confirm = frame
                break
        assert confirm is not None
        ws.send_text(json.dumps({
            "t": "confirm_reply", "confirm_id": confirm["confirm_id"],
            "approve": True,
        }))
        for _ in range(10):
            if json.loads(ws.receive_text())["t"] == "turn_end":
                break

    assert not doomed.exists(), "approval did not run the skill"


def test_run_frame_rejects_a_second_request_while_busy(client):
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps(
            {"t": "run", "id": "a", "skill": "list_processes", "args": {}}))
        ws.send_text(json.dumps(
            {"t": "run", "id": "b", "skill": "list_processes", "args": {}}))
        errors = []
        for _ in range(12):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "error":
                errors.append(frame["message"])
            if frame["t"] == "turn_end":
                break
    assert any("already running" in m for m in errors), errors


# ---------------------------------------------------------------------------
# Snapshot correctness
# ---------------------------------------------------------------------------

def test_processes_snapshot_reports_real_cpu_on_first_call():
    """psutil's cpu_percent returns 0.0 until it has a previous sample.

    Without priming, a freshly started server served a process table where
    every row read 0.0 — so "sort by CPU" degenerated to PID order and the
    screen filled with idle kernel threads. The flagship view lied on load.
    """
    import time

    from brahma import server

    server._PROC_CACHE.clear()
    server.prime_cpu_counters()
    time.sleep(0.3)          # let something, anything, use a slice of CPU

    snapshot = server._processes_snapshot(limit=50)
    assert snapshot["available"]
    assert snapshot["processes"], "no processes returned"
    assert any(p["cpu"] > 0 for p in snapshot["processes"]), (
        "every process reported 0.0% CPU — the counters were not primed"
    )


def test_processes_snapshot_prunes_dead_entries():
    """The cache must not grow without bound on a long-running server."""
    from brahma import server

    server._PROC_CACHE.clear()
    server._PROC_CACHE[-1] = object()      # a pid that cannot exist
    server._processes_snapshot(limit=5)
    assert -1 not in server._PROC_CACHE


def test_state_snapshot_carries_a_hostname():
    """The top bar names the machine being controlled."""
    from brahma import server

    assert isinstance(server._state_snapshot()["hostname"], str)


# ---------------------------------------------------------------------------
# Live voice
# ---------------------------------------------------------------------------
#
# These deliberately never start a real session: doing so would open the
# microphone and load a speech model on whatever machine runs the suite. What
# is worth testing is the wiring around it — the guards, and the decision that
# a destructive spoken request confirms in the browser rather than out loud.


def test_voice_start_reports_readiness_problems_instead_of_starting(client, monkeypatch):
    from brahma import voice

    monkeypatch.setattr(
        voice, "readiness", lambda: (False, ["No microphone", "No speech recognition"])
    )

    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())          # hello
        ws.send_text(json.dumps({"t": "voice_start", "mode": "local"}))
        frame = json.loads(ws.receive_text())

    assert frame["t"] == "voice_state"
    assert frame["phase"] == "error"
    assert "No microphone" in frame["text"]


def test_voice_stop_without_a_session_is_harmless(client):
    """The browser may send stop after the loop has already exited."""
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"t": "voice_stop"}))
        ws.send_text(json.dumps({"t": "ping"}))
        assert json.loads(ws.receive_text())["t"] == "pong"


def test_voice_session_uses_an_injected_confirmer_rather_than_asking_aloud():
    """A spoken summary cannot show the exact target; a card can.

    `VoiceSession` defaults to speaking the question and listening for "yes".
    The web path injects the browser confirmer instead, so the user sees the
    exact path or pid being acted on. This asserts the injection is honoured
    and that nothing is spoken to reach that decision.
    """
    from brahma import voice
    from brahma.safety import Danger

    seen: list[tuple[str, str]] = []

    def confirmer(action, detail, danger):
        seen.append((action, detail))
        return False

    convo = voice.VoiceSession(confirmer=confirmer)
    approved = convo._confirm("delete /tmp/x", "destructive", Danger.DESTRUCTIVE)

    assert approved is False
    assert seen == [("delete /tmp/x", "destructive")]


def test_voice_agent_is_separate_from_the_text_conversation():
    """Voice must not rewrite the text console's history.

    The server builds a VoiceSession without passing an agent, so it creates
    its own. If that ever changed to share the web agent, the two transcripts
    would interleave and each would misrepresent what the model actually saw.
    """
    from brahma import voice

    convo = voice.VoiceSession()
    assert convo.agent is not None
    assert convo.agent.source == "voice"
