"""Tests: every shell write target goes through sensitive_kind/check_path (#199 review).

`cp x ~/.config/systemd/user/evil.service` used to be MODERATE and landed a
persistent user unit with no card. Whatever the verb, and whether or not the
target exists: a login/startup surface asks (DESTRUCTIVE, never "Always");
Kravein's own config/state/data is refused.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import config  # noqa: E402
from kravein.safety import Danger, classify_command  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "_settings_cache", None)
    return h


SURFACES = {
    "autostart": ".config/autostart/evil.desktop",
    "systemd-user": ".config/systemd/user/evil.service",
    "ssh": ".ssh/authorized_keys2",
    "shell-rc": ".bashrc",
    "gnupg": ".gnupg/evil.conf",
    "fish": ".config/fish/config.fish",
}
# {t} = the write target, {d} = its directory, {name} = its file name
VERBS = {
    "cp": "cp /tmp/a {t}",
    "cp-into-dir": "cp /tmp/a {d}/",
    "cp-t": "cp -t {d} /tmp/a",
    "mv": "mv /tmp/a {t}",
    "install": "install -m 644 /tmp/a {t}",
    "ln": "ln -s /tmp/a {t}",
    "tee": "echo x | tee {t}",
    "tee-a": "echo x | tee -a {t}",
    "redirect": "echo x > {t}",
    "append": "echo x >> {t}",
    "touch": "touch {t}",
    "mkdir": "mkdir -p {t}",
    "rsync": "rsync -a /tmp/a {t}",
    "dd": "dd if=/tmp/a of={t}",
    "curl": "curl -o {t} http://example.invalid/x",
    "wget": "wget -O {t} http://example.invalid/x",
    "env-wrapped": "env FOO=1 cp /tmp/a {t}",
    "nohup": "nohup cp /tmp/a {t}",
    "chained": "ls && cp /tmp/a {t}",
    "cd-then-relative": "cd {d} && cp /tmp/a {name}",
}


def _command(verb: str, target: Path) -> str:
    return VERBS[verb].format(t=target, d=target.parent, name=target.name)


@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
@pytest.mark.parametrize("verb", sorted(VERBS))
@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_startup_surfaces_ask_and_never_offer_always(surface, verb, existing, home):
    if surface == "shell-rc" and verb in ("cp-into-dir", "cp-t"):
        pytest.skip("the directory of a shell rc is $HOME itself, not a startup surface")
    target = home / SURFACES[surface]
    target.parent.mkdir(parents=True, exist_ok=True)
    if existing:
        target.write_text("old")
    command = _command(verb, target)
    v = classify_command(command, cwd=home)
    assert not v.blocked, command
    assert v.needs_confirmation, command
    assert v.signature == "", command


@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
@pytest.mark.parametrize("verb", sorted(VERBS))
@pytest.mark.parametrize("where", ["config", "state", "data"])
def test_kravein_own_dirs_are_refused(where, verb, existing, home):
    root = {"config": config.CONFIG_DIR, "state": config.STATE_DIR, "data": config.DATA_DIR}[where]
    target = root / "keys.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if existing:
        target.write_text("old")
    command = _command(verb, target)
    assert classify_command(command, cwd=home).blocked, command


def test_mv_of_a_sensitive_source_asks(home):
    rc = home / ".bashrc"
    rc.write_text("x")
    v = classify_command(f"mv {rc} /tmp/gone", cwd=home)
    assert v.needs_confirmation and v.signature == ""


@pytest.mark.parametrize("command", [
    "cp /tmp/a {h}/.config/systemd/user/*.service",
    "cp /tmp/a {h}/.config/systemd/user/{{a,b}}.service",
    "cp /tmp/a {h}/.config/autostart/",
    "cp /tmp/a $HOME/.config/autostart/x.desktop",
    "echo x > $HOME/.ssh/authorized_keys",
    "cat /tmp/a >> {h}/.bashrc",
    "cp /tmp/a {h}/.config/sys*/user/x",
])
def test_unresolvable_sensitive_targets_ask(command, home):
    v = classify_command(command.format(h=home), cwd=home)
    assert v.needs_confirmation and not v.signature, command


def test_relative_write_after_cd_into_a_sensitive_dir_asks(home):
    (home / ".ssh").mkdir()
    v = classify_command(f"cd {home}/.ssh && cp /tmp/a authorized_keys", cwd=home)
    assert v.needs_confirmation
    # and with an unknown directory the textual guard still catches the fragment
    assert classify_command("cd ~/.ssh && cp /tmp/a authorized_keys").needs_confirmation


def test_unresolvable_kravein_targets_are_refused(home):
    v = classify_command(f"cp /tmp/a {config.STATE_DIR}/x*", cwd=home)
    assert v.blocked


def test_ordinary_write_targets_are_unchanged(home):
    assert classify_command(f"cp /tmp/a {home}/dir/b.txt", cwd=home).danger <= Danger.MODERATE
    assert classify_command(f"mkdir -p {home}/new/dir", cwd=home).danger <= Danger.MODERATE
    assert classify_command(f"touch {home}/x", cwd=home).danger <= Danger.MODERATE
    assert classify_command(f"echo x > {home}/ok.txt", cwd=home).danger == Danger.SAFE
    assert classify_command("echo x | tee /dev/null", cwd=home).danger <= Danger.MODERATE
    assert classify_command("ls 2>/dev/null", cwd=home).danger == Danger.SAFE


@pytest.mark.parametrize("command", [
    "systemctl --user enable foo.service", "systemctl --user enable --now foo.service",
    "systemctl enable --user foo.service", "systemctl --user reenable foo",
    "systemctl --user link /tmp/foo.service", "systemctl --user preset foo",
    "systemctl --user add-wants default.target foo", "systemctl --user edit foo",
    "systemctl --user set-default x", "systemd-run --user --on-calendar=daily x",
    "crontab -e", "crontab /tmp/jobs", "loginctl enable-linger me",
])
def test_unit_and_timer_creation_asks_without_always(command, home):
    v = classify_command(command, cwd=home)
    assert v.needs_confirmation and v.signature == "", command


@pytest.mark.parametrize("command", [
    "systemctl --user restart foo", "systemctl --user status foo", "systemctl --user start foo",
    "systemctl --user stop foo", "systemctl --user daemon-reload", "crontab -l",
])
def test_restart_status_stay_as_they_were(command, home):
    assert not classify_command(command, cwd=home).needs_confirmation, command
