"""Confirmations relayed to paired phones (#50).

The test thread plays the phone over a real websocket; a helper thread plays
the agent worker parked inside ``Session.confirm``. The invariant under test:
a phone can only ever *answer* a confirmation that is open, first answer wins,
and nothing it does can approve anything by accident.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from kravein import events, phone, webauth
from kravein.safety import Danger

TOKEN = "test-token-relay"
HOST = {"Host": "127.0.0.1:8787"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(phone, "PHONES_FILE", tmp_path / "phones.json")
    monkeypatch.setattr(phone, "_offer", None)
    monkeypatch.setattr(phone, "_links", {})
    monkeypatch.setattr(events, "_open_confirms", {})
    records: list[tuple[str, dict]] = []
    monkeypatch.setattr(phone.audit, "record", lambda e, **f: records.append((e, f)))
    monkeypatch.setattr(events.audit, "record", lambda e, **f: records.append((e, f)))
    webauth.clear_lan_hosts()
    return records


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    return TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    t = threading.Thread(target=lp.run_forever, daemon=True)
    t.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    t.join(2)


def _pair(client, name="Pixel") -> dict:
    offer = client.post("/api/phone/offer", headers={"Authorization": f"Bearer {TOKEN}"}).json()["offer"]
    got = client.post("/phone/pair", json={"code": offer["code"], "device_name": name})
    assert got.status_code == 200, got.text
    return got.json()


def _headers(cred) -> dict:
    return {**HOST, "Authorization": f"Device {cred['device_id']}:{cred['device_secret']}"}


def _send(ws, t, rid="x", **payload):
    ws.send_text(json.dumps({"type": t, "request_id": rid, "payload": payload}))


def _recv(ws) -> dict:
    return json.loads(ws.receive_text())


def _hello(ws):
    _send(ws, "hello", "h1", protocol=phone.protocol_version(), capabilities=[])
    assert _recv(ws)["type"] == "welcome"


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class _Confirm:
    """Run Session.confirm on a worker thread."""

    def __init__(self, session, action="kill firefox", detail="pid 42", danger=Danger.DESTRUCTIVE):
        self.session, self.result = session, None
        self.thread = threading.Thread(
            target=lambda: setattr(self, "result", session.confirm(action, detail, danger)),
            daemon=True,
        )
        self.thread.start()

    def join(self):
        self.thread.join(5)
        assert not self.thread.is_alive(), "confirm() hung"
        return self.result


def _pending_id(timeout=3.0) -> str:
    assert _wait(lambda: events.open_confirms(), timeout)
    return events.open_confirms()[0][0]


def test_request_reaches_phone_and_reply_approves(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        req = _recv(ws)
        assert req["type"] == "confirm_request"
        assert req["request_id"] == _pending_id()
        p = req["payload"]
        assert (p["action"], p["detail"], p["danger"]) == ("kill firefox", "pid 42", "destructive")
        assert p["expires_at"] > time.time()
        _send(ws, "confirm_reply", req["request_id"], approve=True)
        assert c.join() is True
        closed = _recv(ws)
        assert closed["type"] == "confirm_closed"
        assert closed["request_id"] == req["request_id"]
        assert closed["payload"] == {"outcome": "approved"}


def test_critical_danger_is_reported(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session, danger=Danger.CRITICAL)
        req = _recv(ws)
        assert req["payload"]["danger"] == "critical"
        _send(ws, "confirm_reply", req["request_id"], approve=False)
        assert c.join() is False
        assert _recv(ws)["payload"] == {"outcome": "denied"}


def test_browser_answer_closes_the_phone_notification(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        req = _recv(ws)
        assert session.resolve_confirm(req["request_id"], False)
        assert c.join() is False
        assert _recv(ws)["payload"] == {"outcome": "denied"}


def test_timeout_expires_on_the_phone_and_denies(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=0.2)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        req = _recv(ws)
        assert c.join() is False
        closed = _recv(ws)
        assert closed["request_id"] == req["request_id"]
        assert closed["payload"] == {"outcome": "expired"}


def test_cancel_denies_and_closes_on_the_phone(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        _recv(ws)
        assert session.deny_pending() == 1
        assert c.join() is False
        assert _recv(ws)["payload"] == {"outcome": "denied"}


def test_dropped_browser_session_denies_and_closes_on_the_phone(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        _recv(ws)
        session.close()
        assert c.join() is False
        assert _recv(ws)["payload"] == {"outcome": "denied"}


def test_phone_disconnect_does_not_deny(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        req = _recv(ws)
    # Socket closed; the browser can still answer.
    assert _wait(lambda: not phone._links)
    assert c.thread.is_alive()
    assert session.resolve_confirm(req["request_id"], True)
    assert c.join() is True


@pytest.mark.parametrize("payload", [
    {"approve": "true"}, {"approve": 1}, {"approve": None}, {}, {"approve": True, "x": 1},
])
def test_malformed_reply_never_resolves(client, loop, payload):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        req = _recv(ws)
        _send(ws, "confirm_reply", req["request_id"], **payload)
        assert _recv(ws)["type"] == "error"
        assert c.thread.is_alive() and events.open_confirms()
        session.deny_pending()
        c.join()


def test_unknown_and_duplicate_reply_is_refused_and_cannot_flip_a_denial(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        _send(ws, "confirm_reply", "c99999", approve=True)
        err = _recv(ws)
        assert err["type"] == "error" and err["payload"]["code"] == "unknown_request"

        c = _Confirm(session)
        req = _recv(ws)
        assert session.resolve_confirm(req["request_id"], False)
        # First answer wins, even before the worker has woken up.
        assert session.resolve_confirm(req["request_id"], True) is False
        assert events.resolve_open(req["request_id"], True) is False
        assert c.join() is False


def test_reply_before_hello_is_refused(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    c = _Confirm(session)
    cid = _pending_id()
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _send(ws, "confirm_reply", cid, approve=True)
        assert _recv(ws)["payload"]["code"] == "not_ready"
        assert c.thread.is_alive()
    session.deny_pending()
    c.join()


def test_two_sessions_never_share_a_confirmation_id(loop):
    a, b = events.Session(loop, confirm_timeout=5), events.Session(loop, confirm_timeout=5)
    ca, cb = _Confirm(a), _Confirm(b)
    assert _wait(lambda: len(events.open_confirms()) == 2)
    ids = [i for i, _ in events.open_confirms()]
    assert len(set(ids)) == 2
    # Answering one by id leaves the other untouched.
    assert events.resolve_open(ids[0], True)
    assert _wait(lambda: not (ca.thread.is_alive() and cb.thread.is_alive()))
    survivor = ca if ca.thread.is_alive() else cb
    survivor.session.deny_pending()
    ca.join(); cb.join()


def test_late_phone_gets_the_open_confirmation(client, loop):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    c = _Confirm(session)
    cid = _pending_id()
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        req = _recv(ws)
        assert req["type"] == "confirm_request" and req["request_id"] == cid
        _send(ws, "confirm_reply", cid, approve=True)
        assert c.join() is True


def test_setting_off_sends_nothing(client, loop, monkeypatch):
    from kravein import config

    real = config.get
    monkeypatch.setattr(
        phone.config, "get",
        lambda k, d=None: False if k == "phone.confirm_notifications" else real(k, d),
    )
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=0.3)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        c.join()
        _send(ws, "ping", "p1")
        # The first frame back is the pong: no request was relayed.
        assert _recv(ws)["type"] == "pong"


def test_reply_is_audited(client, loop, isolated):
    cred = _pair(client)
    session = events.Session(loop, confirm_timeout=5)
    with client.websocket_connect("/phone/ws", headers=_headers(cred)) as ws:
        _hello(ws)
        c = _Confirm(session)
        req = _recv(ws)
        _send(ws, "confirm_reply", req["request_id"], approve=True)
        c.join()
    rec = [f for e, f in isolated if e == "phone.confirm_reply"]
    assert rec == [{
        "device_id": cred["device_id"], "confirm_id": req["request_id"],
        "approve": True, "accepted": True,
    }]
