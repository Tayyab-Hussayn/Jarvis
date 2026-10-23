"""Confirmation policy endpoints (#39): token-gated, validated, and the only
way the web UI can change the policy."""

from __future__ import annotations

import copy

import pytest

from kravein import audit, config, safety, webauth

TOKEN = "test-token-policy"
BROWSER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_settings_cache", None)
    # config._deep_merge shares nested dicts with DEFAULT_SETTINGS, so a write
    # to the "safety" section would otherwise leak into every later test.
    monkeypatch.setattr(config, "DEFAULT_SETTINGS", copy.deepcopy(config.DEFAULT_SETTINGS))
    monkeypatch.setattr(safety, "_auto_until", None)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda ev, **kw: events.append((ev, kw)))
    webauth.clear_lan_hosts()
    return events


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    return TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")


def test_requires_token(client):
    assert client.get("/api/safety/policy").status_code == 401
    assert client.post("/api/safety/policy", json={"mode": "auto"}).status_code == 401
    assert client.post("/api/safety/auto/off").status_code == 401
    assert safety.confirmation_policy()["mode"] == "ask"


def test_read_default(client):
    got = client.get("/api/safety/policy", headers=BROWSER).json()
    assert got["mode"] == "ask"
    assert got["seconds_left"] == 0
    assert got["default_seconds"] == 3600 and got["max_seconds"] == 4 * 3600


def test_set_auto_with_duration_audited_as_web(client, isolated):
    got = client.post("/api/safety/policy", headers=BROWSER, json={"mode": "auto", "seconds": 900})
    assert got.status_code == 200
    assert got.json()["mode"] == "auto"
    assert 0 < got.json()["seconds_left"] <= 900
    assert client.get("/api/safety/policy", headers=BROWSER).json()["mode"] == "auto"
    assert ("safety.mode_changed", {"mode": "auto", "seconds": 900, "by": "web"}) in isolated


def test_auto_defaults_to_an_hour(client):
    got = client.post("/api/safety/policy", headers=BROWSER, json={"mode": "auto"}).json()
    assert 3590 < got["seconds_left"] <= 3600


def test_decline_then_ask(client):
    assert client.post("/api/safety/policy", headers=BROWSER, json={"mode": "decline"}).json()["mode"] == "decline"
    assert client.post("/api/safety/policy", headers=BROWSER, json={"mode": "ask"}).json()["mode"] == "ask"


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "yolo"},
        {},
        {"mode": None},
        {"mode": "auto", "seconds": 0},
        {"mode": "auto", "seconds": -5},
        {"mode": "auto", "seconds": "60"},
        {"mode": "auto", "seconds": True},
        {"mode": "auto", "seconds": 1.5},
        [1, 2],
    ],
)
def test_bad_input_is_400_with_a_message(client, body):
    got = client.post("/api/safety/policy", headers=BROWSER, json=body)
    assert got.status_code == 400
    assert got.json()["detail"]
    assert safety.confirmation_policy()["mode"] == "ask"


def test_not_json_is_400(client):
    got = client.post("/api/safety/policy", headers={**BROWSER, "Content-Type": "application/json"}, content=b"{nope")
    assert got.status_code == 400


def test_turn_off(client):
    client.post("/api/safety/policy", headers=BROWSER, json={"mode": "auto"})
    got = client.post("/api/safety/auto/off", headers=BROWSER)
    assert got.status_code == 200 and got.json()["mode"] == "ask"
    # Safe to repeat.
    assert client.post("/api/safety/auto/off", headers=BROWSER).json()["mode"] == "ask"


@pytest.mark.parametrize(
    "patch",
    [
        {"safety": {"confirmation_mode": "auto"}},
        {"safety": {"confirmation_mode": "decline"}},
        {"safety": {"allow_destructive": True}},
        {"safety": "anything"},
        {"safety": None},
        {"safety": {"confirmation_mode": "ask", "other": 1}},
    ],
)
def test_generic_settings_cannot_touch_the_policy(client, patch):
    safety.set_confirmation_mode("decline", by="test")
    got = client.post("/api/settings", headers=BROWSER, json=patch)
    assert got.status_code == 400
    assert safety.confirmation_policy()["mode"] == "decline"
    assert safety._auto_until is None


def test_generic_settings_still_work_for_other_keys(client):
    got = client.post("/api/settings", headers=BROWSER, json={"voice": {"mode": "local"}})
    assert got.status_code == 200
    assert config.get("voice.mode") == "local"
