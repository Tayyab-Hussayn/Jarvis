"""Which LAN address Kravein serves on right now, and telling phones about it.

The laptop moves between networks, so an address written into `web.env` goes
stale the first time the Wi-Fi changes. `KRAVEIN_LAN_HOST=auto` (or an address
this machine no longer holds) means "whatever private address I have now".
Phones find the server again over mDNS (`_kravein._tcp`); the TLS certificate
is not tied to the address and the phone pins its fingerprint, so a new
address needs no re-pairing.

Nothing here spawns a process: interfaces come from psutil and the default
route from /proc/net/route.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from typing import Callable

from . import audit, webauth

AUTO = "auto"
SERVICE_TYPE = "_kravein._tcp.local."
POLL_SECONDS = 5.0

# Container and VM bridges hold private addresses but no phone can reach the
# server through them; offering one made docker0 (172.17.0.1) the owner's
# address once. Same list as the installer's filter.
_VIRTUAL_PREFIXES = (
    "docker", "br-", "veth", "virbr", "vmnet", "vboxnet", "lxcbr", "lxdbr",
    "podman", "cni", "flannel", "cali", "tun", "tap",
)


def is_virtual_interface(name: str) -> bool:
    return name.startswith(_VIRTUAL_PREFIXES)


def interface_addresses() -> list[tuple[str, str]]:
    """(interface, address) for each real private IPv4 address held now."""
    import psutil

    found: list[tuple[str, str]] = []
    for iface, addrs in psutil.net_if_addrs().items():
        if is_virtual_interface(iface):
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(addr.address)
            # Link-local (169.254/16) is what an interface holds *without* a
            # DHCP lease: no phone is on that network.
            if webauth.is_private_address(addr.address) and not ip.is_loopback \
                    and not ip.is_link_local:
                found.append((iface, addr.address))
    return sorted(found, key=lambda pair: pair[1])


def default_route_interface(route_file: str = "/proc/net/route") -> str | None:
    """The interface carrying the default route (lowest metric), if any."""
    try:
        with open(route_file, encoding="ascii") as fh:
            rows = fh.read().splitlines()[1:]
    except OSError:
        return None
    best: tuple[int, str] | None = None
    for row in rows:
        fields = row.split()
        # Iface Destination Gateway Flags RefCnt Use Metric Mask ...
        if len(fields) < 8 or fields[1] != "00000000" or fields[7] != "00000000":
            continue
        if not int(fields[3], 16) & 0x2:          # RTF_GATEWAY
            continue
        metric = int(fields[6])
        if best is None or metric < best[0]:
            best = (metric, fields[0])
    return best[1] if best else None


def pick_address(
    preference: str,
    candidates: list[tuple[str, str]] | None = None,
    default_iface: str | None = None,
) -> str | None:
    """The address to serve on, or None when there is no safe choice.

    `preference` is `auto` or an address. An address still held (and real) is
    honoured; one that is not held is stale and treated as `auto`. With several
    candidates the one on the default-route interface wins; with no default
    route and several candidates there is no way to tell which network the
    phone is on, so None — never the first one.
    """
    if candidates is None:
        candidates = interface_addresses()
    if default_iface is None:
        default_iface = default_route_interface()
    held = [ip for _, ip in candidates]
    if preference != AUTO and preference in held:
        return preference
    if len(candidates) == 1:
        return candidates[0][1]
    for iface, ip in candidates:
        if iface == default_iface:
            return ip
    return None


class AddressWatcher:
    """Notices the address changing while the server runs.

    `check()` is the whole logic (tests call it directly); `start()` just runs
    it every few seconds on a daemon thread. `on_change(new)` gets the new
    address, or None when the network went away.
    """

    def __init__(
        self,
        preference: str,
        current: str | None,
        on_change: Callable[[str | None], None],
        *,
        pick: Callable[[str], str | None] = pick_address,
        interval: float = POLL_SECONDS,
    ) -> None:
        self.preference = preference
        self.current = current
        self.on_change = on_change
        self._pick = pick
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> bool:
        try:
            new = self._pick(self.preference)
        except Exception:
            return False                       # a failed probe is not a change
        if new == self.current:
            return False
        self.current = new
        self.on_change(new)
        return True

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if self.check():
                return                          # one change per watcher

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="addr-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class Advertiser:
    """Announces `_kravein._tcp` on the current address (zeroconf).

    Degrades quietly: without the package, or when mDNS cannot start, the
    server still works and the phone falls back to its last known address.
    """

    def __init__(self, port: int, fingerprint: str | None, *, zeroconf_module=None) -> None:
        self.port = port
        self.fp = (fingerprint or "")[:16]
        self._zc_mod = zeroconf_module
        self._zc = None
        self._info = None

    def _module(self):
        if self._zc_mod is None:
            import zeroconf
            self._zc_mod = zeroconf
        return self._zc_mod

    def advertise(self, address: str) -> bool:
        """(Re-)announce on `address`. Returns False when mDNS is unavailable."""
        self.close()
        try:
            zc = self._module()
            host = socket.gethostname().split(".")[0] or "kravein"
            info = zc.ServiceInfo(
                SERVICE_TYPE,
                f"Kravein-{host}.{SERVICE_TYPE}",
                port=self.port,
                properties={"fp": self.fp, "v": "1"},
                parsed_addresses=[address],
                server=f"{host}.local.",
            )
            # Bound to this interface only: announcing on a docker bridge or a
            # second network would advertise an address nobody can use.
            instance = zc.Zeroconf(interfaces=[address])
            instance.register_service(info)
        except Exception as exc:               # ImportError, OSError, zeroconf's own
            audit.record("discovery.unavailable", error=f"{type(exc).__name__}: {exc}")
            self._zc = self._info = None
            return False
        self._zc, self._info = instance, info
        audit.record("discovery.advertised", address=address, port=self.port)
        return True

    def close(self) -> None:
        zc, info = self._zc, self._info
        self._zc = self._info = None
        if zc is None:
            return
        try:
            zc.unregister_service(info)
            zc.close()
        except Exception:
            pass
