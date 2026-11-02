"""inputs.status() must not call a half-working input stack 'ready'."""
from __future__ import annotations

from kravein.platform import detect, inputs, shell


def test_wtype_fallback_reports_inactive_ydotool_daemon(monkeypatch):
    monkeypatch.setattr(shell, "have", lambda name: name in ("ydotool", "wtype"))
    monkeypatch.setattr(inputs, "_ydotool_socket", lambda: "")
    monkeypatch.setattr(detect, "environment", lambda: type("E", (), {"is_wayland": True})())
    text = inputs.status()
    assert "wtype" in text
    assert "ydotoold is not running" in text
    assert "systemctl --user enable --now ydotool.service" in text


def test_ydotool_ready_has_no_warning(monkeypatch):
    monkeypatch.setattr(shell, "have", lambda name: name == "ydotool")
    monkeypatch.setattr(inputs, "_ydotool_socket", lambda: "/run/user/1000/.ydotool_socket")
    assert inputs.status() == "Input synthesis ready via ydotool."
