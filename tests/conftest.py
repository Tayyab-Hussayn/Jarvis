import sys
from pathlib import Path

# Ensure tests can import the application package from the workspace root.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest  # noqa: E402

from kravein import llm  # noqa: E402,F401


# Helpers and fixtures shared by more than one tests/<module>/ file.

class _StubProvider:
    """Scripted provider so the loop can be tested without a network call."""

    name = "cline"   # must match a real provider name for ordering

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen_tools = None
        self.seen_system = ""

    def available(self):
        return True

    def complete(self, messages, *, system="", tools=None, temperature=0.4, model=""):
        self.seen_tools = tools
        self.seen_system = system
        reply = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return reply


def _agent_with(script, **kwargs):
    from kravein.agent import Agent

    stub = _StubProvider(script)
    agent = Agent(source="test", **kwargs)
    # Replace every provider, so the loop can never reach a real backend.
    agent._llm.cline = stub
    agent._llm.gemini = stub
    agent._llm.openrouter = stub
    return agent, stub


@pytest.fixture
def habits(tmp_path, monkeypatch):
    """Point the routine store at a temp dir, and never deliver for real."""
    from kravein import delivery, routines as routines_mod

    monkeypatch.setattr(routines_mod, "STORE", tmp_path / "routines.json")
    monkeypatch.setattr(routines_mod, "LOCK", tmp_path / "routines.lock")
    monkeypatch.setattr(routines_mod, "HISTORY", tmp_path / "routine-days.jsonl")

    sent: list[dict] = []
    monkeypatch.setattr(
        delivery, "deliver",
        lambda text, **kw: sent.append({"text": text, **kw}) or {},
    )
    routines_mod.sent = sent          # handed to the test, not part of the module
    return routines_mod


def _morning(store, tz="UTC", day_start="04:00"):
    """Wake 07:00 → Breakfast 30m after it is actually confirmed → Meds after that."""
    routine = store.add_routine("Morning", day_start=day_start, tz_name=tz)
    store.add_step(routine["id"], label="Wake up", at="07:00", points=10,
                   window_minutes=60, nag={"every_minutes": 10, "max": 3})
    store.add_step(routine["id"], label="Breakfast", after_step="s1", after_minutes=30,
                   points=10, window_minutes=90, nag={"every_minutes": 15, "max": 2})
    store.add_step(routine["id"], label="Meds", after_step="s2", after_minutes=5,
                   points=10, window_minutes=60, nag={"every_minutes": 10, "max": 1})
    return routine["id"]


def _at(day, hour, minute=0):
    from datetime import datetime, timezone

    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def _states(store, routine_id):
    data = store.load()
    return {k: v["state"] for k, v in data["today"][routine_id]["steps"].items()}


class _FakeWindow:
    def __init__(self, wid, app, workspace, pid=9999, title="t"):
        self.id, self.app, self.workspace = wid, app, str(workspace)
        self.pid, self.title, self.focused = pid, title, False


class _FakeBackend:
    """Closes on request unless the window is listed as stubborn."""

    name = "fake"

    def __init__(self, windows, stubborn=(), accepts=True):
        self.windows = list(windows)
        self.stubborn = set(stubborn)
        self.accepts = accepts
        self.closed: list[str] = []

    def available(self):
        return True

    def list_windows(self):
        return list(self.windows)

    def focused(self):
        return self.windows[0] if self.windows else None

    def close(self, window):
        self.closed.append(window.id)
        if window.id not in self.stubborn:
            self.windows = [w for w in self.windows if w.id != window.id]
        return self.accepts

    kill = close


@pytest.fixture
def fake_desktop(monkeypatch):
    from kravein.skills import windows as win

    def install(backend):
        monkeypatch.setattr(win.desktop, "backend", lambda: backend)
        return backend

    return install
