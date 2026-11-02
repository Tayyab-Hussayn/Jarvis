"""System settings: volume, brightness, network, power, appearance.

Ports the rest of ``actions/computer_settings.py``. The Windows build reached
these through pycaw, WMI-over-PowerShell, registry writes and media keys; each
is now a native Linux control path.
"""

from __future__ import annotations

from typing import Literal

from ..platform import audio, display, power, shell
from ..registry import skill
from . import _merge
from ..safety import Danger, Verdict, enforce


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def get_volume() -> str:
    """Report the current output volume and mute state."""
    volume, muted = audio.get_volume()
    return f"Volume is {volume}%{' (muted)' if muted else ''}."


def set_volume(percent: int) -> str:
    """Set this computer's output volume. For the phone use phone_set_volume.

    Args:
        percent: Target volume, 0-100.
    """
    return audio.set_volume(percent)


def change_volume(delta: int = 10) -> str:
    """Turn the volume up or down.

    Args:
        delta: Signed percentage change, e.g. 10 or -10.
    """
    return audio.change_volume(delta)


def mute_audio(state: bool | None = None) -> str:
    """Mute, unmute, or toggle the speakers.

    Args:
        state: True to mute, False to unmute, omit to toggle.
    """
    return audio.set_mute(state)


@skill(category="settings", danger=Danger.MODERATE)
def mute_microphone(state: bool | None = None) -> str:
    """Mute, unmute, or toggle the microphone.

    Args:
        state: True to mute, False to unmute, omit to toggle.
    """
    return audio.set_mic_mute(state)


def list_audio_devices() -> str:
    """List audio outputs and inputs."""
    return audio.list_sinks()


@skill(
    category="settings", danger=Danger.MODERATE,
    aliases={
        "get_volume": {"action": "get"},
        "set_volume": {"action": "set"},
        "change_volume": {"action": "change"},
        "mute_audio": {"action": "mute"},
    },
)
def volume(
    action: Literal["get", "set", "change", "mute"] = "get",
    percent: int | None = None,
    delta: int | None = None,
    state: bool | None = None,
) -> str:
    """Read or control this computer's speaker volume. For the phone use phone_set_volume.


    Args:
        action: get; set (needs percent); change (needs delta, default +10); mute (state, omit to toggle).
        percent: For set: target volume 0-100.
        delta: For change: signed percentage change, e.g. 10 or -10.
        state: For mute: True to mute, False to unmute, omit to toggle.
    """
    if action == "get":
        return get_volume()
    if action == "set":
        if percent is None:
            return "set needs percent (0-100)."
        return set_volume(percent)
    if action == "change":
        return _merge.run(change_volume, delta=delta)
    if action == "mute":
        return mute_audio(state)
    return _merge.unknown(action, ["get", "set", "change", "mute"])


@skill(
    category="settings", danger=Danger.SAFE,
    aliases={"list_audio_devices": {"kind": "audio"}, "list_monitors": {"kind": "monitors"}},
)
def list_devices(kind: Literal["audio", "monitors"] = "audio") -> str:
    """List audio outputs/inputs, or connected monitors with resolution and refresh rate.

    Args:
        kind: audio or monitors.
    """
    if kind == "audio":
        return list_audio_devices()
    if kind == "monitors":
        return list_monitors()
    return _merge.unknown(kind, ["audio", "monitors"])


# ---------------------------------------------------------------------------
# Media transport
# ---------------------------------------------------------------------------

@skill(
    category="settings", danger=Danger.MODERATE, requires=("playerctl",),
    aliases={"now_playing": {"command": "status"}},
)
def media_control(command: str = "play-pause") -> str:
    """Control media playback in any MPRIS-aware player, or show what is playing.

    Args:
        command: play, pause, play-pause, next, previous, stop, or status to
            show what is currently playing.
    """
    if (command or "").strip().lower() in ("status", "now_playing", "now-playing"):
        return now_playing()
    return audio.media(command)


def now_playing() -> str:
    """Show what is currently playing."""
    return audio.now_playing()


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def get_brightness() -> str:
    """Report the current screen brightness."""
    level = display.get_brightness()
    if level < 0:
        return "Could not read the screen brightness."
    return f"Brightness is {level}%."


def set_brightness(percent: int) -> str:
    """Set the screen brightness.

    Args:
        percent: Target brightness, 5-100.
    """
    return display.set_brightness(percent)


def change_brightness(delta: int = 10) -> str:
    """Turn the screen brightness up or down.

    Args:
        delta: Signed percentage change, e.g. 10 or -10.
    """
    return display.change_brightness(delta)

@skill(
    category="settings", danger=Danger.MODERATE,
    aliases={
        "get_brightness": {"action": "get"},
        "set_brightness": {"action": "set"},
        "change_brightness": {"action": "change"},
    },
)
def brightness(
    action: Literal["get", "set", "change"] = "get",
    percent: int | None = None,
    delta: int | None = None,
) -> str:
    """Read or control the screen brightness.


    Args:
        action: get; set (needs percent); change (needs delta, default +10).
        percent: For set: target brightness 5-100.
        delta: For change: signed percentage change, e.g. 10 or -10.
    """
    if action == "get":
        return get_brightness()
    if action == "set":
        if percent is None:
            return "set needs percent (5-100)."
        return set_brightness(percent)
    if action == "change":
        return _merge.run(change_brightness, delta=delta)
    return _merge.unknown(action, ["get", "set", "change"])



@skill(category="settings", danger=Danger.MODERATE)
def blank_screen() -> str:
    """Turn the displays off. Move the mouse to wake them."""
    return display.blank_screen()


def list_monitors() -> str:
    """List connected monitors with their resolution and refresh rate."""
    return display.list_monitors()


@skill(category="settings", danger=Danger.MODERATE)
def set_dark_mode(enabled: bool | None = None) -> str:
    """Switch the desktop between light and dark.

    Args:
        enabled: True for dark, False for light, omit to toggle.
    """
    return display.set_dark_mode(enabled)


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

@skill(
    category="settings", danger=Danger.MODERATE, requires=("nmcli",),
    aliases={"list_wifi_networks": {"action": "list"}},
)
def wifi_control(action: str = "status", rescan: bool = False) -> str:
    """Turn Wi-Fi on or off, check its state, or list visible networks.

    Args:
        action: 'on', 'off', 'toggle', 'status', or 'list' for visible networks.
        rescan: For list: force a fresh scan first.
    """
    action = (action or "status").lower().strip()
    if action == "list":
        return list_wifi_networks(rescan)

    state = shell.run(["nmcli", "radio", "wifi"], timeout=8)
    enabled = "enabled" in state.stdout.lower()

    if action == "status":
        return f"Wi-Fi is {'on' if enabled else 'off'}."

    if action == "toggle":
        target = "off" if enabled else "on"
    elif action in ("on", "off"):
        target = action
    else:
        return f"Unknown action '{action}'. Use on, off, toggle or status."

    result = shell.run(["nmcli", "radio", "wifi", target], timeout=15)
    if not result.ok:
        return f"Could not turn Wi-Fi {target}: {result.output}"
    return f"Wi-Fi turned {target}."


def list_wifi_networks(rescan: bool = False) -> str:
    """List visible Wi-Fi networks.

    Args:
        rescan: Force a fresh scan before listing.
    """
    if rescan:
        shell.run(["nmcli", "device", "wifi", "rescan"], timeout=25)

    result = shell.run(
        ["nmcli", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
        timeout=20,
    )
    if not result.ok:
        return f"Could not list networks: {result.output}"
    return result.stdout.strip() or "No networks found."


@skill(category="settings", danger=Danger.MODERATE, requires=("bluetoothctl",))
def bluetooth_control(action: str = "status") -> str:
    """Turn Bluetooth on or off, or list devices.

    Args:
        action: 'on', 'off', 'status' or 'devices'.
    """
    action = (action or "status").lower().strip()

    if action == "devices":
        result = shell.run(["bluetoothctl", "devices"], timeout=12)
        return result.stdout.strip() or "No paired devices."

    if action == "status":
        result = shell.run(["bluetoothctl", "show"], timeout=12)
        if not result.ok:
            return "Could not read Bluetooth status."
        powered = "Powered: yes" in result.stdout
        return f"Bluetooth is {'on' if powered else 'off'}."

    if action in ("on", "off"):
        result = shell.run(["bluetoothctl", "power", action], timeout=12)
        if not result.ok:
            return f"Could not turn Bluetooth {action}: {result.output}"
        return f"Bluetooth turned {action}."

    return f"Unknown action '{action}'."


# ---------------------------------------------------------------------------
# Session and power
# ---------------------------------------------------------------------------

@skill(category="power", danger=Danger.MODERATE)
def lock_screen() -> str:
    """Lock the session."""
    return power.lock_session()


@skill(category="power", danger=Danger.SAFE)
def battery_status() -> str:
    """Report battery charge and time remaining."""
    return power.battery()


def suspend_system(_source: str = "local", _confirm: bool = False) -> str:
    """Suspend the machine to RAM."""
    enforce(
        Verdict(danger=Danger.CRITICAL, reasons=["suspends the machine immediately"]),
        "suspend the system", skill="suspend_system",
        source=_source, pre_confirmed=_confirm,
    )
    return power.suspend()


def reboot_system(delay_seconds: int = 0, _source: str = "local", _confirm: bool = False) -> str:
    """Reboot the machine.

    Args:
        delay_seconds: Wait this long first, giving you time to cancel.
    """
    enforce(
        Verdict(
            danger=Danger.CRITICAL,
            reasons=["reboots the machine — all unsaved work will be lost"],
        ),
        "reboot the system", skill="reboot_system",
        source=_source, pre_confirmed=_confirm,
    )
    return power.reboot(delay_seconds)


def shutdown_system(delay_seconds: int = 0, _source: str = "local", _confirm: bool = False) -> str:
    """Power the machine off.

    Args:
        delay_seconds: Wait this long first, giving you time to cancel.
    """
    enforce(
        Verdict(
            danger=Danger.CRITICAL,
            reasons=["shuts the machine down — all unsaved work will be lost"],
        ),
        "shut down the system", skill="shutdown_system",
        source=_source, pre_confirmed=_confirm,
    )
    return power.power_off(delay_seconds)


def log_out(_source: str = "local", _confirm: bool = False) -> str:
    """End the graphical session."""
    enforce(
        Verdict(danger=Danger.CRITICAL, reasons=["ends your desktop session"]),
        "log out", skill="log_out", source=_source, pre_confirmed=_confirm,
    )
    return power.logout()


@skill(
    category="power", danger=Danger.CRITICAL,
    aliases={
        "suspend_system": {"action": "suspend"},
        "reboot_system": {"action": "reboot"},
        "shutdown_system": {"action": "shutdown"},
        "log_out": {"action": "log_out"},
    },
)
def power_session(
    action: Literal["suspend", "reboot", "shutdown", "log_out"],
    delay_seconds: int | None = None,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Suspend, reboot, shut down, or log out of this computer. Always asks first.

    To cancel a pending shutdown or reboot use cancel_shutdown.

    Args:
        action: suspend, reboot, shutdown or log_out.
        delay_seconds: For reboot/shutdown: wait this long first, giving you time to cancel.
    """
    if action == "suspend":
        return _merge.run(suspend_system, _source, _confirm)
    if action == "reboot":
        return _merge.run(reboot_system, _source, _confirm, delay_seconds=delay_seconds)
    if action == "shutdown":
        return _merge.run(shutdown_system, _source, _confirm, delay_seconds=delay_seconds)
    if action == "log_out":
        return _merge.run(log_out, _source, _confirm)
    return _merge.unknown(action, ["suspend", "reboot", "shutdown", "log_out"])


@skill(category="power", danger=Danger.MODERATE)
def cancel_shutdown() -> str:
    """Cancel a scheduled shutdown or reboot."""
    return power.cancel_scheduled()
