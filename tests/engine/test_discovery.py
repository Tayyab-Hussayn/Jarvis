"""Dynamic LAN address + mDNS discovery (#105). Network, zeroconf and uvicorn stubbed."""
from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from kravein import audit, cli, config, discovery, server, service, webauth

WIFI, ETH, DOCKER = "192.168.1.20", "10.0.0.7", "172.17.0.1"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)


# -- address selection --------------------------------------------------------

def test_auto_single_candidate():
    assert discovery.pick_address("auto", [("wlan0", WIFI)], "wlan0") == WIFI


def test_auto_several_prefers_default_route():
    cands = [("eth0", ETH), ("wlan0", WIFI)]
    assert discovery.pick_address("auto", cands, "wlan0") == WIFI
    assert discovery.pick_address("auto", cands, "eth0") == ETH


def test_auto_several_without_default_route_refuses_to_guess():
    assert discovery.pick_address("auto", [("eth0", ETH), ("wlan0", WIFI)], None) is None


def test_no_candidates_is_none():
    assert discovery.pick_address("auto", [], "wlan0") is None


def test_held_explicit_address_is_kept_even_if_not_default_route():
    cands = [("eth0", ETH), ("wlan0", WIFI)]
    assert discovery.pick_address(ETH, cands, "wlan0") == ETH


def test_stale_address_is_treated_as_auto():
    # The owner's bug: web.env names the home address, the laptop is elsewhere.
    assert discovery.pick_address("192.168.100.239", [("wlan0", WIFI)], "wlan0") == WIFI


def test_interfaces_skip_virtual_loopback_and_link_local(monkeypatch):
    import psutil

    def addr(ip):
        return SimpleNamespace(family=socket.AF_INET, address=ip)

    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        "lo": [addr("127.0.0.1")],
        "docker0": [addr(DOCKER)],
        "veth12": [addr("172.18.0.1")],
        "wlan0": [addr(WIFI), SimpleNamespace(family=socket.AF_INET6, address="fe80::1")],
        "eth0": [addr("169.254.3.4")],
        "wan0": [addr("8.8.8.8")],
    })
    assert discovery.interface_addresses() == [("wlan0", WIFI)]


def test_default_route_lowest_metric_wins(tmp_path):
    table = tmp_path / "route"
    table.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t0100000A\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
        "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
        "wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n"
    )
    assert discovery.default_route_interface(str(table)) == "eth0"
    assert discovery.default_route_interface(str(tmp_path / "missing")) is None


# -- change detection ---------------------------------------------------------

def test_watcher_reports_a_change_once_and_ignores_steady_state():
    seen, current = [], {"ip": WIFI}
    watcher = discovery.AddressWatcher(
        "auto", WIFI, seen.append, pick=lambda pref: current["ip"]
    )
    assert watcher.check() is False and seen == []
    current["ip"] = ETH
    assert watcher.check() is True and seen == [ETH]
    assert watcher.check() is False and seen == [ETH]


def test_watcher_reports_network_loss_as_none():
    seen = []
    watcher = discovery.AddressWatcher("auto", WIFI, seen.append, pick=lambda pref: None)
    assert watcher.check() and seen == [None]


def test_watcher_probe_failure_is_not_a_change():
    def boom(pref):
        raise OSError("netlink hiccup")

    seen = []
    assert discovery.AddressWatcher("auto", WIFI, seen.append, pick=boom).check() is False
    assert seen == []


# -- mDNS ---------------------------------------------------------------------

class FakeZeroconf:
    """Stands in for the zeroconf module: records what would be announced."""
    instances: list = []

    class ServiceInfo:
        def __init__(self, type_, name, **kw):
            self.type, self.name, self.kw = type_, name, kw

    class Zeroconf:
        def __init__(self, interfaces=None):
            self.interfaces, self.registered, self.closed = interfaces, [], False
            FakeZeroconf.instances.append(self)

        def register_service(self, info):
            self.registered.append(info)

        def unregister_service(self, info):
            self.registered.remove(info)

        def close(self):
            self.closed = True


@pytest.fixture
def fakezc():
    FakeZeroconf.instances = []
    return FakeZeroconf


def test_advertise_announces_type_port_and_txt(fakezc):
    ad = discovery.Advertiser(8787, "ab" * 32, zeroconf_module=fakezc)
    assert ad.advertise(WIFI) is True
    zc = fakezc.instances[0]
    assert zc.interfaces == [WIFI]
    info = zc.registered[0]
    assert info.type == "_kravein._tcp.local."
    assert info.kw["port"] == 8787
    assert info.kw["properties"] == {"fp": "ab" * 8, "v": "1"}
    assert info.kw["parsed_addresses"] == [WIFI]


def test_readvertise_on_new_address_replaces_the_old_announcement(fakezc):
    ad = discovery.Advertiser(8787, "cd" * 32, zeroconf_module=fakezc)
    ad.advertise(WIFI)
    ad.advertise(ETH)
    old, new = fakezc.instances
    assert old.closed and old.registered == []
    assert new.interfaces == [ETH] and not new.closed


def test_advertise_degrades_when_mdns_unavailable():
    class Broken:
        class ServiceInfo:
            def __init__(self, *a, **k): ...

        class Zeroconf:
            def __init__(self, **k):
                raise OSError("no multicast")

    assert discovery.Advertiser(8787, "ef", zeroconf_module=Broken).advertise(WIFI) is False


def test_close_is_idempotent(fakezc):
    ad = discovery.Advertiser(8787, "ef", zeroconf_module=fakezc)
    ad.advertise(WIFI)
    ad.close()
    ad.close()
    assert fakezc.instances[0].closed


# -- the serving loop ---------------------------------------------------------

class FakeUvicorn:
    """Config/Server pair. Each run() ends only when told to, like the real one."""
    runs: list = []
    on_run = None

    class Config:
        def __init__(self, app, **kw):
            self.kw = kw

    class Server:
        def __init__(self, config):
            self.config, self.should_exit = config, False

        def run(self):
            FakeUvicorn.runs.append(self.config.kw["host"])
            FakeUvicorn.on_run(self, len(FakeUvicorn.runs))


def test_server_rebinds_on_address_change(monkeypatch):
    FakeUvicorn.runs = []
    watchers = []

    class ScriptedWatcher(discovery.AddressWatcher):
        def start(self):
            watchers.append(self)

    monkeypatch.setattr(discovery, "AddressWatcher", ScriptedWatcher)
    announced = []
    monkeypatch.setattr(discovery.Advertiser, "advertise",
                        lambda self, ip: announced.append(ip) or True)
    monkeypatch.setattr(discovery.Advertiser, "close", lambda self: None)

    def on_run(srv, n):
        if n == 1:
            watchers[-1].on_change(ETH)        # the network moved
            assert srv.should_exit
        # second run: exits on its own (Ctrl-C / SIGTERM path)

    FakeUvicorn.on_run = on_run
    app = SimpleNamespace(state=SimpleNamespace(public_host=WIFI))
    webauth.clear_lan_hosts()
    webauth.allow_lan_host(WIFI)
    try:
        rc = server._serve_following(FakeUvicorn, app, WIFI, 8787, {}, "ab" * 32, "auto")
        assert rc == 0
        assert FakeUvicorn.runs == [WIFI, ETH]
        assert announced == [WIFI, ETH]
        assert app.state.public_host == ETH
        # Host allowlist follows: new address in, old one out (rebinding defence).
        assert ETH in webauth.allowed_hostnames()
        assert WIFI not in webauth.allowed_hostnames()
    finally:
        webauth.clear_lan_hosts()


def test_server_without_follow_runs_once_and_does_not_watch(monkeypatch):
    FakeUvicorn.runs = []
    started = []
    monkeypatch.setattr(discovery.AddressWatcher, "start", lambda self: started.append(1))
    monkeypatch.setattr(discovery.Advertiser, "advertise", lambda self, ip: True)
    FakeUvicorn.on_run = lambda srv, n: None
    app = SimpleNamespace(state=SimpleNamespace(public_host=WIFI))
    assert server._serve_following(FakeUvicorn, app, WIFI, 8787, {}, None, None) == 0
    assert FakeUvicorn.runs == [WIFI] and started == []


# -- CLI + service ------------------------------------------------------------

def test_cli_host_auto_serves_the_current_address(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "port_busy", lambda port=service.PORT: False)
    monkeypatch.setattr(server, "serve", lambda **kw: calls.append(kw) or 0)
    monkeypatch.setattr(discovery, "interface_addresses", lambda: [("wlan0", WIFI)])
    monkeypatch.setattr(discovery, "default_route_interface", lambda *a: "wlan0")
    assert cli.main(["serve", "--lan", "--host", "auto", "--no-open"]) == 0
    assert calls[0]["host"] == WIFI and calls[0]["follow"] == "auto"


def test_cli_follow_with_stale_address_uses_the_real_one(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "port_busy", lambda port=service.PORT: False)
    monkeypatch.setattr(server, "serve", lambda **kw: calls.append(kw) or 0)
    monkeypatch.setattr(discovery, "interface_addresses", lambda: [("wlan0", WIFI)])
    monkeypatch.setattr(discovery, "default_route_interface", lambda *a: "wlan0")
    assert cli.main(["serve", "--lan", "--follow", "--host", "192.168.100.239",
                     "--no-open"]) == 0
    assert calls[0]["host"] == WIFI


def test_cli_auto_with_nowhere_to_serve_fails_so_systemd_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "port_busy", lambda port=service.PORT: False)
    monkeypatch.setattr(server, "serve", lambda **kw: calls.append(kw) or 0)
    monkeypatch.setattr(discovery, "interface_addresses", lambda: [])
    monkeypatch.setattr(discovery, "default_route_interface", lambda *a: None)
    assert cli.main(["serve", "--lan", "--host", "auto", "--no-open"]) == 1
    assert calls == []


def test_cli_still_refuses_second_server(monkeypatch):
    monkeypatch.setattr(service, "port_busy", lambda port=service.PORT: True)
    assert cli.main(["serve", "--lan", "--host", "auto", "--no-open"]) == 1


def test_service_current_host_resolves_auto_and_stale(tmp_path, monkeypatch):
    env = tmp_path / "web.env"
    monkeypatch.setattr(service, "web_env_path", lambda: env)
    monkeypatch.setattr(discovery, "interface_addresses", lambda: [("wlan0", WIFI)])
    monkeypatch.setattr(discovery, "default_route_interface", lambda *a: "wlan0")
    for value in ("auto", "192.168.100.239"):
        env.write_text(f"KRAVEIN_LAN_HOST={value}\n")
        assert service.current_host() == WIFI


def test_service_current_host_auto_without_network_says_so(tmp_path, monkeypatch):
    env = tmp_path / "web.env"
    env.write_text("KRAVEIN_LAN_HOST=auto\n")
    monkeypatch.setattr(service, "web_env_path", lambda: env)
    monkeypatch.setattr(discovery, "interface_addresses", lambda: [])
    monkeypatch.setattr(discovery, "default_route_interface", lambda *a: None)
    with pytest.raises(service.ServiceError):
        service.current_host()


def test_installer_and_unit_use_auto():
    root = config.BASE_DIR
    assert "KRAVEIN_LAN_HOST=auto" in (root / "install-linux.sh").read_text()
    unit = (root / "systemd" / "kravein-web.service").read_text()
    assert "--follow" in unit and "${KRAVEIN_LAN_HOST}" in unit
