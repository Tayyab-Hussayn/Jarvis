"""Models pane endpoints (v3 phase 0d): catalog + role chains + spend.

All writes go through ``usage.set_role_chain``; nothing here probes a model.
"""

from __future__ import annotations

import copy
import types

import pytest

from kravein import audit, config, usage, webauth

TOKEN = "test-token-models"
BROWSER = {"Authorization": f"Bearer {TOKEN}"}
GOOD = "gemini:gem-ok"
OTHER = "gemini:gem-untested"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(config, "DEFAULT_SETTINGS", copy.deepcopy(config.DEFAULT_SETTINGS))
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda ev, **kw: calls.append((ev, kw)))
    webauth.clear_lan_hosts()
    usage.update_entry(GOOD, as_of="2026-11-01", tool_calling="verified",
                       price={"input": 1.0, "output": 2.0}, secret_field="sk-leak")
    usage.update_entry(OTHER, as_of="2026-11-01", tool_calling="untested",
                       probe={"ok": False, "step": "call", "error": "HTTPError 404",
                              "api_key": "sk-leak"})
    return calls


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    return TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")


def test_requires_token(client):
    assert client.get("/api/models").status_code == 401
    assert client.put("/api/models/roles/utility", json={"chain": [GOOD]}).status_code == 401
    assert client.get("/api/usage").status_code == 401
    assert config.get("models.roles") in (None, {})


def test_read_models_whitelists_and_has_no_secret(client):
    got = client.get("/api/models", headers=BROWSER)
    assert got.status_code == 200
    body = got.json()
    assert body["catalog"][GOOD]["tool_calling"] == "verified"
    assert body["catalog"][OTHER]["probe"]["error"] == "HTTPError 404"
    assert "orchestrator" in body["roles"] and "utility" in body["roles"]
    assert "orchestrator" in body["agent_roles"]
    assert isinstance(body["warnings"], list) and isinstance(body["in_use"], dict)
    assert "sk-leak" not in got.text


def test_set_and_remove_role_chain(client, isolated):
    put = client.put("/api/models/roles/utility", headers=BROWSER, json={"chain": [OTHER, GOOD]})
    assert put.status_code == 200
    assert client.get("/api/models", headers=BROWSER).json()["chains"]["utility"] == [OTHER, GOOD]
    assert ("web.models_role_set", {"role": "utility", "chain": [OTHER, GOOD], "by": "web"}) in isolated
    # An empty list removes it: the role inherits again.
    assert client.put("/api/models/roles/utility", headers=BROWSER, json={"chain": []}).status_code == 200
    assert "utility" not in client.get("/api/models", headers=BROWSER).json()["chains"]


def test_unverified_first_choice_for_agent_role_is_400_with_reasons(client):
    got = client.put("/api/models/roles/orchestrator", headers=BROWSER, json={"chain": [OTHER]})
    assert got.status_code == 400
    assert got.json()["reasons"] and "probe" in got.json()["reasons"][0]
    assert "orchestrator" not in (config.get("models.roles") or {})


def test_bad_role_and_bad_body(client):
    assert client.put("/api/models/roles/nonsense", headers=BROWSER, json={"chain": [GOOD]}).status_code == 400
    assert client.put("/api/models/roles/utility", headers=BROWSER, json={"chain": "x"}).status_code == 400
    assert client.put("/api/models/roles/utility", headers=BROWSER, content=b"not json").status_code == 400
    bad = client.put("/api/models/roles/utility", headers=BROWSER, json={"chain": ["nope:model"]})
    assert bad.status_code == 400 and "unknown provider" in bad.json()["detail"]


def test_worker_role_with_colon_in_path(client):
    got = client.put("/api/models/roles/worker:research", headers=BROWSER, json={"chain": [GOOD]})
    assert got.status_code == 200
    assert client.get("/api/models", headers=BROWSER).json()["chains"]["worker:research"] == [GOOD]


def test_usage_rows_and_validation(client):
    usage.record(role="utility", provider="gemini", model="gem-ok", ok=True,
                 usage=types.SimpleNamespace(input=10, cached=0, output=5), latency_ms=1)
    got = client.get("/api/usage?days=30", headers=BROWSER)
    assert got.status_code == 200
    rows = got.json()["rows"]
    assert rows and rows[0]["role"] == "utility" and rows[0]["calls"] == 1
    assert client.get("/api/usage", headers=BROWSER).json()["days"] == 7
    for bad in ("abc", "0", "-1", "9999"):
        assert client.get(f"/api/usage?days={bad}", headers=BROWSER).status_code == 400
