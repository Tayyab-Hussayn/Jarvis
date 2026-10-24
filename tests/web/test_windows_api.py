"""/api/windows carries each window's exact id and terminal cwd (#87), so the
panel can target one of several same-app windows."""

from __future__ import annotations

from kravein import webauth
from kravein.platform import desktop

TOKEN = "test-token-windows"


class _Backend:
    name = "fake"

    def available(self):
        return True

    def list_windows(self):
        return [
            desktop.Window("0x1", "zsh", "foot", 11, "1", True),
            desktop.Window("0x2", "zsh", "foot", 12, "2", False),
        ]

    def list_workspaces(self):
        return ["1", "2"]


def test_snapshot_has_id_and_cwd(monkeypatch):
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    webauth.clear_lan_hosts()
    monkeypatch.setattr(desktop, "backend", lambda: _Backend())
    monkeypatch.setattr(
        desktop, "terminal_cwd", lambda w: {"0x1": "/home/u/a"}.get(w.id)
    )
    client = TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")
    data = client.get(
        "/api/windows", headers={"Authorization": f"Bearer {TOKEN}"}
    ).json()
    wins = {w["id"]: w for w in data["windows"]}
    assert set(wins) == {"0x1", "0x2"}
    assert wins["0x1"]["cwd"] == "/home/u/a"
    assert wins["0x2"]["cwd"] is None
