"""`kravein start|stop|restart|status|pair|phones|open|logs`.

Manual on/off for the LAN web server, which runs as the `kravein-web` systemd
user unit (installed by `install-linux.sh`, never enabled: the owner starts it
when wanted). Everything here talks to that unit through `systemctl --user`
and to the running server over its own HTTP API with the owner token, so the
CLI shows the server's truth (who is connected, which mode is on), not a
second reading of the files.

All subprocesses go through `platform/shell.py` so the audit trail stays
complete.
"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from . import config, webauth
from .platform import shell

UNIT = "kravein-web.service"
PORT = 8787                       # fixed by the owner: one server, one port
START_WAIT_SECONDS = 20.0


class ServiceError(Exception):
    """Something the owner can act on; the message says what."""


# ---------------------------------------------------------------------------
# The unit
# ---------------------------------------------------------------------------

def web_env_path():
    return config.CONFIG_DIR / "web.env"


def unit_path():
    return config.XDG_CONFIG_HOME / "systemd" / "user" / UNIT


def _env_value(name: str) -> str:
    try:
        lines = web_env_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip("'\"")
    return ""


def lan_host() -> str:
    """The address the unit binds, from web.env ("" when not set up)."""
    return _env_value("KRAVEIN_LAN_HOST")


def port_busy(port: int = PORT) -> bool:
    """True when anything is listening on `port` on any local address.

    Asked before binding rather than by catching the crash: a server bound to
    the LAN address is invisible to a connect on 127.0.0.1, and the unit would
    only report "failed" a few seconds later. Binding the wildcard address
    conflicts with a listener on any specific address; SO_REUSEADDR keeps a
    connection in TIME_WAIT from counting as a second server.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


def already_running_message(port: int = PORT) -> str:
    return (f"Kravein server is already running on port {port}. Close it first "
            "(kravein stop, or stop that process) to start a new one.")


def _installer_hint() -> str:
    return f"{config.BASE_DIR / 'install-linux.sh'}   (answer y at the LAN web UI step)"


def require_installed() -> str:
    """The bound host, or ServiceError naming the one command that fixes it."""
    if not unit_path().is_file() or not lan_host():
        raise ServiceError(
            "The web service is not installed (no kravein-web unit or web.env). Run:\n"
            f"  {_installer_hint()}"
        )
    return current_host()


def current_host() -> str:
    """The address the server is on *now*: web.env may say `auto`, or name an
    address this machine no longer holds (the server follows the network and
    re-binds within seconds, so the CLI must look where it is today)."""
    from . import discovery

    configured = lan_host()
    try:
        resolved = discovery.pick_address(configured)
    except Exception:
        resolved = None
    if resolved:
        return resolved
    if configured == discovery.AUTO:
        raise ServiceError("No usable private LAN address right now. Connect to a Wi-Fi/Ethernet network.")
    return configured


def _systemctl(*args: str, timeout: float = 30) -> shell.Result:
    return shell.run(["systemctl", "--user", *args], timeout=timeout, audit_event="service.systemctl")


def unit_state() -> dict[str, Any]:
    """ActiveState and uptime seconds (None unless active)."""
    res = _systemctl("show", UNIT, "-p", "ActiveState", "-p", "ActiveEnterTimestampMonotonic")
    props = dict(line.partition("=")[::2] for line in res.stdout.splitlines())
    active = props.get("ActiveState", "unknown")
    uptime = None
    if active == "active":
        try:
            # systemd's monotonic clock is CLOCK_MONOTONIC, as is Python's.
            uptime = max(0.0, time.monotonic() - int(props["ActiveEnterTimestampMonotonic"]) / 1e6)
        except (KeyError, ValueError):
            pass
    return {"active": active, "uptime": uptime}


# ---------------------------------------------------------------------------
# The server's HTTP API
# ---------------------------------------------------------------------------

def _scheme() -> str:
    return "https" if (webauth.tls_dir() / "cert.pem").is_file() else "http"


def base_url(host: str) -> str:
    return f"{_scheme()}://{host}:{PORT}"


def _ssl_context() -> ssl.SSLContext:
    """Trust exactly the server's own certificate and nothing else.

    The certificate is self-signed, so the system store would reject it and
    disabling verification would let anything on the LAN answer with the
    owner token in the request. Pinning the one cert on disk avoids both.
    """
    ctx = ssl.create_default_context(cafile=str(webauth.tls_dir() / "cert.pem"))
    ctx.check_hostname = False        # the pin is the identity; the SAN is an IP
    return ctx


def api(host: str, method: str, path: str, body: dict | None = None, timeout: float = 5.0) -> Any:
    """Call the running server with the owner token. Raises ServiceError."""
    token = webauth.read_token()
    if not token:
        raise ServiceError("No server token yet; start the server first (kravein start).")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base_url(host) + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    context = _ssl_context() if _scheme() == "https" else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise ServiceError(f"{method} {path} -> HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ssl.SSLError, ValueError) as exc:
        raise ServiceError(f"server not reachable at {base_url(host)} ({exc})") from exc


def _wait_answering(host: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            api(host, "GET", "/api/phones", timeout=2.0)
            return True
        except ServiceError:
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else f"{seconds}s"


def expires_in(expires_at: float, now: float | None = None) -> str:
    left = expires_at - (now if now is not None else time.time())
    if left <= 0:
        return "expired"
    days = int(left // 86400)
    return f"expires in {days} days" if days >= 1 else f"expires in {_duration(left)}"


def _phone_summary(devices: list[dict]) -> str:
    return "paired" if devices else "not paired"


def _device_line(dev: dict) -> str:
    live = "connected" if dev.get("connected") else "offline"
    return f"{dev['name'] or '(unnamed)'}  [{dev['device_id']}]  {live}, {expires_in(dev.get('expires_at', 0))}"


# ---------------------------------------------------------------------------
# Commands (each returns an exit code)
# ---------------------------------------------------------------------------

def start() -> int:
    host = require_installed()
    if port_busy():
        print(already_running_message())
        return 1
    res = _systemctl("start", UNIT)
    if not res.ok:
        print(f"Could not start {UNIT}: {res.output}")
        print("  kravein logs")
        return 1
    if not _wait_answering(host, START_WAIT_SECONDS):
        print(f"{UNIT} started but is not answering on {base_url(host)}.")
        print("  kravein logs")
        return 1
    devices = api(host, "GET", "/api/phones").get("devices", [])
    print(f"Kravein is running: {base_url(host)}")
    print(f"phone: {_phone_summary(devices)}")
    if not devices:
        print("  kravein pair   to connect one")
    return 0


def stop() -> int:
    require_installed()
    res = _systemctl("stop", UNIT)
    if not res.ok:
        print(f"Could not stop {UNIT}: {res.output}")
        return 1
    print("Kravein web server stopped.")
    return 0


def restart() -> int:
    host = require_installed()
    res = _systemctl("restart", UNIT)
    if not res.ok:
        print(f"Could not restart {UNIT}: {res.output}")
        return 1
    if not _wait_answering(host, START_WAIT_SECONDS):
        print("Restarted but not answering yet. kravein logs")
        return 1
    print(f"Kravein is running: {base_url(host)}")
    return 0


def status() -> int:
    try:
        host = require_installed()
    except ServiceError as exc:
        print(exc)
        return 3
    state = unit_state()
    if state["active"] != "active":
        print(f"Kravein web server: stopped ({state['active']})")
        print("  kravein start")
        return 3
    line = f"Kravein web server: running at {base_url(host)}"
    if lan_host() == "auto":
        line += "  [address follows the network]"
    if state["uptime"] is not None:
        line += f"  (up {_duration(state['uptime'])})"
    print(line)
    try:
        devices = api(host, "GET", "/api/phones").get("devices", [])
        policy = api(host, "GET", "/api/safety/policy")
    except ServiceError as exc:
        print(f"  running, but the API did not answer: {exc}")
        return 1
    print(f"phone: {_phone_summary(devices)}")
    for dev in devices:
        print(f"  {_device_line(dev)}")
    mode = policy.get("mode", "ask")
    extra = ""
    if mode == "auto":
        extra = f" (auto-approve ON, {_duration(policy.get('seconds_left', 0))} left)"
    print(f"confirmations: {mode}{extra}")
    # Said plainly: a private LAN address is not reachable from mobile data.
    print("reach: phones on the same Wi-Fi/LAN only (found automatically over mDNS)")
    return 0


def phones(action: str = "list", target: str = "") -> int:
    host = require_installed()
    devices = api(host, "GET", "/api/phones").get("devices", [])
    if action == "list":
        if not devices:
            print("No paired phones. kravein pair")
        for dev in devices:
            print(_device_line(dev))
        return 0

    # Never the first match: the same phone name can be paired twice.
    exact = [d for d in devices if target in (d["device_id"], d["name"])]
    if not exact:
        exact = [d for d in devices if target and target.lower() in d["name"].lower()]
    if not exact:
        print(f"No paired phone matches '{target}'.")
        return 1
    if len(exact) > 1:
        print(f"'{target}' matches {len(exact)} phones; remove by id:")
        for dev in exact:
            print(f"  {_device_line(dev)}")
        return 1
    api(host, "DELETE", f"/api/phones/{exact[0]['device_id']}")
    print(f"Removed {exact[0]['name'] or exact[0]['device_id']}.")
    return 0


def pair(render_qr, *, sleep=time.sleep) -> int:
    """Show a pairing QR; refresh it as it expires until a phone appears."""
    host = require_installed()
    before = {d["device_id"] for d in api(host, "GET", "/api/phones").get("devices", [])}
    try:
        while True:
            res = api(host, "POST", "/api/phone/offer", {})
            offer = res["offer"]
            if not res.get("reachable_from_lan", True):
                print("The server is bound to a loopback address; a phone cannot reach it.")
                return 1
            print(f"Scan with the Kravein app:  {offer['host']}:{offer['port']}  code {offer['code']}")
            render_qr(json.dumps(offer))
            # Poll until the code expires; a new device ends the loop.
            while time.time() < offer["expires_at"]:
                sleep(2)
                now = {d["device_id"]: d for d in api(host, "GET", "/api/phones").get("devices", [])}
                new = [d for i, d in now.items() if i not in before]
                if new:
                    print(f"Paired: {new[0]['name'] or new[0]['device_id']}")
                    return 0
            print("Code expired, making a new one (Ctrl-C to stop)...")
    except KeyboardInterrupt:
        print()
        return 130


def open_ui(opener=None) -> int:
    host = require_installed()
    if unit_state()["active"] != "active":
        print("The server is not running. kravein start")
        return 3
    token = webauth.read_token()
    if not token:
        print("No server token yet. kravein restart")
        return 1
    if opener is None:
        import webbrowser

        opener = webbrowser.open
    opener(f"{base_url(host)}/?token={token}")
    # The token stays out of the terminal and its scrollback.
    print(f"Opened {base_url(host)} in your browser.")
    return 0


def logs(follow: bool = False, lines: int = 50, *, sleep=time.sleep) -> int:
    base = ["journalctl", "--user", "-u", UNIT, "--no-pager", "--show-cursor"]

    def fetch(extra: list[str]) -> tuple[str, str]:
        res = shell.run([*base, *extra], timeout=15, audit_event="service.logs")
        cursor, body = "", []
        for line in res.stdout.splitlines():
            if line.startswith("-- cursor:"):
                cursor = line.split(":", 1)[1].strip()
            else:
                body.append(line)
        return cursor, "\n".join(body)

    cursor, text = fetch(["-n", str(lines)])
    if text:
        print(text)
    if not follow:
        return 0
    try:
        while True:
            sleep(1)
            extra = [f"--after-cursor={cursor}"] if cursor else ["-n", "0"]
            new_cursor, text = fetch(extra)
            cursor = new_cursor or cursor
            if text.strip():
                print(text)
    except KeyboardInterrupt:
        return 0
