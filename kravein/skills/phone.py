"""Skills that act on a paired phone, through the phone channel (``kravein/phone.py``).

Every tool name starts with ``phone`` and every docstring says "on your phone"
and names the desktop counterpart. The desktop already has ``set_volume`` and
``open_url``; a model that cannot tell the two apart turns up the laptop when
asked about the phone, or opens a link on the wrong screen.

Design rules, each one a failure it prevents:

* **The phone is resolved before anything is confirmed.** ``phone.call()``
  would pick the phone itself, but then a confirmation could only say "your
  phone" — not reviewable with two phones paired. The skill resolves to one
  ``device_id`` first, puts its name and id in the confirmation, and passes that
  id to ``call()`` so the command lands on the phone that was approved.
* **Several phones and no name is a refusal that lists them,** never the first
  match (same rule as windows, see ``hard-won-desktop.md``).
* **Arguments and capability are checked before asking.** Otherwise the user
  approves "open ftp://…" and is then told it was never valid.
* **Success is what the phone reported.** A timeout or a dropped socket after
  the command went out means the outcome is *unknown*, and the result says so.
"""

from __future__ import annotations

from typing import Any

from .. import phone as channel
from ..registry import skill
from ..safety import Danger, Verdict, describe_targets, enforce

# Errors where the command definitely never reached the phone.
_NOT_SENT = (channel.PhoneNotConnected, channel.PhoneAmbiguous, channel.PhoneCommandRejected)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _label(link: dict[str, Any]) -> str:
    name = link.get("name") or "unnamed phone"
    return f"{name} ({link['device_id']})"


def _offline_hint() -> str:
    """Name paired phones that are offline, so "not connected" is actionable."""
    try:
        paired = [d for d in channel.list_devices() if not d.get("connected")]
    except Exception:  # a broken store must not hide the real answer
        return ""
    if not paired:
        return " No phone is paired yet; pair one from the web UI."
    names = ", ".join(d.get("name") or d["device_id"] for d in paired)
    return f" Paired but offline: {names}. Open the Kravein app on the phone to reconnect."


def _resolve(device: str) -> dict[str, Any]:
    """Pick exactly one connected phone, with the same rules as ``phone._pick``
    (id first, then case-insensitive name). Raises the channel's own errors."""
    links = channel.connected()
    if not links:
        raise channel.PhoneNotConnected("no phone is connected")
    wanted = (device or "").strip()
    if not wanted:
        if len(links) == 1:
            return links[0]
        names = ", ".join(sorted(_label(l) for l in links))
        raise channel.PhoneAmbiguous(f"several phones are connected; name one: {names}")
    matches = [l for l in links if l["device_id"] == wanted]
    if not matches:
        matches = [l for l in links if (l.get("name") or "").lower() == wanted.lower()]
    if not matches:
        names = ", ".join(sorted(_label(l) for l in links))
        raise channel.PhoneNotConnected(
            f"no connected phone named {wanted!r}; connected: {names}"
        )
    if len(matches) > 1:
        ids = ", ".join(sorted(l["device_id"] for l in matches))
        raise channel.PhoneAmbiguous(f"several phones are named {wanted!r}: {ids}")
    return matches[0]


def _precheck(link: dict[str, Any], command: str, args: dict[str, Any]) -> None:
    """Refuse before confirming: bad arguments or a command the app lacks."""
    channel.validate_command(command, args)
    if command not in link.get("capabilities", ()):
        raise channel.PhoneCommandRejected(
            f"phone {_label(link)} does not support {command}", code="unknown_capability",
        )


def _explain(exc: channel.PhoneError, what: str) -> str:
    """Turn every channel failure into one honest sentence.

    ``what`` is the action in plain words ("turn the flashlight on").
    """
    if isinstance(exc, channel.PhoneNotConnected):
        return f"Could not {what}: {exc}. Nothing was sent.{_offline_hint()}"
    if isinstance(exc, channel.PhoneAmbiguous):
        return (f"Could not {what}: {exc}. Nothing was sent. "
                "Say which phone, by name or id (see list_phones).")
    if isinstance(exc, channel.PhoneCommandRejected):
        if exc.code == "unknown_capability":
            return (f"Could not {what}: {exc}. Nothing was sent; "
                    "the Kravein app on the phone may need an update.")
        return f"Could not {what}: the request was invalid ({exc}). Nothing was sent."
    if isinstance(exc, channel.PhoneTimeout):
        return (f"Sent the request to {what}, but the phone did not answer in time ({exc}). "
                "It may or may not have happened; I cannot confirm it.")
    if isinstance(exc, channel.PhoneDisconnected):
        return (f"The phone disconnected before answering ({exc}). "
                f"Whether it managed to {what} is unknown.")
    if isinstance(exc, channel.PhoneBadResult):
        return (f"The phone answered with a malformed reply ({exc}), "
                f"so I cannot confirm that it did {what}.")
    if isinstance(exc, channel.PhoneCommandFailed):
        hint = ""
        if exc.code == "permission_denied":
            hint = " Grant the Kravein app that permission on the phone."
        elif exc.code == "unavailable":
            hint = " The phone says this is not available right now."
        return f"The phone could not {what} ({exc.code}): {exc}.{hint}"
    return f"Could not {what}: phone error ({exc.code}): {exc}."


def _act(
    *,
    skill_name: str,
    device: str,
    command: str,
    args: dict[str, Any],
    what: str,
    danger: Danger,
    source: str,
    confirm: bool,
) -> tuple[dict[str, Any], dict[str, Any]] | str:
    """Resolve, check, enforce, call. Returns ``(link, data)`` or a failure sentence.

    ``SafetyError``/``ConfirmationRequired`` from ``enforce`` propagate on
    purpose: the registry turns them into the tool result, and catching them
    here would be the easiest way to accidentally carry on after a "no".
    """
    try:
        link = _resolve(device)
        _precheck(link, command, args)
    except channel.PhoneError as exc:
        return _explain(exc, what)

    phone_id = f"phone:{link['device_id']}"
    targets = [phone_id, f"{phone_id}:{command}"]
    if not (device or "").strip():
        # The phone was picked because it was the only one connected. If the
        # user says no, a retry must not prompt again once a second phone
        # shows up and the guess lands somewhere else.
        targets.append("phone:implicit")
    enforce(
        Verdict(danger, reasons=[f"acts on your phone {_label(link)}"]),
        f"{what} on your phone {_label(link)}",
        skill=skill_name, source=source, pre_confirmed=confirm,
        targets=describe_targets(*targets),
    )
    try:
        data = channel.call(command, args, device=link["device_id"])
    except channel.PhoneError as exc:
        return _explain(exc, what)
    return link, data


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------

@skill(category="phone", danger=Danger.SAFE)
def list_phones() -> str:
    """List the phones paired with Kravein and which are connected right now.

    Use this to find a phone's name or id when several are connected.
    """
    try:
        devices = channel.list_devices()
    except Exception as exc:
        return f"Could not read the paired phones: {exc}"
    if not devices:
        return "No phone is paired. Pair one from the web UI with the Kravein app."
    lines = []
    for d in devices:
        state = "connected" if d.get("connected") else "offline"
        lines.append(f"- {d.get('name') or 'unnamed phone'} (id {d['device_id']}): {state}")
    return "Paired phones:\n" + "\n".join(lines)


@skill(category="phone", danger=Danger.SAFE)
def phone_status(device: str = "") -> str:
    """Read your phone's battery, volume and model. Not this computer's battery or volume.

    Args:
        device: Phone name or id. Omit when only one phone is connected.
    """
    try:
        link = _resolve(device)
    except channel.PhoneError as exc:
        return _explain(exc, "read the phone's status")

    caps = set(link.get("capabilities", ()))
    lines = [f"Phone {_label(link)}:"]
    for command, what, render in (
        ("get_battery", "report the battery",
         lambda d: f"battery {d['percent']}%"
                   + (" (charging)" if d["charging"] else " (not charging)")),
        ("volume_get", "report the media volume",
         lambda d: f"media volume {d['percent']}%"),
        ("get_device_info", "report its model",
         lambda d: f"{d['model']}, OS {d['os_version']}, app {d['app_version']}"),
    ):
        if command not in caps:
            lines.append(f"- {what}: not supported by the app on this phone")
            continue
        try:
            data = channel.call(command, {}, device=link["device_id"])
        except (channel.PhoneTimeout, channel.PhoneDisconnected) as exc:
            # The link itself is in trouble; asking again would only add
            # another full timeout to the wait.
            lines.append(f"- {_explain(exc, what)}")
            lines.append("Stopped asking: the phone is not responding.")
            break
        except channel.PhoneError as exc:
            lines.append(f"- {_explain(exc, what)}")
            continue
        lines.append(f"- {render(data)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State-changing
#
# Levels (enforce asks for confirmation from DESTRUCTIVE up):
#   flashlight, volume -> MODERATE. Visible, instantly reversible, carry no
#     data — the same level the desktop gives its own volume.
#   open_url, launch_app -> DESTRUCTIVE, so they are confirmed. Their argument
#     is model-chosen and can come straight from a web page the model just
#     read, and the effect lands on a device the user may not be looking at.
# ---------------------------------------------------------------------------

@skill(category="phone", danger=Danger.MODERATE)
def phone_flashlight(on: bool, device: str = "", _source: str = "local",
                     _confirm: bool = False) -> str:
    """Turn the flashlight (torch) on your phone on or off.

    Args:
        on: True to turn the phone's flashlight on, False to turn it off.
        device: Phone name or id. Omit when only one phone is connected.
    """
    want = "on" if on else "off"
    command = "flashlight_on" if on else "flashlight_off"
    out = _act(skill_name="phone_flashlight", device=device, command=command, args={},
               what=f"turn the flashlight {want}", danger=Danger.MODERATE,
               source=_source, confirm=_confirm)
    if isinstance(out, str):
        return out
    link, data = out
    if data["flashlight"] != want:
        return (f"Asked {_label(link)} to turn the flashlight {want}, "
                f"but the phone reports it is {data['flashlight']}.")
    return f"The flashlight on {_label(link)} is now {want}."


@skill(category="phone", danger=Danger.MODERATE)
def phone_set_volume(percent: int, device: str = "", _source: str = "local",
                     _confirm: bool = False) -> str:
    """Set the media volume on your phone. For this computer's speakers use set_volume.

    Args:
        percent: Target phone media volume, 0-100.
        device: Phone name or id. Omit when only one phone is connected.
    """
    out = _act(skill_name="phone_set_volume", device=device, command="volume_set",
               args={"percent": percent}, what=f"set the media volume to {percent}%",
               danger=Danger.MODERATE, source=_source, confirm=_confirm)
    if isinstance(out, str):
        return out
    link, data = out
    if data["percent"] != percent:
        # Android rounds to its own volume steps, so a near miss is normal.
        return (f"Asked {_label(link)} for {percent}%; the phone's media volume "
                f"is now {data['percent']}%.")
    return f"Media volume on {_label(link)} is now {percent}%."


@skill(category="phone", danger=Danger.DESTRUCTIVE)
def phone_open_url(url: str, device: str = "", _source: str = "local",
                   _confirm: bool = False) -> str:
    """Open a web page in the browser on your phone. To open it on this computer use open_url.

    Args:
        url: The http or https address to open on the phone. A bare domain
            like example.com gets https:// added.
        device: Phone name or id. Omit when only one phone is connected.
    """
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    out = _act(skill_name="phone_open_url", device=device, command="open_url",
               args={"url": url}, what=f"open {url}", danger=Danger.DESTRUCTIVE,
               source=_source, confirm=_confirm)
    if isinstance(out, str):
        return out
    link, data = out
    return f"{_label(link)} opened {data['url']}."


@skill(category="phone", danger=Danger.DESTRUCTIVE)
def phone_launch_app(app: str, device: str = "", _source: str = "local",
                     _confirm: bool = False) -> str:
    """Open an installed app on your phone. To open an app on this computer use open_app.

    Args:
        app: The app's name as shown on the phone (e.g. "WhatsApp") or its
            package name (e.g. "com.whatsapp").
        device: Phone name or id. Omit when only one phone is connected.
    """
    app = (app or "").strip()
    if not app:
        return "Say which app to open on the phone."
    out = _act(skill_name="phone_launch_app", device=device, command="launch_app",
               args={"app": app}, what=f"open the app {app!r}", danger=Danger.DESTRUCTIVE,
               source=_source, confirm=_confirm)
    if isinstance(out, str):
        return out
    link, data = out
    return f"{_label(link)} opened {data['package']}."
