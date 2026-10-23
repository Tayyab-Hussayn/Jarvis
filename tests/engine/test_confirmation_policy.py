"""Tests: confirmation policy ask / decline / auto (#35).

The owner's guardrails for auto-approve are pinned here, in code:
live owner sources only, DESTRUCTIVE only, hard refusals and denial memory
first, expiry at decision time, never persisted as "on", every approval audited,
and no skill able to change the mode.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import audit, config, registry, safety  # noqa: E402
from kravein.safety import Danger, Verdict  # noqa: E402

LIVE = ["web", "web-panel", "voice", "voice-live", "cli", "cli-ask"]
NOT_LIVE = ["daemon-run", "whatsapp", "cli-run", "local", "test", "", "unknown"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Own settings dir, no auto-approve left over, audit captured."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(safety, "_auto_until", None)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda ev, **kw: events.append((ev, kw)))
    yield events
    safety.set_confirmer(None)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(safety.time, "monotonic", lambda: now[0])
    return now


def _verdict(danger: Danger) -> Verdict:
    return Verdict(danger, reasons=["test"])


def _run(danger, source, answer=True):
    """enforce() with a confirmer; returns how many times it was asked."""
    asked = []

    def confirm(action, detail, level):
        asked.append(action)
        return answer

    with safety.confirmer_scope(confirm):
        safety.enforce(_verdict(danger), "do the thing", skill="t", source=source,
                       targets=["thing:1"])
    return len(asked)


def _names(events):
    return [e for e, _ in events]


# -- ask (today's behaviour) ------------------------------------------------

@pytest.mark.parametrize("source", LIVE + NOT_LIVE)
@pytest.mark.parametrize("danger", [Danger.DESTRUCTIVE, Danger.CRITICAL])
def test_ask_mode_always_asks(source, danger):
    assert safety.confirmation_policy()["mode"] == "ask"
    assert _run(danger, source) == 1


def test_ask_without_confirmer_denies():
    with safety.confirmer_scope(None):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(_verdict(Danger.DESTRUCTIVE), "x", source="web")


def test_declined_confirmation_still_denies():
    with pytest.raises(safety.ConfirmationRequired):
        _run(Danger.DESTRUCTIVE, "web", answer=False)


# -- decline ----------------------------------------------------------------

@pytest.mark.parametrize("source", LIVE + NOT_LIVE)
@pytest.mark.parametrize("danger", [Danger.DESTRUCTIVE, Danger.CRITICAL])
def test_decline_refuses_without_asking(source, danger):
    safety.set_confirmation_mode("decline", by="test")
    asked = []
    with safety.confirmer_scope(lambda *a: asked.append(a) or True):
        with pytest.raises(safety.SafetyError, match='policy is "decline"'):
            safety.enforce(_verdict(danger), "do the thing", source=source)
    assert asked == []


def test_decline_beats_pre_confirmed():
    safety.set_confirmation_mode("decline")
    with pytest.raises(safety.SafetyError):
        safety.enforce(_verdict(Danger.DESTRUCTIVE), "x", pre_confirmed=True)


def test_decline_does_not_touch_safe_or_moderate():
    safety.set_confirmation_mode("decline")
    safety.enforce(_verdict(Danger.MODERATE), "x", source="web")


def test_legacy_allow_destructive_false_means_decline():
    settings = config.load_settings()
    settings["safety"]["allow_destructive"] = False
    config.save_settings(settings)
    assert safety.confirmation_policy()["mode"] == "decline"
    with pytest.raises(safety.SafetyError, match="decline"):
        safety.enforce(_verdict(Danger.DESTRUCTIVE), "x", source="web")


def test_picking_ask_clears_legacy_decline():
    settings = config.load_settings()
    settings["safety"]["allow_destructive"] = False
    config.save_settings(settings)
    safety.set_confirmation_mode("ask")
    assert safety.confirmation_policy()["mode"] == "ask"


def test_decline_survives_in_settings_and_beats_running_auto():
    safety.set_confirmation_mode("auto", 600)
    settings = config.load_settings(reload=True)
    settings["safety"]["confirmation_mode"] = "decline"
    config.save_settings(settings)
    assert safety.confirmation_policy()["mode"] == "decline"


# -- auto -------------------------------------------------------------------

@pytest.mark.parametrize("source", LIVE)
def test_auto_approves_destructive_from_live_sources_and_audits(source, isolated):
    safety.set_confirmation_mode("auto", by="test")
    assert _run(Danger.DESTRUCTIVE, source) == 0
    ev = [kw for name, kw in isolated if name == "safety.auto_approved"]
    assert len(ev) == 1
    assert ev[0]["skill"] == "t" and ev[0]["source"] == source
    assert ev[0]["action"] == "do the thing"


@pytest.mark.parametrize("source", LIVE)
def test_auto_still_asks_for_critical(source):
    safety.set_confirmation_mode("auto")
    assert _run(Danger.CRITICAL, source) == 1


@pytest.mark.parametrize("source", NOT_LIVE)
def test_auto_does_not_cover_other_sources(source, isolated):
    safety.set_confirmation_mode("auto")
    assert _run(Danger.DESTRUCTIVE, source) == 1
    assert "safety.auto_approved" not in _names(isolated)


@pytest.mark.parametrize("source", LIVE + NOT_LIVE)
def test_auto_with_no_confirmer_still_refuses(source):
    safety.set_confirmation_mode("auto")
    with safety.confirmer_scope(None):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(_verdict(Danger.DESTRUCTIVE), "x", source=source)


def test_auto_leaves_hard_refusals_alone():
    safety.set_confirmation_mode("auto")
    verdict = safety.check_path("/etc/passwd")
    assert verdict.blocked
    with safety.confirmer_scope(lambda *a: True):
        with pytest.raises(safety.SafetyError):
            safety.enforce(verdict, "write /etc/passwd", source="web")


def test_auto_leaves_denial_memory_alone():
    safety.set_confirmation_mode("auto")
    with safety.confirmer_scope(lambda *a: True):
        # Declined earlier this turn (e.g. before auto was switched on).
        safety._denied_targets().add("thing:1")
        with pytest.raises(safety.SafetyError, match="already declined"):
            safety.enforce(_verdict(Danger.DESTRUCTIVE), "do the thing",
                           source="web", targets=["thing:1"])


def test_auto_expires_at_decision_time(clock):
    safety.set_confirmation_mode("auto", 60)
    assert _run(Danger.DESTRUCTIVE, "web") == 0
    clock[0] += 61  # no timer fired; the next decision must notice
    assert _run(Danger.DESTRUCTIVE, "web") == 1
    assert safety.confirmation_policy()["mode"] == "ask"


def test_policy_reports_seconds_left(clock):
    p = safety.set_confirmation_mode("auto", 120)
    assert p["mode"] == "auto" and p["seconds_left"] == 120
    clock[0] += 30
    assert safety.confirmation_policy()["seconds_left"] == 90


def test_auto_default_duration_and_cap():
    p = safety.set_confirmation_mode("auto")
    assert p["seconds_left"] == safety.AUTO_DEFAULT_SECONDS == 3600
    p = safety.set_confirmation_mode("auto", 10**9)
    assert p["seconds_left"] == safety.AUTO_MAX_SECONDS


@pytest.mark.parametrize("bad", [0, -5])
def test_auto_rejects_non_positive_duration(bad):
    with pytest.raises(ValueError):
        safety.set_confirmation_mode("auto", bad)


def test_unknown_mode_rejected_and_nothing_changes():
    with pytest.raises(ValueError):
        safety.set_confirmation_mode("yolo")
    assert safety.confirmation_policy()["mode"] == "ask"


def test_disable_auto_approve():
    safety.set_confirmation_mode("auto")
    assert safety.disable_auto_approve(by="web")["mode"] == "ask"
    assert _run(Danger.DESTRUCTIVE, "web") == 1
    assert safety.disable_auto_approve()["mode"] == "ask"  # idempotent


def test_switching_to_ask_turns_auto_off():
    safety.set_confirmation_mode("auto")
    safety.set_confirmation_mode("ask")
    assert _run(Danger.DESTRUCTIVE, "web") == 1


def test_mode_changes_are_audited(isolated):
    safety.set_confirmation_mode("auto", 90, by="web")
    ev = [kw for name, kw in isolated if name == "safety.mode_changed"]
    assert ev == [{"mode": "auto", "seconds": 90, "by": "web"}]


# -- restart ----------------------------------------------------------------

def test_auto_is_never_persisted_as_on(tmp_path):
    safety.set_confirmation_mode("auto", 600)
    stored = json.loads((tmp_path / "settings.json").read_text())
    assert stored["safety"]["confirmation_mode"] == "ask"
    assert stored["safety"]["allow_destructive"] is True


def test_stored_auto_reads_as_ask():
    settings = config.load_settings()
    settings["safety"]["confirmation_mode"] = "auto"
    config.save_settings(settings)
    assert safety.confirmation_policy()["mode"] == "ask"


def test_restart_comes_back_as_ask(tmp_path):
    safety.set_confirmation_mode("auto", 600)
    code = "from kravein import safety; print(safety.confirmation_policy()['mode'])"
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path.parent), "PYTHONPATH": str(ROOT)}
    # Same dir the app derives from XDG_CONFIG_HOME.
    app_dir = config._app_dir(tmp_path.parent)
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "settings.json").write_text((tmp_path / "settings.json").read_text())
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "ask", out.stderr


# -- the model cannot change it ---------------------------------------------

def test_no_skill_touches_the_policy():
    pattern = re.compile(
        r"set_confirmation_mode|disable_auto_approve|_auto_until|_persist_mode"
        r"|confirmation_mode|AUTO_SOURCES"
    )
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "kravein" / "skills").rglob("*.py")
        if pattern.search(p.read_text())
    ]
    assert offenders == []


def test_settings_file_is_not_writable_by_a_skill():
    verdict = safety.check_path(config.settings_path(), write=True)
    assert verdict.blocked and "settings" in verdict.block_reason
    # Not only the exact spelling: a symlinked / relative-looking path too.
    alias = Path(config.settings_path().parent) / "." / "settings.json"
    assert safety.check_path(alias, write=True).blocked


def test_registry_has_no_skill_for_the_policy():
    names = " ".join(registry.REGISTRY) if hasattr(registry, "REGISTRY") else ""
    assert "confirmation" not in names and "auto_approve" not in names


# -- phone skills ride the same policy --------------------------------------

def _phone(monkeypatch):
    from kravein import phone
    from kravein.skills import phone as ps

    link = {"device_id": "dev-pixel", "name": "Pixel",
            "capabilities": sorted(phone.command_names())}
    calls = []
    monkeypatch.setattr(phone, "connected", lambda: [dict(link)])
    monkeypatch.setattr(phone, "call",
                        lambda c, a=None, **k: calls.append(c) or dict(a or {}))
    return ps, calls


def test_phone_open_url_auto_from_web_asks_from_whatsapp_refused_unattended(monkeypatch):
    ps, calls = _phone(monkeypatch)
    safety.set_confirmation_mode("auto")
    prompts = []

    with safety.confirmer_scope(lambda a, d, g: prompts.append(a) or True):
        ps.phone_open_url(url="https://example.com", _source="web")
        assert prompts == [] and calls == ["open_url"]

        safety._denied_targets().clear()
        ps.phone_open_url(url="https://example.com", _source="whatsapp")
        assert len(prompts) == 1 and calls == ["open_url", "open_url"]

    safety._denied_targets().clear()
    with safety.confirmer_scope(None):
        with pytest.raises(safety.ConfirmationRequired):
            ps.phone_open_url(url="https://example.com", _source="daemon-run")
    assert calls == ["open_url", "open_url"]


# -- shell cannot rewrite the settings file either (#40) ----------------------

def _spellings(path: Path) -> list[str]:
    p = str(path)
    return [
        f"sed -i 's/true/false/' {p}",
        f"echo '{{}}' > {p}",
        f"echo '{{}}' >{p}",
        f"cp /tmp/x.json {p}",
        f"mv /tmp/x.json {p}",
        f"cat /tmp/x.json | tee {p}",
        f"tee -a {p} < /tmp/x.json",
        f"dd if=/tmp/x.json of={p}",
        f"ln -sf /tmp/x.json {p}",
        f"vim {p}",
        f"python3 -c \"open('{p}','w').write('{{}}')\"",
        f"cd {path.parent} && echo '{{}}' > settings.json",
        f"echo '{{}}' > \"{p}\"",
        f"sed -i s/true/false/ {path.parent}/*.json",
        f"sed -i s/true/false/ {path.parent}/settings.js*",
        f"cd {path.parent} && sed -i s/true/false/ *",
        f"cp -r /tmp/evil/. {path.parent}/",
        f"cp -r /tmp/evil/. {path.parent}",
        f"rsync -a /tmp/evil/ {path.parent}/./",
    ]


def test_shell_writes_to_settings_are_blocked(tmp_path):
    for cmd in _spellings(config.settings_path()):
        v = safety.classify_command(cmd)
        assert v.blocked, cmd
        assert "settings file" in v.block_reason


def test_shell_settings_block_survives_auto_and_confirmation():
    safety.set_confirmation_mode("auto")
    cmd = f"sed -i 's/a/b/' {config.settings_path()}"
    with safety.confirmer_scope(lambda *a: True):
        with pytest.raises(safety.SafetyError, match="settings file"):
            safety.enforce(safety.classify_command(cmd), f"run `{cmd}`",
                           source="web", pre_confirmed=True)


def test_shell_settings_block_handles_home_and_env_spellings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_DIR", Path.home() / ".config" / "kravein")
    assert safety.classify_command("echo x > ~/.config/kravein/settings.json").blocked
    monkeypatch.setenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    assert safety.classify_command(
        "echo x > $XDG_CONFIG_HOME/kravein/settings.json").blocked
    assert safety.classify_command(
        "echo x > ${HOME}/.config/kravein/./settings.json").blocked


def test_shell_may_still_read_settings_and_write_other_settings_files():
    path = config.settings_path()
    assert not safety.classify_command(f"cat {path}").blocked
    assert not safety.classify_command(f"grep protected {path} | head").blocked
    assert not safety.classify_command("jq . settings.json").blocked
    other = "echo '{}' > /tmp/some/other/settings.json"
    assert not safety.classify_command(other).blocked
    assert not safety.classify_command("echo x > /tmp/mysettings.json").blocked


def test_shell_may_still_list_and_read_the_settings_directory():
    d = config.settings_path().parent
    assert not safety.classify_command(f"ls {d}/").blocked
    assert not safety.classify_command(f"cat {d}/*.json").blocked
    assert not safety.classify_command(f"cd {d} && ls").blocked


def test_shell_glob_and_dir_spellings_with_home_and_env(monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", Path.home() / ".config" / "kravein")
    for cmd in (
        "sed -i s/true/false/ ~/.config/kravein/*.json",
        "sed -i s/true/false/ ~/.config/kravein/settings.js*",
        "cd ~/.config/kravein && sed -i s/true/false/ *",
        "cp -r /tmp/evil/. ~/.config/kravein/",
        "cp -r /tmp/evil/. $HOME/.config/kravein",
    ):
        assert safety.classify_command(cmd).blocked, cmd
    assert not safety.classify_command("cat ~/.config/kravein/*.json").blocked
    assert not safety.classify_command("ls ~/.config/kravein/").blocked
