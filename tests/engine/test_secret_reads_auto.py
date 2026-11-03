"""Tests: auto-approve and Always rules never answer a secret-read card (#211 review).

Auto mode is the owner's "stop asking me about destructive things" switch; a
secret read is a different kind of card (the content leaves the machine for a
cloud model), so only a live owner click may answer it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import audit, config, safety  # noqa: E402
from kravein.safety import Danger, check_read, classify_command  # noqa: E402

LIVE_SOURCES = ["web", "web-panel", "voice", "voice-live", "cli", "cli-ask"]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(safety, "_auto_until", None)
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    key = home / ".ssh" / "id_ed25519"
    key.parent.mkdir()
    key.write_text("secret\n")
    yield home
    safety.set_confirmer(None)


@pytest.fixture
def auto():
    safety.set_confirmation_mode("auto", by="test")


def _asked(verdict, source, answer=True):
    asked = []

    def confirm(a, d, level):
        asked.append(a)
        return answer

    with safety.confirmer_scope(confirm, principal="browser"):
        safety.enforce(verdict, "read it", skill="t", source=source, targets=["t:1"])
    return asked


def _both(home):
    return (check_read(home / ".ssh" / "id_ed25519"),                 # what read_file does
            classify_command("cat ~/.ssh/id_ed25519", cwd=home))      # what run_command does


def test_verdicts_carry_the_secret_read_mark(env):
    a, b = _both(env)
    assert a.secret_read and b.secret_read
    assert not check_read(env / "notes.txt").secret_read
    assert not classify_command("pkill firefox", cwd=env).secret_read


@pytest.mark.parametrize("source", LIVE_SOURCES)
def test_auto_mode_still_asks_for_a_secret_read(env, auto, source):
    for v in _both(env):
        assert _asked(v, source) == ["read it"]
        with pytest.raises(safety.ConfirmationRequired):               # a no is a no
            _asked(v, source, answer=False)


@pytest.mark.parametrize("source", LIVE_SOURCES + ["whatsapp", "local"])
def test_a_secret_read_with_no_confirmer_is_denied_even_in_auto(env, auto, source):
    for v in _both(env):
        with safety.confirmer_scope(None):
            with pytest.raises(safety.ConfirmationRequired):
                safety.enforce(v, "read it", skill="t", source=source)


def test_an_ordinary_destructive_command_is_still_auto_approved(env, auto):
    v = classify_command("pkill firefox", cwd=env)
    assert v.danger == Danger.DESTRUCTIVE and not v.secret_read
    assert _asked(v, "web") == []


def test_a_matching_always_rule_does_not_answer_a_secret_read(env):
    v = classify_command("cat ~/.ssh/id_ed25519", cwd=env)
    # Even a forged signature with a stored rule for it must not apply.
    v.signature = "cat secret"
    safety.add_always_rule("cat secret", "browser")
    assert _asked(v, "web") == ["read it"]
