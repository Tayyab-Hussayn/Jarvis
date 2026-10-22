"""Tests for the phone channel: pairing, device auth, the vocabulary, and
``phone.call()``.

The test thread plays the phone over a real websocket (Starlette's TestClient
runs the app on its own loop thread). ``phone.call()`` blocks, so it runs on a
helper thread while the "phone" answers, stays silent or hangs up.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from kravein import phone, webauth

TOKEN = "test-token-phone"
HOST = {"Host": "127.0.0.1:8787"}
BROWSER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Fresh store, no outstanding offer, no live links, audit captured."""
    monkeypatch.setattr(phone, "PHONES_FILE", tmp_path / "phones.json")
    monkeypatch.setattr(phone, "_offer", None)
    monkeypatch.setattr(phone, "_links", {})
    records: list[tuple[str, dict]] = []
    monkeypatch.setattr(phone.audit, "record", lambda event, **f: records.append((event, f)))
    webauth.clear_lan_hosts()
    return records


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    return TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")


def _offer(client) -> dict:
    got = client.post("/api/phone/offer", headers=BROWSER)
    assert got.status_code == 200, got.text
    return got.json()["offer"]


def _pair(client, name="Pixel") -> dict:
    offer = _offer(client)
    got = client.post("/phone/pair", json={"code": offer["code"], "device_name": name})
    assert got.status_code == 200, got.text
    return got.json()


def _device_headers(cred: dict) -> dict:
    return {**HOST, "Authorization": f"Device {cred['device_id']}:{cred['device_secret']}"}


def _send(ws, frame_type, request_id="h1", **payload):
    ws.send_text(json.dumps({"type": frame_type, "request_id": request_id, "payload": payload}))


def _recv(ws) -> dict:
    return json.loads(ws.receive_text())


ALL = sorted(phone.command_names())


def _hello(ws, capabilities=ALL):
    _send(ws, "hello", "h1", protocol=phone.protocol_version(), capabilities=capabilities)
    welcome = _recv(ws)
    assert welcome["type"] == "welcome", welcome
    return welcome


def _probe(ws):
    """Use a socket that should have been refused. An accepted phone socket
    answers ping with pong, so a broken guard fails here instead of hanging
    on a socket that waits for hello forever."""
    _send(ws, "ping", "probe")
    pytest.fail(f"socket was accepted: {ws.receive_text()}")


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _Caller:
    """Run phone.call() on a thread; collect its outcome."""

    def __init__(self, *args, **kwargs):
        self.result = None
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, args=args, kwargs=kwargs, daemon=True)
        self.thread.start()

    def _run(self, *args, **kwargs):
        try:
            self.result = phone.call(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            self.error = exc

    def join(self, timeout=5.0):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "phone.call() hung"
        return self


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

def test_protocol_file_is_self_consistent():
    proto = phone.protocol()
    known_types = {"string", "integer", "boolean", "object", "command",
                   "list_of_commands", "error_code", "url"}
    for name, spec in proto["frames"].items():
        assert spec["direction"] in ("phone_to_server", "server_to_phone", "both"), name
        for field, f in spec["payload"].items():
            assert f["type"] in known_types, (name, field)
    for name, spec in proto["commands"].items():
        for arg, f in spec["args"].items():
            assert f["type"] in known_types, (name, arg)
    # One vocabulary: the old category names are not commands.
    for category in ("battery", "flashlight", "volume", "media", "apps", "wifi_state"):
        assert category not in proto["commands"]


def test_validate_command_rejects_unknown_and_bad_args():
    with pytest.raises(phone.PhoneCommandRejected) as exc:
        phone.validate_command("battery", {})
    assert exc.value.code == "unknown_command"
    with pytest.raises(phone.PhoneCommandRejected):
        phone.validate_command("volume_set", {"percent": 101})
    with pytest.raises(phone.PhoneCommandRejected):
        phone.validate_command("volume_set", {"percent": True})
    with pytest.raises(phone.PhoneCommandRejected):
        phone.validate_command("volume_set", {})
    with pytest.raises(phone.PhoneCommandRejected):
        phone.validate_command("open_url", {"url": "javascript:alert(1)"})
    with pytest.raises(phone.PhoneCommandRejected):
        phone.validate_command("get_battery", {"extra": 1})
    assert phone.validate_command("volume_set", {"percent": 40}) == {"percent": 40}
    assert phone.validate_command("open_url", {"url": "https://example.com"})


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

def test_offer_requires_the_browser_token(client):
    assert client.post("/api/phone/offer").status_code == 401


def test_pairing_happy_path_stores_only_a_hash(client, isolated):
    offer = _offer(client)
    assert offer["service"] == "kravein" and offer["port"] == 8787 and offer["v"] == 1
    got = client.post("/phone/pair", json={"code": offer["code"], "device_name": "Pixel 9"})
    assert got.status_code == 200
    cred = got.json()
    assert cred["device_id"].startswith("ph_") and len(cred["device_secret"]) >= 40

    stored = phone.PHONES_FILE.read_text()
    assert cred["device_secret"] not in stored
    assert offer["code"] not in stored
    assert phone.PHONES_FILE.stat().st_mode & 0o777 == 0o600

    logged = json.dumps(isolated)
    assert cred["device_secret"] not in logged and offer["code"] not in logged
    # No field may shadow the audit record's own `host` (the machine name).
    assert all("host" not in fields for _, fields in isolated)

    devices = client.get("/api/phones", headers=BROWSER).json()["devices"]
    assert [d["name"] for d in devices] == ["Pixel 9"]
    assert "secret_sha256" not in json.dumps(devices)


def test_reused_offer_is_refused(client):
    offer = _offer(client)
    assert client.post("/phone/pair", json={"code": offer["code"]}).status_code == 200
    assert client.post("/phone/pair", json={"code": offer["code"]}).status_code == 403


def test_expired_offer_is_refused(client, monkeypatch):
    offer = _offer(client)
    later = time.monotonic() + phone.DEFAULT_OFFER_TTL + 1
    monkeypatch.setattr(phone, "_clock", lambda: later)
    assert client.post("/phone/pair", json={"code": offer["code"]}).status_code == 403


def test_wrong_code_does_not_burn_the_offer(client):
    offer = _offer(client)
    assert client.post("/phone/pair", json={"code": "guess"}).status_code == 403
    assert client.post("/phone/pair", json={"code": offer["code"]}).status_code == 200


def test_a_new_offer_replaces_the_old_one(client):
    first = _offer(client)
    _offer(client)
    assert client.post("/phone/pair", json={"code": first["code"]}).status_code == 403


def test_pair_without_an_offer_or_with_junk_is_refused(client):
    assert client.post("/phone/pair", json={"code": "x"}).status_code == 403
    assert client.post("/phone/pair", content=b"not json",
                       headers={"Content-Type": "application/json"}).status_code == 400


# ---------------------------------------------------------------------------
# Who may use which endpoint
# ---------------------------------------------------------------------------

def test_phone_endpoints_refuse_any_origin(client):
    offer = _offer(client)
    got = client.post("/phone/pair", json={"code": offer["code"]},
                      headers={"Origin": "http://127.0.0.1:8787"})
    assert got.status_code == 403
    # The offer survives a refused browser attempt.
    assert client.post("/phone/pair", json={"code": offer["code"]}).status_code == 200


def test_phone_ws_refuses_any_origin(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    headers = {**_device_headers(cred), "Origin": "http://127.0.0.1:8787"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/phone/ws", headers=headers) as ws:
            _probe(ws)


def test_phone_endpoints_keep_the_host_check(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    offer = _offer(client)
    got = client.post("/phone/pair", json={"code": offer["code"]},
                      headers={"Host": "evil.example.com"})
    assert got.status_code == 400
    headers = {**_device_headers(cred), "Host": "evil.example.com"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/phone/ws", headers=headers) as ws:
            _probe(ws)


def test_browser_token_is_not_a_device_credential(client):
    from starlette.websockets import WebSocketDisconnect

    _pair(client)
    for auth in (f"Bearer {TOKEN}", TOKEN):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/phone/ws", headers={**HOST, "Authorization": auth},
            ) as ws:
                _probe(ws)


def test_device_credential_is_not_a_browser_token(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    device_auth = _device_headers(cred)["Authorization"]
    assert client.get("/api/state", headers={"Authorization": device_auth}).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={**HOST, "Authorization": device_auth}) as ws:
            ws.receive_text()
    secret = cred["device_secret"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?token={secret}", headers=HOST) as ws:
            ws.receive_text()


@pytest.mark.parametrize("auth", [
    "Device ph_nope:secret",          # unknown device
    None,                              # no credential
    "Device ",                         # malformed
])
def test_phone_ws_rejects_bad_credentials(client, auth):
    from starlette.websockets import WebSocketDisconnect

    headers = dict(HOST)
    if auth is not None:
        headers["Authorization"] = auth
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/phone/ws", headers=headers) as ws:
            _probe(ws)


def test_phone_ws_rejects_a_wrong_secret(client, isolated):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    headers = {**HOST, "Authorization": f"Device {cred['device_id']}:wrong"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/phone/ws", headers=headers) as ws:
            _probe(ws)
    assert any(e == "phone.auth_refused" for e, _ in isolated)


def test_revoked_device_cannot_connect(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    got = client.delete(f"/api/phones/{cred['device_id']}", headers=BROWSER)
    assert got.status_code == 200
    assert client.delete(f"/api/phones/{cred['device_id']}", headers=BROWSER).status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
            _probe(ws)


def test_revoke_drops_the_live_socket_and_fails_the_pending_call(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _hello(ws)
        caller = _Caller("get_battery", timeout=5)
        assert _recv(ws)["type"] == "execute"
        client.delete(f"/api/phones/{cred['device_id']}", headers=BROWSER)
        caller.join()
        assert isinstance(caller.error, phone.PhoneDisconnected)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

def test_hello_is_answered_with_welcome(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        welcome = _hello(ws)
        assert welcome["request_id"] == "h1"
        assert welcome["payload"]["device_id"] == cred["device_id"]
        assert phone.connected()[0]["device_id"] == cred["device_id"]
        devices = client.get("/api/phones", headers=BROWSER).json()["devices"]
        assert devices[0]["connected"] is True


def test_hello_with_an_unknown_capability_is_rejected(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _send(ws, "hello", "h1", protocol=1, capabilities=["get_battery", "battery"])
        reply = _recv(ws)
    assert reply["type"] == "error" and reply["payload"]["code"] == "unknown_capability"
    assert reply["request_id"] == "h1"


def test_protocol_mismatch_is_refused_and_closed(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _send(ws, "hello", "h1", protocol=99, capabilities=[])
        assert _recv(ws)["payload"]["code"] == "protocol_mismatch"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


@pytest.mark.parametrize("raw, code", [
    ("not json", "malformed_frame"),
    ("[1, 2]", "malformed_frame"),
    (json.dumps({"type": "ping", "payload": {}}), "malformed_frame"),
    (json.dumps({"type": "nonsense", "request_id": "x", "payload": {}}), "unknown_frame"),
    (json.dumps({"type": "execute", "request_id": "x",
                 "payload": {"command": "get_battery", "args": {}}}), "unknown_frame"),
    (json.dumps({"type": "result", "request_id": "x", "payload": {"ok": "yes"}}), "malformed_frame"),
])
def test_malformed_and_unknown_frames_get_an_error_reply(client, raw, code):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        ws.send_text(raw)
        reply = _recv(ws)
        assert reply["type"] == "error" and reply["payload"]["code"] == code
        # The socket is still usable afterwards.
        _send(ws, "ping", "p1")
        assert _recv(ws) == {"type": "pong", "request_id": "p1", "payload": {}}


def test_oversized_frame_closes_the_socket(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        ws.send_text("x" * (phone.protocol()["max_frame_bytes"] + 1))
        assert _recv(ws)["payload"]["code"] == "frame_too_large"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


# ---------------------------------------------------------------------------
# phone.call()
# ---------------------------------------------------------------------------

def test_call_round_trip(client, isolated):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _hello(ws)
        caller = _Caller("volume_set", {"percent": 30}, timeout=5)
        execute = _recv(ws)
        assert execute["type"] == "execute"
        assert execute["payload"] == {"command": "volume_set", "args": {"percent": 30}}
        _send(ws, "result", execute["request_id"], ok=True, data={"percent": 30})
        caller.join()
    assert caller.error is None and caller.result == {"percent": 30}
    commands = [f for e, f in isolated if e == "phone.command"]
    assert len(commands) == 1 and commands[0]["outcome"] == "ok"


def test_phone_reported_failure_raises(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _hello(ws)
        caller = _Caller("flashlight_on", timeout=5)
        execute = _recv(ws)
        _send(ws, "result", execute["request_id"], ok=False,
              error_code="permission_denied", error="camera permission missing")
        caller.join()
    assert isinstance(caller.error, phone.PhoneCommandFailed)
    assert caller.error.code == "permission_denied"


def test_unknown_command_never_reaches_the_phone(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _hello(ws)
        with pytest.raises(phone.PhoneCommandRejected):
            phone.call("battery")
        with pytest.raises(phone.PhoneCommandRejected):
            phone.call("open_url", {"url": "file:///etc/passwd"})
        # Nothing was sent: the next frame is the pong we ask for.
        _send(ws, "ping", "p1")
        assert _recv(ws)["type"] == "pong"


def test_command_the_phone_did_not_advertise_is_rejected(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _hello(ws, capabilities=["get_battery"])
        with pytest.raises(phone.PhoneCommandRejected) as exc:
            phone.call("flashlight_on")
        assert exc.value.code == "unknown_capability"


def test_no_phone_connected_raises_at_once():
    started = time.monotonic()
    with pytest.raises(phone.PhoneNotConnected):
        phone.call("get_battery", timeout=5)
    assert time.monotonic() - started < 1


def test_phone_that_has_not_said_hello_is_not_ready(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)):
        with pytest.raises(phone.PhoneNotConnected):
            phone.call("get_battery")


def test_timeout_of_an_in_flight_command(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _hello(ws)
        started = time.monotonic()
        caller = _Caller("get_battery", timeout=0.3)
        execute = _recv(ws)               # the phone got it and stays silent
        caller.join()
        assert isinstance(caller.error, phone.PhoneTimeout)
        assert time.monotonic() - started < 3
        # The late answer is not silently matched to anything.
        _send(ws, "result", execute["request_id"], ok=True, data={})
        late = _recv(ws)
        assert late["type"] == "error" and late["payload"]["code"] == "unknown_request"


def test_socket_drop_mid_command_fails_the_call(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _hello(ws)
        caller = _Caller("get_battery", timeout=10)
        assert _recv(ws)["type"] == "execute"
    # Context exit closed the phone's socket with the command unanswered.
    started = time.monotonic()
    caller.join()
    assert isinstance(caller.error, phone.PhoneDisconnected)
    assert time.monotonic() - started < 3
    assert _wait_until(lambda: phone.connected() == [])


def test_phone_error_frame_for_a_pending_command_fails_it(client):
    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as ws:
        _hello(ws)
        caller = _Caller("get_battery", timeout=5)
        execute = _recv(ws)
        _send(ws, "error", execute["request_id"], code="invalid_args", message="nope")
        caller.join()
    assert isinstance(caller.error, phone.PhoneCommandFailed)
    assert caller.error.code == "invalid_args"


def test_a_second_connection_replaces_the_first(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as first:
        _hello(first)
        caller = _Caller("get_battery", timeout=5)
        assert _recv(first)["type"] == "execute"
        with client.websocket_connect("/phone/ws", headers=_device_headers(cred)) as second:
            caller.join()
            assert isinstance(caller.error, phone.PhoneDisconnected)
            with pytest.raises(WebSocketDisconnect):
                first.receive_text()
            _hello(second)
            assert len(phone.connected()) == 1


def test_two_phones_without_a_name_is_ambiguous(client):
    a = _pair(client, "Pixel")
    b = _pair(client, "Galaxy")
    with client.websocket_connect("/phone/ws", headers=_device_headers(a)) as wa, \
         client.websocket_connect("/phone/ws", headers=_device_headers(b)) as wb:
        _hello(wa)
        _hello(wb)
        with pytest.raises(phone.PhoneAmbiguous) as exc:
            phone.call("get_battery")
        assert "Pixel" in str(exc.value) and "Galaxy" in str(exc.value)

        caller = _Caller("get_battery", device="galaxy", timeout=5)
        execute = _recv(wb)
        _send(wb, "result", execute["request_id"], ok=True, data={"percent": 80})
        caller.join()
        assert caller.result == {"percent": 80}


def test_call_from_the_event_loop_thread_raises_instead_of_deadlocking():
    import asyncio

    async def inside_loop():
        link = phone.PhoneLink({"device_id": "ph_x", "name": "x"}, asyncio.get_running_loop())
        with pytest.raises(phone.PhoneError):
            link.request("get_battery", {}, timeout=5)

    asyncio.run(inside_loop())
