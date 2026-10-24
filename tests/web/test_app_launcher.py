"""Launch counts behind the per-workspace app launcher (#94): only installed
entries are counted, the list is frequent-first, and the file survives junk."""

from __future__ import annotations

import json
from pathlib import Path

from kravein import config, server, webauth
from kravein.platform import apps as apps_platform

TOKEN = "test-token-launcher"


def _entry(name: str, hidden: bool = False) -> apps_platform.DesktopEntry:
    return apps_platform.DesktopEntry(
        name=name, exec_cmd=name.lower(), path=Path(f"/usr/share/applications/{name}.desktop"),
        no_display=hidden,
    )


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        apps_platform, "index",
        lambda: [_entry("Firefox"), _entry("Foot"), _entry("Blender"), _entry("Hidden", True)],
    )


def _get(monkeypatch):
    from fastapi.testclient import TestClient

    webauth.clear_lan_hosts()
    client = TestClient(server.create_app(TOKEN), base_url="http://127.0.0.1:8787")
    return client.get("/api/apps", headers={"Authorization": f"Bearer {TOKEN}"}).json()


def test_counts_only_installed_entries(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    server._record_launch("Firefox")
    server._record_launch("Firefox")
    server._record_launch("firefox")        # not an exact entry name
    server._record_launch("Nonexistent")
    server._record_launch("Hidden")         # NoDisplay entries are not offered
    assert json.loads((tmp_path / "app_launches.json").read_text()) == {"Firefox": 2}


def test_frequent_first_and_capped(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for name, n in (("Foot", 3), ("Firefox", 1), ("Blender", 3)):
        for _ in range(n):
            server._record_launch(name)
    data = _get(monkeypatch)
    # ties break alphabetically
    assert data["frequent"] == ["Blender", "Foot", "Firefox"]
    by = {a["name"]: a for a in data["apps"]}
    assert by["Foot"]["launches"] == 3
    assert by["Foot"]["id"] == "Foot.desktop" and by["Foot"]["exec"] == "foot"
    assert by["Foot"]["icon"] == ""
    assert "Hidden" not in by

    monkeypatch.setattr(server, "FREQUENT_APPS", 2)
    assert len(_get(monkeypatch)["frequent"]) == 2


def test_persists_and_tolerates_a_corrupt_file(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    server._record_launch("Foot")
    assert server._launch_counts() == {"Foot": 1}       # re-read from disk
    (tmp_path / "app_launches.json").write_text("{not json")
    assert server._launch_counts() == {}
    assert _get(monkeypatch)["frequent"] == []
    server._record_launch("Foot")                        # recovers by rewriting
    assert server._launch_counts() == {"Foot": 1}
    (tmp_path / "app_launches.json").write_text(json.dumps({"Foot": "x", "A": -1, "B": True}))
    assert server._launch_counts() == {}


def test_open_app_run_records_a_launch_and_needs_a_token(monkeypatch, tmp_path):
    """The ws `run` path counts a successful open_app; the mocked skill stands
    in for the real skill (#93/#95 cover open_app itself)."""
    from fastapi.testclient import TestClient

    _setup(monkeypatch, tmp_path)
    seen = {}

    def fake_dispatch(name, args, **kw):
        seen.update(name=name, args=args)
        return "Opened Foot on workspace 2."

    monkeypatch.setattr(server.registry, "dispatch", fake_dispatch)
    webauth.clear_lan_hosts()
    client = TestClient(server.create_app(TOKEN), base_url="http://127.0.0.1:8787")
    assert client.get("/api/apps").status_code in (401, 403)
    with client.websocket_connect(f"/ws?token={TOKEN}", headers={"Host": "127.0.0.1:8787"}) as ws:
        ws.receive_json()   # hello
        ws.send_json({"t": "run", "id": "p1", "skill": "open_app",
                      "args": {"app_name": "Foot", "workspace": "2"}})
        for _ in range(20):
            if ws.receive_json().get("t") == "turn_end":
                break
    assert seen["args"] == {"app_name": "Foot", "workspace": "2"}
    assert server._launch_counts() == {"Foot": 1}
