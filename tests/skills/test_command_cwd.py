"""run_command and the terminal skills classify relative paths where they run."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import safety  # noqa: E402
from conftest import _FakeBackend, _FakeWindow, fake_desktop  # noqa: E402,F401


def _confirmer(asked):
    def ask(action, detail, danger):
        asked.append(action)
        return False
    return ask


def test_relative_cp_onto_existing_file_asks(tmp_path):
    from kravein.skills import shell as sh

    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    asked = []
    with safety.confirmer_scope(_confirmer(asked)):
        with pytest.raises(safety.ConfirmationRequired):
            sh.run_command("cp a.txt b.txt", working_directory=str(tmp_path))
    assert asked and (tmp_path / "b.txt").read_text() == "b"


def test_relative_cp_onto_new_file_runs(tmp_path):
    from kravein.skills import shell as sh

    (tmp_path / "a.txt").write_text("a")
    asked = []
    with safety.confirmer_scope(_confirmer(asked)):
        out = sh.run_command("cp a.txt new.txt", working_directory=str(tmp_path))
    assert asked == [] and "[exit 0]" in out
    assert (tmp_path / "new.txt").read_text() == "a"


def test_missing_working_directory_is_reported_before_any_prompt(tmp_path):
    from kravein.skills import shell as sh

    asked = []
    with safety.confirmer_scope(_confirmer(asked)):
        out = sh.run_command("cp a b", working_directory=str(tmp_path / "nope"))
    assert asked == [] and "does not exist" in out


def test_type_into_terminal_classifies_in_the_terminals_directory(
        fake_desktop, monkeypatch, tmp_path):
    from kravein.skills import terminal as term

    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    monkeypatch.setattr(term, "own_pids", lambda: set())
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    monkeypatch.setattr(term.desktop, "terminal_cwd", lambda w: str(tmp_path))
    fake_desktop(_FakeBackend([_FakeWindow("t", "foot", 4, pid=9)]))
    seen = []
    with safety.confirmer_scope(lambda a, d, g: (seen.append(d), False)[1]):
        with pytest.raises(safety.ConfirmationRequired):
            term.type_into_terminal(command="cp a.txt b.txt", query="foot")
    assert seen


def test_open_terminal_classifies_relative_paths_in_home(monkeypatch, tmp_path):
    from kravein.skills import terminal as term

    seen = {}
    monkeypatch.setattr(term, "classify_command",
                        lambda c, cwd=None: seen.setdefault("cwd", cwd) and safety.Verdict(
                            danger=safety.Danger.SAFE))
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    monkeypatch.setattr(term, "_pick_terminal", lambda t="": "kitty")
    monkeypatch.setattr(term.commands, "check_command_line", lambda c: (c, ""))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with safety.confirmer_scope(lambda *a: False):
        with pytest.raises(safety.ConfirmationRequired):
            term.open_terminal(command="cp a b")
    assert seen["cwd"] == tmp_path
