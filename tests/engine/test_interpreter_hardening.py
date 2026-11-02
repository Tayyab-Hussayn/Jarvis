"""Tests: findings of the #206 self-review - head-of-command tricks, more
interpreters, `python -m` code runners, and a bound on analysis cost."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import audit, config, safety  # noqa: E402
from kravein.safety import Danger, classify_command  # noqa: E402


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    return home


ASKS = [
    # NAME=value words in front of the program
    "FOO=1 python3 -c 'x'", "PYTHONSTARTUP=/tmp/x.py python3", "A=1 B=2 node -e 'x'",
"FOO=1 sh -c 'python3 -c x'", "FOO=1 bash -c 'rm x'",
    "exec -a foo python3 -c x", "FOO=1 nohup python3 -c x", "time FOO=1 python3 -c x",
    # shell control words in front of the program
    "if true; then python3 -c 'x'; fi", "for i in 1; do python3 -c x; done",
    "while true; do node -e x; done", "{ python3 -c x; }", "! python3 -c x",
    "if true; then FOO=1 env python3 -c x; fi", "true && { perl -e 'x'; }",
    "until false; do ruby -e x; done", "if x; then bash -c 'rm y'; else :; fi",
    # python -m with modules that run the code you give them
    "python3 -m runpy x", "python3 -m code", "python3 -m timeit -s 'import os' 1",
    "python3 -mtimeit 1", "python3 -m cProfile -c x", "python3 -m pdb x.py",
    "python3 -m idlelib", "python3 -m pydoc -b", "python3 -m trace --count x",
    # more interpreters and versioned names
    "node18 -e 'x'", "nodejs18 -e x", "ruby3.2 -e x", "perl5.38 -e x", "luajit2 -e x",
    "ipython -c 'x'", "ipython3 -c x", "pythonw -c x", "julia -e 'x'", "julia -E x",
    "groovy -e 'x'", "Rscript -e 'x'", "jshell", "pry", "xonsh -c 'rm x'", "tcsh -c 'rm x'",
    # awk: script from stdin, includes, in-place
    "awk -f - f", "gawk '@include \"x\"' f", "gawk -i inplace '{print}' f",
    "gawk -i /tmp/x.awk 'BEGIN{}'",
    # a command nobody typed
    "ls " + "&& ls " * 6000,
]


@pytest.mark.parametrize("command", ASKS)
def test_review_gaps_now_ask(command, env):
    v = classify_command(command, cwd=env)
    assert not v.blocked, command
    assert v.needs_confirmation and v.signature == "", command[:80]


def test_awk_program_from_dev_stdin_is_not_quietly_allowed(env):
    v = classify_command("awk -f /dev/stdin f", cwd=env)
    assert v.blocked or v.needs_confirmation


STAYS = [
    "FOO=1 python3 script.py", "if true; then python3 script.py; fi", "for f in a b; do ls; done",
    "python3 -m pytest -q", "python3 -m pip list", "python3 -m venv .venv", "python3 -m http.server",
    "python3 -m json.tool x.json", "node18 app.js", "ruby3.2 app.rb", "julia script.jl",
    "Rscript script.R", "ipython --version", "awk -f prog.awk f", "gawk --version",
    "A=1 ls", "FOO=bar pwd", "time ls", "! ls",
]


@pytest.mark.parametrize("command", STAYS)
def test_script_and_module_runs_keep_their_verdict(command, env):
    assert not classify_command(command, cwd=env).needs_confirmation, command


def test_analysis_cost_is_bounded():
    for command in [
        "xargs -I{} " * 10000 + "ls", "nohup -x " * 12000, "env " * 25000, "$X " * 30000,
        "timeout 5 " * 10000, "FOO=1 " * 20000 + "ls", "{ " * 30000 + "ls",
        "bash -c '" * 200 + "ls" + "'" * 200,
    ]:
        started = time.monotonic()
        classify_command(command)
        assert time.monotonic() - started < 2.0, command[:30]


def test_a_long_but_ordinary_wrapper_chain_still_resolves(env):
    # within the bound: judged normally, not just "long"
    assert classify_command("nohup nice env timeout 5 python3 -c x", cwd=env).needs_confirmation
    assert not classify_command("nohup nice env timeout 5 ls", cwd=env).needs_confirmation


def test_wrapper_flood_counts_as_inline(env):
    v = classify_command("nohup " * 60 + "ls", cwd=env)
    assert v.needs_confirmation


def test_dotdot_in_a_one_liner_path_still_hits_the_declined_key():
    keys = safety._target_keys("run `python3 -c 'os.remove(\"/home/u/dir/../v.txt\")'`")
    assert "/home/u/v.txt" in keys
