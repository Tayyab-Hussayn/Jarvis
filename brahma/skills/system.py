"""System status, processes and services."""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timedelta

from ..platform import detect, power, shell
from ..registry import skill
from ..safety import Danger, check_process, describe_targets, enforce

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def _need_psutil() -> None:
    if psutil is None:
        raise RuntimeError("psutil is not installed. Run: pip install psutil")


def _human_bytes(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} EB"


@skill(category="system", danger=Danger.SAFE)
def system_status(detailed: bool = False) -> str:
    """Report overall machine health: CPU, memory, disk, uptime and load.

    Args:
        detailed: Include per-core CPU, temperatures and network counters.
    """
    _need_psutil()
    env = detect.environment()
    lines = [f"{env.distro} · kernel {env.kernel}", f"Desktop: {env.desktop} ({env.session_type})"]

    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = timedelta(seconds=int(time.time() - psutil.boot_time()))
    lines.append(f"Uptime: {uptime} (booted {boot:%Y-%m-%d %H:%M})")

    cpu = psutil.cpu_percent(interval=0.4)
    cores = psutil.cpu_count(logical=True)
    try:
        load = os.getloadavg()
        load_text = f", load {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}"
    except OSError:
        load_text = ""
    lines.append(f"CPU: {cpu:.0f}% across {cores} threads{load_text}")

    mem = psutil.virtual_memory()
    lines.append(
        f"Memory: {_human_bytes(mem.used)} / {_human_bytes(mem.total)} ({mem.percent:.0f}%)"
    )
    swap = psutil.swap_memory()
    if swap.total:
        lines.append(
            f"Swap: {_human_bytes(swap.used)} / {_human_bytes(swap.total)} ({swap.percent:.0f}%)"
        )

    for mount in ("/", str(os.path.expanduser("~"))):
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        pct = usage.used / usage.total * 100 if usage.total else 0
        lines.append(
            f"Disk {mount}: {_human_bytes(usage.used)} / {_human_bytes(usage.total)} "
            f"({pct:.0f}%, {_human_bytes(usage.free)} free)"
        )

    battery = power.battery()
    if "No battery" not in battery:
        lines.append(battery)

    if detailed:
        per_core = psutil.cpu_percent(interval=0.4, percpu=True)
        lines.append("Per-core: " + " ".join(f"{c:.0f}%" for c in per_core))

        try:
            temps = psutil.sensors_temperatures()
        except (AttributeError, OSError):
            temps = {}
        for chip, readings in (temps or {}).items():
            hottest = max((r.current for r in readings if r.current), default=None)
            if hottest:
                lines.append(f"Temp {chip}: {hottest:.0f}°C")

        net = psutil.net_io_counters()
        lines.append(
            f"Network: {_human_bytes(net.bytes_recv)} in / {_human_bytes(net.bytes_sent)} out"
        )

    return "\n".join(lines)


@skill(category="system", danger=Danger.SAFE)
def list_processes(sort_by: str = "cpu", limit: int = 15, filter_name: str = "") -> str:
    """List running processes.

    Args:
        sort_by: 'cpu', 'memory' or 'name'.
        limit: How many to show.
        filter_name: Only show processes whose name contains this text.
    """
    _need_psutil()
    limit = max(1, min(int(limit), 100))
    needle = (filter_name or "").lower()

    # First call primes the CPU counters; the interval gives a real reading.
    psutil.cpu_percent(interval=None)
    procs = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "status"]):
        try:
            info = proc.info
            if needle and needle not in (info.get("name") or "").lower():
                continue
            procs.append((proc, info))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(0.3)
    rows = []
    for proc, info in procs:
        try:
            cpu = proc.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            cpu = 0.0
        mem = (info.get("memory_info").rss if info.get("memory_info") else 0)
        rows.append({
            "pid": info.get("pid"),
            "name": info.get("name") or "?",
            "user": info.get("username") or "?",
            "cpu": cpu,
            "mem": mem,
            "status": info.get("status") or "?",
        })

    key = {"cpu": lambda r: -r["cpu"], "memory": lambda r: -r["mem"],
           "name": lambda r: r["name"].lower()}.get(sort_by, lambda r: -r["cpu"])
    rows.sort(key=key)
    rows = rows[:limit]

    if not rows:
        return f"No processes matched '{filter_name}'." if needle else "No processes found."

    header = f"{'PID':>7}  {'CPU%':>6}  {'MEM':>9}  {'USER':<12} NAME"
    body = "\n".join(
        f"{r['pid']:>7}  {r['cpu']:>6.1f}  {_human_bytes(r['mem']):>9}  "
        f"{r['user'][:12]:<12} {r['name']}"
        for r in rows
    )
    return f"Top {len(rows)} process(es) by {sort_by}:\n{header}\n{body}"


@skill(category="system", danger=Danger.SAFE)
def find_process(name: str) -> str:
    """Find running processes by name and show their PIDs.

    Args:
        name: Process name or fragment to search for.
    """
    _need_psutil()
    needle = (name or "").lower().strip()
    if not needle:
        return "Give a process name to search for."

    matches = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "username", "create_time"]):
        try:
            info = proc.info
            pname = (info.get("name") or "").lower()
            cmdline = " ".join(info.get("cmdline") or [])
            if needle in pname or needle in cmdline.lower():
                matches.append((info, cmdline))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not matches:
        return f"No running process matches '{name}'."

    lines = [f"{len(matches)} process(es) matching '{name}':"]
    for info, cmdline in matches[:25]:
        started = datetime.fromtimestamp(info.get("create_time", 0))
        lines.append(
            f"  PID {info['pid']:<7} {info.get('name')} "
            f"(user {info.get('username')}, since {started:%H:%M})"
        )
        if cmdline and cmdline != info.get("name"):
            lines.append(f"      {cmdline[:120]}")
    return "\n".join(lines)


@skill(category="system", danger=Danger.DESTRUCTIVE)
def kill_process(
    target: str,
    force: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Terminate a process by PID or name. Asks for confirmation first.

    Args:
        target: A numeric PID, or a process name to match.
        force: Send SIGKILL instead of SIGTERM.
    """
    _need_psutil()
    target = (target or "").strip()
    if not target:
        return "Give a PID or process name to terminate."

    victims: list = []
    if target.isdigit():
        try:
            victims = [psutil.Process(int(target))]
        except psutil.NoSuchProcess:
            return f"No process with PID {target}."
    else:
        needle = target.lower()
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if needle in (proc.info.get("name") or "").lower():
                    victims.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not victims:
            return f"No running process matches '{target}'."

    # Every victim is screened before anything is signalled.
    described = []
    for proc in victims:
        try:
            name = proc.name()
            pid = proc.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        verdict = check_process(name, pid)
        enforce(
            verdict,
            f"kill {name} (PID {pid})",
            skill="kill_process", source=_source, pre_confirmed=_confirm,
            targets=describe_targets(f"pid:{pid}", f"process:{name}"),
        )
        described.append((proc, name, pid))

    if not described:
        return "Nothing left to terminate."

    killed, failed = [], []
    for proc, name, pid in described:
        try:
            proc.kill() if force else proc.terminate()
            killed.append(f"{name} ({pid})")
        except psutil.NoSuchProcess:
            killed.append(f"{name} ({pid}, already gone)")
        except psutil.AccessDenied:
            failed.append(f"{name} ({pid}): permission denied")
        except Exception as exc:
            failed.append(f"{name} ({pid}): {exc}")

    # Give SIGTERM a moment, then report what actually died.
    if killed and not force:
        gone, alive = psutil.wait_procs([p for p, _, _ in described], timeout=3)
        if alive:
            names = ", ".join(f"{p.pid}" for p in alive)
            failed.append(f"still running after SIGTERM: {names} (retry with force=true)")

    signal_name = "SIGKILL" if force else "SIGTERM"
    out = [f"Sent {signal_name} to {len(killed)} process(es): {', '.join(killed)}."]
    if failed:
        out.append("Failed: " + "; ".join(failed))
    return "\n".join(out)


@skill(category="system", danger=Danger.SAFE)
def disk_usage(path: str = "/") -> str:
    """Show disk usage for a filesystem, plus the largest mounts.

    Args:
        path: A path on the filesystem to report.
    """
    _need_psutil()
    lines = []
    try:
        usage = shutil.disk_usage(os.path.expanduser(path))
        pct = usage.used / usage.total * 100 if usage.total else 0
        lines.append(
            f"{path}: {_human_bytes(usage.used)} used of {_human_bytes(usage.total)} "
            f"({pct:.0f}%), {_human_bytes(usage.free)} free"
        )
    except OSError as exc:
        return f"Could not read {path}: {exc}"

    lines.append("\nAll mounted filesystems:")
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        lines.append(
            f"  {part.mountpoint:<28} {part.fstype:<8} "
            f"{_human_bytes(usage.used):>9} / {_human_bytes(usage.total):<9} "
            f"({usage.percent:.0f}%)"
        )
    return "\n".join(lines)


@skill(category="system", danger=Danger.SAFE)
def network_status() -> str:
    """Show network interfaces, addresses and the active connection."""
    lines = []

    if shell.have("nmcli"):
        result = shell.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show", "--active"],
            timeout=10,
        )
        if result.ok and result.stdout.strip():
            lines.append("Active connections:")
            for row in result.stdout.strip().splitlines():
                parts = row.split(":")
                if len(parts) >= 4:
                    lines.append(f"  {parts[0]} ({parts[1]}) on {parts[2]} — {parts[3]}")

        wifi = shell.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi"], timeout=10
        )
        if wifi.ok:
            for row in wifi.stdout.splitlines():
                if row.startswith("yes:"):
                    parts = row.split(":")
                    if len(parts) >= 3:
                        lines.append(f"  Wi-Fi: {parts[1]} (signal {parts[2]}%)")
                    break

    if psutil is not None:
        lines.append("\nInterfaces:")
        stats = psutil.net_if_stats()
        for iface, addrs in psutil.net_if_addrs().items():
            if iface == "lo":
                continue
            up = stats.get(iface)
            state = "up" if up and up.isup else "down"
            ips = [a.address for a in addrs if a.family.name in ("AF_INET", "AF_INET6")]
            ip_text = ", ".join(ips[:2]) if ips else "no address"
            lines.append(f"  {iface:<14} {state:<5} {ip_text}")

    return "\n".join(lines) if lines else "Could not read network status."


@skill(category="system", danger=Danger.SAFE)
def list_services(failed_only: bool = False, system_scope: bool = False) -> str:
    """List systemd services.

    Args:
        failed_only: Only show units that have failed.
        system_scope: List system units instead of user units.
    """
    return power.list_services(user=not system_scope, failed_only=failed_only)


@skill(category="system", danger=Danger.SAFE)
def service_status(name: str, system_scope: bool = False) -> str:
    """Show the status of one systemd unit.

    Args:
        name: Unit name, e.g. 'ydotool.service'.
        system_scope: Query the system manager instead of the user one.
    """
    return power.service_status(name, user=not system_scope)


@skill(category="system", danger=Danger.DESTRUCTIVE)
def control_service(
    name: str,
    action: str,
    system_scope: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Start, stop, restart, enable or disable a systemd unit.

    Args:
        name: Unit name, e.g. 'ydotool.service'.
        action: start, stop, restart, reload, enable or disable.
        system_scope: Act on the system manager instead of the user one.
    """
    from ..safety import Verdict

    scope = "system" if system_scope else "user"
    danger = Danger.CRITICAL if system_scope else Danger.DESTRUCTIVE
    verdict = Verdict(danger=danger, reasons=[f"{action} the {scope} unit '{name}'"])
    enforce(
        verdict, f"{action} {scope} service '{name}'",
        skill="control_service", source=_source, pre_confirmed=_confirm,
        targets=describe_targets(f"service:{name}"),
    )
    return power.service_action(name, action, user=not system_scope)


@skill(category="system", danger=Danger.SAFE)
def read_logs(unit: str = "", lines: int = 40, since: str = "") -> str:
    """Read the systemd journal.

    Args:
        unit: Limit to one unit, e.g. 'NetworkManager'.
        lines: How many recent lines to return.
        since: A time expression, e.g. '1 hour ago' or 'today'.
    """
    return power.journal(unit=unit, lines=lines, since=since)


@skill(category="system", danger=Danger.SAFE)
def environment_report() -> str:
    """Describe the host: distro, compositor, audio stack and available tools.

    Useful when a skill is unavailable and you need to know why.
    """
    from ..platform import clipboard, inputs, screen

    env = detect.environment()
    lines = [
        "Environment",
        f"  Distro          : {env.distro}",
        f"  Kernel          : {env.kernel}",
        f"  Session         : {env.session_type}",
        f"  Desktop         : {env.desktop}",
        f"  Compositor      : {env.compositor}",
        f"  Audio           : {env.audio}",
        f"  Init            : {env.init}",
        f"  Package manager : {env.package_manager}",
        f"  AUR helper      : {env.aur_helper or 'none'}",
        f"  Flatpak         : {'yes' if env.has_flatpak else 'no'}",
        "",
        "Capabilities",
        f"  Windows   : {__import__('brahma.platform.desktop', fromlist=['x']).backend().name}",
        f"  Input     : {inputs.status()}",
        f"  Screen    : {screen.status()}",
        f"  Clipboard : {clipboard.status()}",
    ]

    missing = detect.missing_report()
    if missing:
        lines.append("")
        lines.append("Degraded capabilities:")
        for capability, tool, package in missing:
            lines.append(f"  {capability:<18} needs {tool:<24} → sudo pacman -S {package}")

    return "\n".join(lines)
