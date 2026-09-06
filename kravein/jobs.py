"""Scheduled jobs: a JSON store plus a minutely tick.

Design notes that are load-bearing — read before changing anything here.

**The tick must stay cheap.** A systemd timer runs `python -m kravein.jobs
tick` every minute, forever. Routing that through `cli.py` would cost ~250ms
per tick, because `cli.py` imports the Agent, the LLM facade and the registry
at module level — six minutes of CPU a day to discover there is nothing to do.
So this module deliberately has its own `__main__`, imports only `config` from
the package (~47ms total against a 23ms bare interpreter), and pulls in the
heavy machinery *inside* `run_job`, only once a job is actually due.

Keep it that way. An import added at the top of this file is paid 1,440 times
a day whether or not it is needed.

**The lock is a separate file, not jobs.json.** `jobs.json` is written with the
project's usual atomic tmp+rename, which replaces the inode — so a `flock` held
on `jobs.json` would be guarding a file that no longer exists at that path, and
two ticks could both believe they hold it. `jobs.lock` is never replaced, so it
is a stable thing to lock.

**A job is marked running before the Agent exists.** The state write happens
while the lock is held and before any heavy import, so the next minute's tick
sees `running` and skips it. Without that ordering a slow job would be started
again every minute — an agent turn can easily outlive its own tick interval.

**A crashed run must not wedge a job forever.** `running` records the pid; a
tick that finds a `running` job whose pid is gone reaps it rather than leaving
it stuck. This is why the pid is stored at all.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config

STORE = config.STATE_DIR / "jobs.json"
LOCK = config.STATE_DIR / "jobs.lock"
HISTORY = config.STATE_DIR / "job-runs.jsonl"

# Fire a late job if it is less than this far behind, otherwise skip it and
# move on to the next occurrence. A laptop shut for a week must not wake up and
# deliver forty stale reminders at once; a laptop asleep over lunch should still
# tell you about the 1pm one. No catch-up beyond the window, by design.
DEFAULT_GRACE_SECONDS = 15 * 60

# Transient failures are retried on later ticks. There is no in-process sleep:
# the retry time is written to the store, so a reboot mid-backoff loses nothing.
RETRY_BACKOFF_SECONDS = (60, 300, 900)
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS)

# A run that has been "running" for longer than this with a live pid is assumed
# wedged. Generous, because an agent turn with slow providers is legitimately
# minutes long.
STALE_RUN_SECONDS = 30 * 60

VALID_KINDS = ("notify", "agent")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

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


def _zone(name: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name or local_timezone())
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


_RELATIVE = __import__("re").compile(
    r"^(?:in\s+)?(?:(?P<days>\d+)\s*d)?\s*(?:(?P<hours>\d+)\s*h)?\s*"
    r"(?:(?P<minutes>\d+)\s*m(?:in)?)?\s*(?:(?P<seconds>\d+)\s*s)?$"
)
def parse_when(text: str, tz_name: str = "") -> datetime | None:
    """Validate a scheduling time. **Parsing prose is not this function's job.**

    There is exactly one natural-language parser in this system and it is the
    model. It is told the current time in the per-turn context (see
    `Agent._with_context`) and is expected to resolve "next Tuesday", "in two
    weeks" or "the day after tomorrow" itself, then hand over an absolute
    timestamp. This only accepts what a machine can read unambiguously:

        2026-09-07T17:00      ISO 8601; naive is read in the job's timezone
        2026-09-07T12:00:00Z  explicit offsets honoured
        20m  1h30m  2d  45s   plain durations, kept because `set_reminder`
                              has taken a `delay` in this form since before
                              the job store existed

    An earlier version also understood "5pm" and "tomorrow 17:00". That was a
    second NL parser competing with the model's, and it would have failed the
    moment anyone said "next Tuesday" — better to have no fuzzy branch at all
    than one that covers three phrasings out of thirty.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    zone = _zone(tz_name)

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    match = _RELATIVE.fullmatch(raw.lower())
    if match and any(match.groupdict().values()):
        parts = {k: int(v or 0) for k, v in match.groupdict().items()}
        delta = timedelta(
            days=parts["days"], hours=parts["hours"],
            minutes=parts["minutes"], seconds=parts["seconds"],
        )
        if delta.total_seconds() > 0:
            return now_utc() + delta

    return None


def next_cron(expression: str, tz_name: str, after: datetime | None = None) -> datetime | None:
    """Next occurrence of a cron expression, as UTC.

    Evaluated in the job's own zone so "every day at 9" means 9am where the
    user is, and keeps meaning that across a DST change.
    """
    try:
        from croniter import croniter
    except ImportError:
        return None

    zone = _zone(tz_name)
    base = (after or now_utc()).astimezone(zone)
    try:
        return croniter(expression, base).get_next(datetime).astimezone(timezone.utc)
    except Exception:
        return None


def valid_cron(expression: str) -> bool:
    try:
        from croniter import croniter

        return bool(croniter.is_valid(expression))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

@contextmanager
def locked(blocking: bool = True):
    """Hold the store lock, or yield False if another tick has it.

    Non-blocking is what the tick wants: if a previous tick is still working,
    this one should exit rather than queue up behind it and then double-run.
    """
    config.ensure_dirs()
    handle = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


def load() -> dict[str, Any]:
    """Read the store. Never raises — a corrupt file must not stop the timer."""
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"jobs": [], "seq": 0}
    if not isinstance(data, dict):
        return {"jobs": [], "seq": 0}
    data.setdefault("jobs", [])
    data.setdefault("seq", 0)
    return data


def save(data: dict[str, Any]) -> None:
    """Atomic write, matching `skills/memory.py`."""
    config.ensure_dirs()
    tmp = STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STORE)


def record_run(**fields: Any) -> None:
    """Append one line of run history. Mirrors audit.py's JSONL shape."""
    try:
        config.ensure_dirs()
        line = json.dumps(
            {"ts": iso(now_utc()), **fields}, ensure_ascii=False, default=str
        )
        with open(HISTORY, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _pid_alive(pid: int | None) -> bool:
    """Is this pid still *our* runner?

    `os.kill(pid, 0)` alone is not enough. Linux recycles pids, and on a busy
    machine the number that belonged to a crashed tick an hour ago is now
    something else entirely — a browser tab, a compiler. Believing that is
    still the runner leaves the job pinned in "running" for ever: never
    reaped, never retried, and invisible unless someone reads the store by
    hand.

    So the identity is checked, not just the liveness. `/proc/<pid>/cmdline`
    is NUL-separated; a real runner has "kravein" somewhere in it because it
    was started as `python -m kravein.jobs`. Anything else — missing proc
    entry, different process — means the runner is gone.
    """
    if not pid:
        return False
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, ValueError):
        # No /proc entry: the process is gone. On a system without procfs this
        # also reads as dead, which is the safe direction — a job gets retried
        # rather than stuck.
        return False
    return b"kravein" in raw.replace(b"\x00", b" ").lower()


# ---------------------------------------------------------------------------
# Creating and listing
# ---------------------------------------------------------------------------

def add(
    *,
    prompt: str,
    when: datetime | None = None,
    cron: str = "",
    kind: str = "notify",
    title: str = "Reminder",
    tz_name: str = "",
    speak: bool = False,
) -> dict[str, Any]:
    """Add a job. Returns the stored record."""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    tz_name = tz_name or local_timezone()

    if cron:
        if not valid_cron(cron):
            raise ValueError(f"not a valid cron expression: {cron!r}")
        first = next_cron(cron, tz_name)
        if first is None:
            raise ValueError(f"could not schedule cron {cron!r}")
    else:
        if when is None:
            raise ValueError("a one-shot job needs a time")
        first = when.astimezone(timezone.utc)

    with locked() as got:
        if not got:
            raise RuntimeError("job store is busy")
        data = load()
        data["seq"] = int(data.get("seq", 0)) + 1
        job = {
            "id": f"j{data['seq']}",
            "kind": kind,
            "prompt": prompt,
            "title": title,
            "cron": cron,
            "timezone": tz_name,
            "speak": bool(speak),
            "created_at": iso(now_utc()),
            "next_run": iso(first),
            "state": "pending",
            "attempts": 0,
            "last_run": "",
            "last_error": "",
            "runs": 0,
            "running_pid": None,
            "running_since": "",
        }
        data["jobs"].append(job)
        save(data)
    record_run(event="scheduled", id=job["id"], kind=kind, next_run=job["next_run"])
    return job


def all_jobs() -> list[dict[str, Any]]:
    return list(load().get("jobs", []))


def pending() -> list[dict[str, Any]]:
    return [j for j in all_jobs() if j.get("state") in ("pending", "running")]


def cancel(job_id: str = "") -> int:
    """Cancel one job, or every pending job when no id is given."""
    removed = 0
    with locked() as got:
        if not got:
            raise RuntimeError("job store is busy")
        data = load()
        keep = []
        for job in data.get("jobs", []):
            target = not job_id or job.get("id") == job_id
            if target and job.get("state") in ("pending", "running"):
                removed += 1
                record_run(event="cancelled", id=job.get("id"))
                continue
            keep.append(job)
        data["jobs"] = keep
        save(data)
    return removed


def _update(job_id: str, **fields: Any) -> None:
    """Apply fields to one job under the lock."""
    with locked() as got:
        if not got:
            return
        data = load()
        for job in data.get("jobs", []):
            if job.get("id") == job_id:
                job.update(fields)
                break
        save(data)


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------

def _reap(data: dict[str, Any]) -> int:
    """Release jobs whose runner died. Returns how many were reaped."""
    reaped = 0
    for job in data.get("jobs", []):
        if job.get("state") != "running":
            continue
        started = parse_iso(job.get("running_since", ""))
        age = (now_utc() - started).total_seconds() if started else 0
        alive = _pid_alive(job.get("running_pid"))
        if alive and age < STALE_RUN_SECONDS:
            continue
        reaped += 1
        job["running_pid"] = None
        job["running_since"] = ""
        job["last_error"] = "the previous run did not finish"
        record_run(event="reaped", id=job.get("id"), alive=alive, age_seconds=round(age))
        _settle(job, ok=False, transient=True)
    return reaped


def _settle(job: dict[str, Any], *, ok: bool, transient: bool = False) -> None:
    """Decide what happens to a job after a run — in place, caller saves."""
    job["running_pid"] = None
    job["running_since"] = ""
    job["last_run"] = iso(now_utc())

    if ok:
        job["attempts"] = 0
        job["last_error"] = ""
        job["runs"] = int(job.get("runs", 0)) + 1
        if job.get("cron"):
            nxt = next_cron(job["cron"], job.get("timezone", ""))
            job["next_run"] = iso(nxt) if nxt else ""
            job["state"] = "pending" if nxt else "failed"
        else:
            job["state"] = "done"
        return

    if not transient:
        job["state"] = "failed"
        return

    attempts = int(job.get("attempts", 0)) + 1
    job["attempts"] = attempts
    if attempts > MAX_ATTEMPTS:
        # Out of retries. A recurring job returns to its normal schedule
        # rather than dying — one bad morning should not end a daily job.
        if job.get("cron"):
            nxt = next_cron(job["cron"], job.get("timezone", ""))
            job["next_run"] = iso(nxt) if nxt else ""
            job["state"] = "pending" if nxt else "failed"
            job["attempts"] = 0
        else:
            job["state"] = "failed"
        return

    backoff = RETRY_BACKOFF_SECONDS[attempts - 1]
    job["next_run"] = iso(now_utc() + timedelta(seconds=backoff))
    job["state"] = "pending"


def _claim(grace: int) -> list[dict[str, Any]]:
    """Mark every due job as running and return copies of them.

    Everything here happens under the lock and before a single heavy import,
    which is what stops the next tick from starting the same job again.
    """
    claimed: list[dict[str, Any]] = []
    with locked(blocking=False) as got:
        if not got:
            return []

        data = load()
        # Reaping mutates the store, so its result has to count towards
        # `changed` — otherwise a tick that only reaps saves nothing and the
        # dead job stays "running" for ever, invisible and never retried.
        moment = now_utc()
        changed = _reap(data) > 0

        for job in data.get("jobs", []):
            if job.get("state") != "pending":
                continue
            due = parse_iso(job.get("next_run", ""))
            if due is None or due > moment:
                continue

            late = (moment - due).total_seconds()
            if late > grace:
                # Too late to be useful. Skip to the next occurrence rather
                # than firing a stale reminder; one-shots are dropped.
                record_run(event="missed", id=job.get("id"), late_seconds=round(late))
                if job.get("cron"):
                    nxt = next_cron(job["cron"], job.get("timezone", ""))
                    job["next_run"] = iso(nxt) if nxt else ""
                    job["state"] = "pending" if nxt else "failed"
                else:
                    job["state"] = "missed"
                changed = True
                continue

            job["state"] = "running"
            job["running_pid"] = os.getpid()
            job["running_since"] = iso(moment)
            changed = True
            claimed.append(dict(job))

        if changed:
            save(data)
    return claimed


def _is_transient(error: str) -> bool:
    """Reuse the vocabulary the LLM facade already uses for failover."""
    lowered = (error or "").lower()
    return any(token in lowered for token in (
        "429", "quota", "rate limit", "too many requests", "503", "unavailable",
        "overloaded", "timeout", "timed out", "deadline", "connection",
        "temporarily", "could not reach",
    ))


def run_job(job: dict[str, Any]) -> tuple[bool, str, str]:
    """Execute one claimed job. Returns (ok, text, error).

    Every heavy import lives in here, so a tick that finds nothing due never
    pays for them.
    """
    kind = job.get("kind", "notify")
    prompt = job.get("prompt", "")

    if kind == "notify":
        return True, prompt, ""

    try:
        from .agent import Agent
    except Exception as exc:
        return False, "", f"could not load the agent: {exc}"

    # A refused confirmation does not make the *turn* fail — the agent
    # answers, explaining it could not do the thing. That reads as success,
    # which is the wrong signal for a job the user is not watching: the whole
    # point is that they find out it did not happen. Watch the tool results
    # for a refusal and report it as a failure.
    refusals: list[str] = []

    def watch(kind: str, text: str, data: dict) -> None:
        if kind != "tool_result":
            return
        lowered = (text or "").lower()
        if "no way to ask" in lowered or "needs confirmation" in lowered:
            refusals.append(str(data.get("name") or "a tool"))

    try:
        # A scheduled turn runs unattended, so it gets the daemon's policy:
        # no confirmer at all. `safety.enforce()` then refuses anything
        # destructive instead of approving it. A job must never be able to
        # authorise its own dangerous action just because nobody is watching.
        agent = Agent(
            source=f"schedule:{job.get('id', '?')}",
            confirmer=None,
            observer=watch,
        )
        turn = agent.ask(prompt)
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"

    if turn.error:
        return False, turn.reply or "", turn.error

    if refusals:
        which = ", ".join(sorted(set(refusals)))
        return False, turn.reply or "", (
            f"needed confirmation for {which} and nobody was there to give it, "
            f"so nothing was changed. {(turn.reply or '')[:200]}"
        )

    return True, turn.reply or "(no reply)", ""


def deliver(job: dict[str, Any], text: str, *, failed: bool = False) -> None:
    """Tell the user. Desktop notification always; web UI as well if it is up."""
    title = job.get("title") or "Kravein"
    if failed:
        title = f"{title} — failed"

    try:
        from .platform import notify

        notify.send(
            text or "(nothing to say)", title,
            urgency="critical" if failed else "normal",
            timeout_ms=0,
        )
    except Exception as exc:
        record_run(event="notify_failed", id=job.get("id"), error=str(exc)[:200])

    if job.get("speak") and not failed:
        try:
            from .platform import tts

            tts.speak(text, blocking=False)
        except Exception:
            pass

    _push_to_web(job, text, failed=failed)


def _push_to_web(job: dict[str, Any], text: str, *, failed: bool) -> None:
    """Best-effort push to a running `kravein serve`.

    The tick is its own short-lived process, so it cannot reach a browser
    directly — it posts to the local server, which fans the event out to
    whatever sockets are connected. A refused connection means the UI simply
    is not running, which is the normal case and not worth reporting.
    """
    try:
        import urllib.error
        import urllib.request

        from . import webauth

        token = webauth.read_token()
        if not token:
            return
        port = int(config.get("web.port", 8787) or 8787)
        payload = json.dumps({
            "id": job.get("id", ""),
            "title": job.get("title", ""),
            "text": text,
            "failed": bool(failed),
        }).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/job-event",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Host": f"127.0.0.1:{port}",
            },
            method="POST",
        )
        urllib.request.urlopen(request, timeout=3).close()
    except Exception:
        pass


def tick(grace: int = 0) -> int:
    """One scheduler pass. Returns how many jobs ran."""
    grace = grace or int(config.get("jobs.grace_seconds", DEFAULT_GRACE_SECONDS)
                         or DEFAULT_GRACE_SECONDS)

    claimed = _claim(grace)
    if not claimed:
        return 0

    for job in claimed:
        started = time.time()
        ok, text, error = run_job(job)
        elapsed = round(time.time() - started, 2)

        if ok:
            deliver(job, text)
        else:
            # Loud, not silent. A job that could not run is exactly the thing
            # the user needs to hear about, and a refused destructive action
            # lands here with the reason attached.
            deliver(job, error or "the job failed", failed=True)

        record_run(
            event="ran" if ok else "failed",
            id=job.get("id"), kind=job.get("kind"),
            seconds=elapsed, error=error[:400],
        )

        with locked() as got:
            if not got:
                continue
            data = load()
            for stored in data.get("jobs", []):
                if stored.get("id") == job.get("id"):
                    stored["last_error"] = error[:400]
                    _settle(stored, ok=ok, transient=_is_transient(error))
                    break
            save(data)

    return len(claimed)


def main(argv: list[str] | None = None) -> int:
    """Tiny entry point: `python -m kravein.jobs tick`.

    Deliberately not a `cli.py` subcommand — that module imports the Agent,
    the LLM facade and the registry at import time, which would cost ~250ms on
    every one of the 1,440 ticks a day.
    """
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    command = args[0] if args else "tick"

    if command == "tick":
        try:
            ran = tick()
        except Exception as exc:
            record_run(event="tick_error", error=f"{type(exc).__name__}: {exc}")
            return 1
        return 0

    if command == "list":
        for job in all_jobs():
            print(f"{job['id']:5} {job['state']:8} {job.get('next_run', ''):26} "
                  f"{job.get('kind', ''):6} {job.get('prompt', '')[:50]}")
        return 0

    print("usage: python -m kravein.jobs [tick|list]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
