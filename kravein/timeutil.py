"""Clock helpers shared by the scheduler and the routine engine.

Stdlib only, and it must stay that way. Both callers run inside the minutely
systemd tick, where every import is paid 1,440 times a day.

This exists so `routines.py` does not have to import `jobs.py` to read a
timestamp. Those two subsystems share a timer and a set of storage idioms and
nothing else; a helper import between them is exactly how "processed
separately" quietly stops being true.

**On local time.** Everything stored is UTC ISO. Local wall-clock appears in
one place only — turning "07:00 in Asia/Karachi" into an instant — and that is
what `local_moment` is for. Read its docstring before touching it; the DST
behaviour it relies on is deliberate and invisible.
"""

from __future__ import annotations

import os
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def parse_iso(text: str) -> datetime | None:
    """Parse a stored timestamp. Naive input is read as UTC, never local."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def local_timezone() -> str:
    """The machine's IANA zone name, or UTC if it cannot be determined."""
    try:
        link = Path("/etc/localtime").resolve()
        parts = link.parts
        if "zoneinfo" in parts:
            return "/".join(parts[parts.index("zoneinfo") + 1:])
    except OSError:
        pass
    return os.environ.get("TZ", "") or "UTC"


def zone(name: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name or local_timezone())
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def parse_clock(text: str) -> tuple[int, int] | None:
    """Read "07:00" or "7:5" into (hour, minute). None if it is not a time."""
    raw = (text or "").strip()
    if ":" not in raw:
        return None
    head, _, tail = raw.partition(":")
    try:
        hour, minute = int(head), int(tail)
    except ValueError:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def local_moment(day: date_cls, hour: int, minute: int, tz) -> datetime:
    """A local wall-clock time on a given date, as a UTC instant.

    Both daylight-saving edge cases are handled by `fold` defaulting to 0, and
    both behaviours are deliberate — this looks like it ignores DST and does
    not:

    **Spring forward** (the wall-clock time does not exist). PEP 495 says a
    naive time inside the gap is interpreted with the *pre*-transition offset,
    so 02:30 on a day where 02:00 jumps to 03:00 resolves to an instant 30
    minutes after the transition — 03:30 local. The step fires shortly after
    the clocks change rather than being silently dropped, which is what
    "resolve forward" means.

    **Fall back** (the wall-clock time happens twice). `fold=0` picks the first
    occurrence, so a 01:30 step fires once, on the earlier pass, and is already
    `done` when the clock repeats itself.

    Getting either of these wrong shows up twice a year as a reminder that
    never arrives, which is close to impossible to debug after the fact. Hence
    the essay for two lines of code.
    """
    naive = datetime(day.year, day.month, day.day, hour, minute)
    return naive.replace(tzinfo=tz).astimezone(timezone.utc)


def in_dst_gap(day: date_cls, hour: int, minute: int, tz) -> bool:
    """Did this wall-clock time not exist on this date, in this zone?

    Only used to explain a resolved time back to the user; nothing branches on
    it. A round trip that does not land where it started means the instant was
    normalised out of a gap.
    """
    naive = datetime(day.year, day.month, day.day, hour, minute)
    back = naive.replace(tzinfo=tz).astimezone(timezone.utc).astimezone(tz)
    return back.replace(tzinfo=None) != naive
