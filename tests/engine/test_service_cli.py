"""`kravein start|stop|status|pair|phones|open|logs` (#76), systemctl and HTTP stubbed."""

import socket
import time

import pytest

from kravein import cli, service
from kravein.platform import shell


@pytest.fixture
def installed(tmp_path, monkeypatch):
    unit = tmp_path / "kravein-web.service"
    unit.write_text("[Unit]\n")
    env = tmp_path / "web.env"
    env.write_text("KRAVEIN_LAN_HOST=192.168.1.20\n")
    monkeypatch.setattr(service, "unit_path", lambda: unit)
    monkeypatch.setattr(service, "web_env_path", lambda: env)
    monkeypatch.setattr(service, "_scheme", lambda: "https")
    return tmp_path


def _res(stdout="", rc=0):
    return shell.Result(["x"], rc, stdout, "")


class Api:
    """Scripted server: records calls, answers from a table."""

    def __init__(self, devices=None, policy=None):
        self.devices = devices if devices is not None else []
        self.policy = policy or {"mode": "ask"}
        self.calls = []

    def __call__(self, host, method, path, body=None, timeout=5.0):
        self.calls.append((method, path))
        if path == "/api/phones" and method == "GET":
            return {"devices": self.devices}
        if path == "/api/safety/policy":
            return self.policy
        if path == "/api/phone/offer":
            return {"offer": {"host": host, "port": 8787, "code": "abc",
                              "expires_at": time.time() + 60}, "reachable_from_lan": True}
        if method == "DELETE":
            return {}
        raise AssertionError(path)


def dev(i, name, days=10, connected=True):
    return {"device_id": i, "name": name, "connected": connected,
            "expires_at": time.time() + days * 86400 + 60}


def test_port_busy_sees_a_listener_on_a_specific_address():
    # The server binds the LAN address; the probe must still see it.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    try:
        assert service.port_busy(port) is True
    finally:
        srv.close()
    assert service.port_busy(port) is False


def test_start_refuses_when_port_busy(installed, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(service, "port_busy", lambda port=service.PORT: True)
    monkeypatch.setattr(service, "_systemctl", lambda *a, **k: called.append(a))
    assert cli.main(["start"]) == 1
    out = capsys.readouterr().out
    assert "already running on port 8787" in out and "Close it first" in out
    assert called == []                      # refused before touching the unit


def test_serve_refuses_when_port_busy_before_binding(monkeypatch, capsys):
    import kravein.server as server
    started = []
    monkeypatch.setattr(service, "port_busy", lambda port=service.PORT: port == 8787)
    monkeypatch.setattr(server, "serve", lambda **kw: started.append(kw) or 0)
    for argv in (["serve"], ["serve", "--lan", "--host", "192.168.1.20"]):
        assert cli.main(argv) == 1
        assert "already running on port 8787" in capsys.readouterr().out
    assert started == []


def test_serve_starts_when_port_free(monkeypatch):
    import kravein.server as server
    started = []
    monkeypatch.setattr(service, "port_busy", lambda port=service.PORT: False)
    monkeypatch.setattr(server, "serve", lambda **kw: started.append(kw) or 0)
    assert cli.main(["serve", "--no-open"]) == 0
    assert started and started[0]["port"] == 8787



def test_not_installed_prints_installer_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(service, "unit_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(service, "web_env_path", lambda: tmp_path / "nope.env")
    called = []
    monkeypatch.setattr(service, "_systemctl", lambda *a, **k: called.append(a))
    assert cli.main(["start"]) == 1
    assert "install-linux.sh" in capsys.readouterr().out
    assert called == []


def test_start_waits_then_prints_url_and_phone_state(installed, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(service, "_systemctl", lambda *a, **k: calls.append(a) or _res())
    monkeypatch.setattr(service, "api", Api(devices=[]))
    assert service.start() == 0
    out = capsys.readouterr().out
    assert calls == [("start", service.UNIT)]
    assert "https://192.168.1.20:8787" in out
    assert "phone: not paired" in out
    assert "token" not in out


def test_start_reports_unit_failure(installed, monkeypatch, capsys):
    monkeypatch.setattr(service, "_systemctl", lambda *a, **k: _res(rc=1))
    assert service.start() == 1
    assert "kravein logs" in capsys.readouterr().out


def test_status_stopped(installed, monkeypatch, capsys):
    monkeypatch.setattr(service, "_systemctl",
                        lambda *a, **k: _res("ActiveState=inactive\n"))
    assert service.status() == 3
    assert "stopped" in capsys.readouterr().out


def test_status_running_lists_phones_and_auto_mode(installed, monkeypatch, capsys):
    mono = int((time.monotonic() - 3700) * 1e6)
    monkeypatch.setattr(
        service, "_systemctl",
        lambda *a, **k: _res(f"ActiveState=active\nActiveEnterTimestampMonotonic={mono}\n"))
    monkeypatch.setattr(service, "api", Api(
        devices=[dev("d1", "Pixel", days=13)],
        policy={"mode": "auto", "seconds_left": 600}))
    assert service.status() == 0
    out = capsys.readouterr().out
    assert "up 1h 1m" in out
    assert "phone: paired" in out
    assert "Pixel" in out and "expires in 13 days" in out
    assert "auto-approve ON" in out


def test_phones_remove_refuses_ambiguous_name(installed, monkeypatch, capsys):
    api = Api(devices=[dev("d1", "Pixel"), dev("d2", "Pixel")])
    monkeypatch.setattr(service, "api", api)
    assert service.phones("remove", "Pixel") == 1
    out = capsys.readouterr().out
    assert "d1" in out and "d2" in out
    assert ("DELETE", "/api/phones/d1") not in api.calls


def test_phones_remove_by_id_and_unknown(installed, monkeypatch, capsys):
    api = Api(devices=[dev("d1", "Pixel"), dev("d2", "Pixel")])
    monkeypatch.setattr(service, "api", api)
    assert service.phones("remove", "d2") == 0
    assert ("DELETE", "/api/phones/d2") in api.calls
    assert service.phones("remove", "nokia") == 1


def test_cli_requires_target_for_remove(installed, capsys):
    assert cli.main(["phones", "remove"]) == 2


def test_pair_renders_qr_json_and_ends_when_phone_appears(installed, monkeypatch, capsys):
    api = Api(devices=[])
    monkeypatch.setattr(service, "api", api)
    shown = []

    def sleep(_):
        api.devices = [dev("d9", "New phone")]

    assert service.pair(shown.append, sleep=sleep) == 0
    assert '"code": "abc"' in shown[0]
    assert "Paired: New phone" in capsys.readouterr().out


def test_pair_refreshes_expired_code(installed, monkeypatch):
    api = Api(devices=[])
    offers = []
    real = api.__call__

    def counting(host, method, path, body=None, timeout=5.0):
        if path == "/api/phone/offer":
            offers.append(1)
            # already expired: forces a refresh without waiting
            return {"offer": {"host": host, "port": 1, "code": "c",
                              "expires_at": time.time() - 1}, "reachable_from_lan": True}
        return real(host, method, path, body, timeout)

    def stop_after_two(_):
        pass

    monkeypatch.setattr(service, "api", counting)

    def render(_):
        if len(offers) == 2:
            raise KeyboardInterrupt

    assert service.pair(render, sleep=stop_after_two) == 130
    assert len(offers) == 2


def test_open_does_not_print_token(installed, monkeypatch, capsys):
    monkeypatch.setattr(service, "_systemctl", lambda *a, **k: _res("ActiveState=active\n"))
    monkeypatch.setattr(service.webauth, "read_token", lambda: "SECRET")
    opened = []
    assert service.open_ui(opened.append) == 0
    assert opened == ["https://192.168.1.20:8787/?token=SECRET"]
    assert "SECRET" not in capsys.readouterr().out


def test_logs_uses_journal_through_shell(monkeypatch, capsys):
    seen = []

    def fake_run(args, **kw):
        seen.append(args)
        return _res("line one\n-- cursor: s=1\n")

    monkeypatch.setattr(shell, "run", fake_run)
    assert service.logs(False, 5) == 0
    assert seen[0][:4] == ["journalctl", "--user", "-u", service.UNIT]
    assert capsys.readouterr().out == "line one\n"


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for word in ("start", "stop", "restart", "status", "pair", "phones", "open", "logs"):
        assert word in out


def test_expires_in_formats():
    assert service.expires_in(1000 + 3 * 86400 + 5, now=1000) == "expires in 3 days"
    assert service.expires_in(1000 + 7200, now=1000) == "expires in 2h 0m"
    assert service.expires_in(999, now=1000) == "expired"
