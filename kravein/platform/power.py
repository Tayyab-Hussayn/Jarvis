"""Power, session and service control.

Replaces ``shutdown /s``, ``shutdown /r``, the Windows lock-screen hotkey and
Task Scheduler. On Linux this is ``systemctl`` and ``loginctl``; the user
service manager is also how the assistant runs itself in the background.
"""

from __future__ import annotations

from . import detect, shell


def _require_systemd() -> None:
    if detect.environment().init != "systemd":
        raise RuntimeError("this system is not running systemd")


def lock_session() -> str:
    """Lock the screen."""
    if shell.have("loginctl"):
        result = shell.run(["loginctl", "lock-session"], timeout=6)
        if result.ok:
            return "Session locked."

    # loginctl only works if the session's lock handler is wired up; fall
    # back to whichever locker is installed.
    for locker in ("hyprlock", "swaylock", "gtklock", "waylock", "i3lock", "xsecurelock"):
        if shell.have(locker):
            shell.spawn([locker])
            return f"Screen locked via {locker}."

    return "No screen locker found. Install hyprlock or swaylock."


def suspend() -> str:
    """Suspend to RAM."""
    _require_systemd()
    result = shell.run(["systemctl", "suspend"], timeout=10)
    if not result.ok:
        return f"Could not suspend: {result.output}"
    return "Suspending."


def hibernate() -> str:
    """Suspend to disk."""
    _require_systemd()
    result = shell.run(["systemctl", "hibernate"], timeout=10)
    if not result.ok:
        return f"Could not hibernate: {result.output}"
    return "Hibernating."


def reboot(delay_seconds: int = 0) -> str:
    """Reboot the machine."""
    _require_systemd()
    if delay_seconds > 0:
        result = shell.run(
            ["shutdown", "-r", f"+{max(1, delay_seconds // 60)}"], timeout=10
        )
        if result.ok:
            return f"Reboot scheduled in {delay_seconds}s. Cancel with: shutdown -c"
    result = shell.run(["systemctl", "reboot"], timeout=10)
    if not result.ok:
        return f"Could not reboot: {result.output}"
    return "Rebooting now."


def power_off(delay_seconds: int = 0) -> str:
    """Shut the machine down."""
    _require_systemd()
    if delay_seconds > 0:
        result = shell.run(
            ["shutdown", "-h", f"+{max(1, delay_seconds // 60)}"], timeout=10
        )
        if result.ok:
            return f"Shutdown scheduled in {delay_seconds}s. Cancel with: shutdown -c"
    result = shell.run(["systemctl", "poweroff"], timeout=10)
    if not result.ok:
        return f"Could not power off: {result.output}"
    return "Shutting down now."


def logout() -> str:
    """End the graphical session."""
    env = detect.environment()
    if env.compositor == "hyprland" and shell.have("hyprctl"):
        from . import desktop

        backend = desktop.backend()
        # Hyprland 0.56 moved dispatchers to Lua; try both spellings.
        if isinstance(backend, desktop.HyprlandBackend):
            if backend._dispatch("return hl.dispatch(hl.dsp.exit())", "exit"):
                return "Logging out."
        elif shell.run(["hyprctl", "dispatch", "exit"], timeout=6).ok:
            return "Logging out."
    if env.compositor == "sway" and shell.have("swaymsg"):
        if shell.run(["swaymsg", "exit"], timeout=6).ok:
            return "Logging out."
    if shell.have("loginctl"):
        result = shell.run(["loginctl", "terminate-session", "self"], timeout=6)
        if result.ok:
            return "Logging out."
    return "Could not determine how to log out of this session."


def cancel_scheduled() -> str:
    """Cancel a pending shutdown or reboot."""
    result = shell.run(["shutdown", "-c"], timeout=6)
    if result.ok:
        return "Cancelled any scheduled shutdown."
    return f"Nothing to cancel, or it failed: {result.output}"


# ---------------------------------------------------------------------------
# Battery / power state
# ---------------------------------------------------------------------------

def battery() -> str:
    """Battery status, if the machine has one."""
    try:
        import psutil
    except ImportError:
        psutil = None

    if psutil is not None:
        info = psutil.sensors_battery()
        if info is None:
            return "No battery detected (desktop or unsupported hardware)."
        state = "charging" if info.power_plugged else "on battery"
        line = f"Battery: {info.percent:.0f}% ({state})"
        if info.secsleft not in (None, getattr(psutil, "POWER_TIME_UNLIMITED", -1), -2):
            if info.secsleft > 0:
                hours, remainder = divmod(int(info.secsleft), 3600)
                line += f", ~{hours}h {remainder // 60}m left"
        return line

    if shell.have("upower"):
        result = shell.run(
            ["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0"], timeout=6
        )
        if result.ok:
            return result.stdout.strip()
    return "Could not read battery state."


# ---------------------------------------------------------------------------
# systemd services
# ---------------------------------------------------------------------------

def list_services(user: bool = True, failed_only: bool = False) -> str:
    """List systemd units."""
    _require_systemd()
    args = ["systemctl"]
    if user:
        args.append("--user")
    args += ["list-units", "--type=service", "--no-pager", "--no-legend"]
    if failed_only:
        args.append("--failed")

    result = shell.run(args, timeout=15)
    if not result.ok:
        return f"Could not list services: {result.output}"

    lines = [l for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        return "No failed services." if failed_only else "No services found."

    scope = "user" if user else "system"
    return f"{len(lines)} {scope} service(s):\n" + "\n".join(f"  {l}" for l in lines[:60])


def service_status(name: str, user: bool = True) -> str:
    """Show one unit's status."""
    _require_systemd()
    args = ["systemctl"]
    if user:
        args.append("--user")
    args += ["status", name, "--no-pager", "-n", "15"]
    result = shell.run(args, timeout=12)
    # systemctl status exits non-zero for inactive units, which is still useful.
    return result.stdout.strip() or result.stderr.strip() or f"No status for {name}."


def service_action(name: str, action: str, user: bool = True) -> str:
    """start / stop / restart / enable / disable a unit.

    System-scope changes need root, so those are surfaced as a command for
    the user to run rather than silently invoking sudo.
    """
    _require_systemd()
    valid = {"start", "stop", "restart", "reload", "enable", "disable"}
    action = (action or "").lower().strip()
    if action not in valid:
        raise ValueError(f"unknown service action '{action}'")

    args = ["systemctl"]
    if user:
        args.append("--user")
    args += [action, name]

    result = shell.run(args, timeout=25)
    if result.ok:
        scope = "user" if user else "system"
        return f"{action}ed {scope} service '{name}'."

    if "authentication" in result.stderr.lower() or "root" in result.stderr.lower():
        return (
            f"'{action} {name}' needs root. Run it yourself:\n"
            f"  sudo systemctl {action} {name}"
        )
    return f"Could not {action} '{name}': {result.output}"


def journal(unit: str = "", lines: int = 50, since: str = "") -> str:
    """Read the journal, optionally for one unit."""
    if not shell.have("journalctl"):
        return "journalctl is not available."
    args = ["journalctl", "--no-pager", "-n", str(max(1, min(lines, 500)))]
    if unit:
        args += ["-u", unit, "--user-unit", unit] if False else ["-u", unit]
    if since:
        args += ["--since", since]
    result = shell.run(args, timeout=20)
    return result.stdout.strip() or result.stderr.strip() or "No journal entries."
