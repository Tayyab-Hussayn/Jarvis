"""POST /phone/ui-session: a paired phone trades its device credential for a
short-lived UI token that works wherever the browser token works (and nowhere
the browser token doesn't)."""

from __future__ import annotations

import json
import time

import pytest

from kravein import phone, webauth

TOKEN = "test-token-ui-session"
HOST = {"Host": "127.0.0.1:8787"}
BROWSER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(phone, "PHONES_FILE", tmp_path / "phones.json")
    monkeypatch.setattr(phone, "_offer", None)
    monkeypatch.setattr(phone, "_links", {})
    records: list[tuple[str, dict]] = []
    monkeypatch.setattr(phone.audit, "record", lambda event, **f: records.append((event, f)))
    webauth.clear_lan_hosts()
    webauth.clear_ui_tokens()
    yield records
    webauth.clear_ui_tokens()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    return TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")


def _pair(client, name="Pixel") -> dict:
    offer = client.post("/api/phone/offer", headers=BROWSER).json()["offer"]
    got = client.post("/phone/pair", json={"code": offer["code"], "device_name": name})
    assert got.status_code == 200, got.text
    return got.json()


def _device_headers(cred: dict) -> dict:
    return {**HOST, "Authorization": f"Device {cred['device_id']}:{cred['device_secret']}"}


def _ui_session(client, cred):
    return client.post("/phone/ui-session", headers=_device_headers(cred))


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_issues_a_token_that_works_on_api_and_ws(client, isolated):
    cred = _pair(client)
    got = _ui_session(client, cred)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["ttl_seconds"] == 12 * 3600
    assert body["expires_at"] > time.time()
    token = body["token"]
    assert client.get("/api/state", headers=_bearer(token)).status_code == 200
    with client.websocket_connect(f"/ws?token={token}", headers=HOST) as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
    audits = [f for e, f in isolated if e == "phone.ui_session"]
    assert audits and audits[0]["device_id"] == cred["device_id"]
    assert token not in json.dumps(isolated)


def test_ui_token_is_refused_on_phone_endpoints(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    token = _ui_session(client, cred).json()["token"]
    got = client.post("/phone/ui-session", headers={**HOST, **_bearer(token)})
    assert got.status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/phone/ws", headers={**HOST, **_bearer(token)},
        ) as ws:
            ws.send_text(json.dumps({"type": "ping", "request_id": "p", "payload": {}}))
            pytest.fail(f"socket was accepted: {ws.receive_text()}")


def test_device_credential_stays_refused_on_the_ui(client):
    cred = _pair(client)
    auth = {"Authorization": _device_headers(cred)["Authorization"]}
    assert client.get("/api/state", headers=auth).status_code == 401


def test_wrong_credential_is_401(client):
    cred = _pair(client)
    bad = {**HOST, "Authorization": f"Device {cred['device_id']}:nope"}
    assert client.post("/phone/ui-session", headers=bad).status_code == 401
    assert client.post("/phone/ui-session", headers=HOST).status_code == 401
    # The browser token is not a device credential.
    assert client.post("/phone/ui-session", headers={**HOST, **BROWSER}).status_code == 401


def test_refuses_any_origin(client):
    cred = _pair(client)
    headers = {**_device_headers(cred), "Origin": "http://127.0.0.1:8787"}
    assert client.post("/phone/ui-session", headers=headers).status_code == 403


def test_keeps_the_host_check(client):
    cred = _pair(client)
    headers = {**_device_headers(cred), "Host": "evil.example.com"}
    assert client.post("/phone/ui-session", headers=headers).status_code == 400


def test_ui_token_keeps_origin_and_host_checks(client):
    cred = _pair(client)
    auth = _bearer(_ui_session(client, cred).json()["token"])
    assert client.get("/api/state", headers={**auth, "Host": "evil.example.com"}).status_code == 400
    assert client.get(
        "/api/state", headers={**auth, "Origin": "http://evil.example.com"},
    ).status_code == 403


def test_token_expires(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    token = _ui_session(client, cred).json()["token"]
    later = time.time() + 13 * 3600
    monkeypatch.setattr(webauth, "_now", lambda: later)
    assert client.get("/api/state", headers=_bearer(token)).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?token={token}", headers=HOST) as ws:
            ws.receive_text()


def test_ttl_is_configurable(client, monkeypatch):
    real_get = webauth.config.get
    monkeypatch.setattr(
        webauth.config, "get",
        lambda key, default=None: 60 if key == "phone.ui_token_ttl_seconds" else real_get(key, default),
    )
    cred = _pair(client)
    assert _ui_session(client, cred).json()["ttl_seconds"] == 60


def test_revoking_the_device_kills_its_token_and_open_socket(client):
    from starlette.websockets import WebSocketDisconnect

    cred = _pair(client)
    other = _pair(client, name="Other")
    token = _ui_session(client, cred).json()["token"]
    other_token = _ui_session(client, other).json()["token"]
    with client.websocket_connect(f"/ws?token={token}", headers=HOST) as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
        assert client.delete(f"/api/phones/{cred['device_id']}", headers=BROWSER).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            for _ in range(50):
                ws.receive_text()
    assert client.get("/api/state", headers=_bearer(token)).status_code == 401
    # Another device's token is untouched, and the revoked credential is dead.
    assert client.get("/api/state", headers=_bearer(other_token)).status_code == 200
    assert _ui_session(client, cred).status_code == 401
