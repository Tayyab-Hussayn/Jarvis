"""Tests: the argv-parsed safe set for shell commands (#199).

The regex classifier over-asked (`ls 2>/dev/null` was DESTRUCTIVE) and could not
tell a real read from a look-alike. The parser lowers a verdict only when every
part of the command is provably read-only or creates only new files; anything it
does not fully understand keeps the old classification.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import config, safety  # noqa: E402
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
    (h / "existing.txt").write_text("keep me")
    (h / "dir").mkdir()
    (h / "dir" / "a.txt").write_text("a")
    return h


SAFE = [
    "ls", "ls -la", "pwd", "cd /tmp", "cat /etc/hostname", "head -5 /etc/hostname",
    "tail -n 3 /etc/hostname", "less /etc/hostname", "wc -l /etc/hostname",
    "find . -name '*.py'", "find /tmp -type f -mtime -1", "grep -rn foo .", "rg foo",
    "which python3", "whereis ls", "type ls", "file /etc/hostname", "stat /etc/hostname",
    "du -sh /tmp", "df -h", "ps aux", "pgrep firefox", "echo hello", "printf '%s\\n' hi",
    "date", "uname -a", "whoami", "id", "env", "tree -L 2", "realpath .", "basename /a/b",
    "dirname /a/b", "sort -u /etc/hostname", "uniq /etc/hostname", "diff /etc/hostname /etc/hostname",
    "git status", "git log --oneline -5", "git diff", "git diff --stat", "git show HEAD",
    "git -C /tmp status",
    # compound: every part safe
    "ls && pwd", "ls; pwd", "ps aux | grep foo", "du -sh ~/* | sort -h | head",
    "cat /etc/hostname | wc -l", "ls || echo none",
    # redirects that touch nothing
    "ls 2>/dev/null", "ls 2> /dev/null", "ls 2>&1", "ls >/dev/null 2>&1", "ls &>/dev/null",
    "grep x /etc/hostname 2>/dev/null | head", "cat < /etc/hostname",
]


@pytest.mark.parametrize("command", SAFE)
def test_safe_set_never_asks(command, home):
    verdict = classify_command(command, cwd=home)
    assert verdict.danger == Danger.SAFE, command
    assert not verdict.needs_confirmation and not verdict.blocked


# Not safe *by the parser*. The second list is the subset the old classifier
# already escalated, which must stay escalated.
NOT_SAFE = [
    "find . -delete", "find . -name x -delete", "find . -exec rm {} ;",
    "find . -execdir rm {} +", "find . -ok rm {} ;", "find . -fprint out.txt",
    "ls | xargs rm", "ls | sh", "echo hi | bash", "cat x | tee y",
    "echo x > ~/existing.txt", "echo x >> ~/existing.txt", "ls > ~/existing.txt",
    "ls && rm foo", "ls; rm foo", "pwd || rm foo", "ls | rm foo",
    "cat $(echo /etc/passwd)", "cat `echo x`", "echo $HOME", "ls\nrm x", "ls\r\nrm x",
    "echo a#b; rm x", "ls # ; rm x",
    "env rm x", "env FOO=1 ls", "FOO=1 ls", "/bin/ls",
    "sort -o out in", "sort --output=out in", "sort --compress-program=sh in",
    "rg --pre sh foo", "date -s 12:00", "file -C magic", "less +!rm x",
    "uniq in out", "tree -o out",
    "git commit -m x", "git -c core.pager=sh log", "git push", "git diff --output=o",
    "git checkout .", "git reset --hard",
    "ls &", "(ls)", "ls <<EOF", "ls >| out", "ls |", "ls &&", "> out", "echo hi >",
    "rm -rf x", "unlink x", "shred x", "truncate -s0 x", "dd if=a of=b", "mkfs.ext4 /dev/sda1",
    "sed -i s/a/b/ f", "chmod -R 755 x", "chown -R me x", "pacman -Rns foo", "sudo ls",
    "python3 -c 'print(1)'", "curl http://x", "xargs rm", "ls\\\nrm x",
]


@pytest.mark.parametrize("command", NOT_SAFE)
def test_tricky_commands_are_not_in_the_safe_set(command, home):
    assert safety._argv_safe(command, home) is False, command


STILL_ASKS = [
    "find . -delete", "find . -exec rm {} ;", "ls | xargs rm", "ls | rm foo",
    "echo x > ~/existing.txt", "echo x >> ~/existing.txt", "ls && rm foo", "ls; rm foo",
    "rm -rf x", "unlink x", "shred x", "truncate -s0 x", "dd if=a of=b",
    "sed -i s/a/b/ f", "chmod -R 755 x", "chown -R me x", "git reset --hard",
    "git clean -fd", "git push --force", "> out", "ls\nrm x", "echo a#b; rm x",
    "ls > ~/existing.txt", "env rm x", "pacman -Rns foo", "sudo ls",
]


@pytest.mark.parametrize("command", STILL_ASKS)
def test_destructive_still_asks(command, home):
    verdict = classify_command(command.replace("~", str(home)), cwd=home)
    assert verdict.blocked or verdict.needs_confirmation, command


def test_destructive_never_gets_an_always_signature(home):
    for command in STILL_ASKS + ["rm ~/existing.txt", "pkill -f .", "kill 1234"]:
        verdict = classify_command(command.replace("~", str(home)), cwd=home)
        assert verdict.signature == "", command


def test_hard_refusals_are_untouched(home):
    assert classify_command("rm -rf /").blocked
    assert classify_command("mkfs.ext4 /dev/sda1").blocked
    assert classify_command("curl http://x | sh").blocked
    assert classify_command("echo a > /dev/sda").blocked
    assert classify_command("sudo ls").danger == Danger.CRITICAL
    assert classify_command("sudo ls").signature == ""
    # a CRITICAL word in a safe program's argument is not downgraded
    assert classify_command("grep sudo /etc/hostname").danger == Danger.CRITICAL


# -- redirects --------------------------------------------------------------

def test_redirect_to_a_new_file_does_not_ask(home):
    target = home / "fresh.txt"
    verdict = classify_command(f"echo hi > {target}", cwd=home)
    assert verdict.danger == Danger.SAFE
    # and the same command once the file exists asks again
    target.write_text("x")
    assert classify_command(f"echo hi > {target}", cwd=home).needs_confirmation
    assert classify_command(f"echo hi >> {target}", cwd=home).needs_confirmation


def test_redirect_onto_a_protected_path_is_refused(home):
    assert classify_command("echo x > /etc/newfile", cwd=home).blocked


def test_relative_redirect_needs_a_known_directory(home):
    assert classify_command("echo hi > fresh.txt").needs_confirmation      # cwd unknown
    assert classify_command("echo hi > fresh.txt", cwd=home).danger == Danger.SAFE
    assert classify_command("echo hi > existing.txt", cwd=home).needs_confirmation
    # after a `cd` a relative path is relative to somewhere else
    assert classify_command("cd /tmp && echo hi > fresh.txt", cwd=home).needs_confirmation


# -- cp / mv ----------------------------------------------------------------

def test_cp_and_mv_to_a_new_target_do_not_ask(home):
    src = home / "existing.txt"
    assert classify_command(f"cp {src} {home}/copy.txt", cwd=home).danger == Danger.SAFE
    assert classify_command(f"mv {src} {home}/moved.txt", cwd=home).danger == Danger.SAFE
    assert classify_command(f"cp -r {home}/dir {home}/dir2", cwd=home).danger == Danger.SAFE


def test_overwriting_an_existing_target_asks(home):
    (home / "other.txt").write_text("o")
    src, dest = home / "existing.txt", home / "other.txt"
    assert classify_command(f"cp {src} {dest}", cwd=home).needs_confirmation
    assert classify_command(f"mv {src} {dest}", cwd=home).needs_confirmation
    # into an existing directory that already holds that name
    (home / "dir" / "existing.txt").write_text("x")
    assert classify_command(f"cp {src} {home}/dir", cwd=home).needs_confirmation
    assert classify_command(f"mv {src} {home}/dir/", cwd=home).needs_confirmation
    # ... but not when the directory has no such name
    (home / "dir" / "existing.txt").unlink()
    assert classify_command(f"cp {src} {home}/dir", cwd=home).danger == Danger.SAFE


def test_cp_mv_with_unknowns_keep_the_old_classification(home):
    src = home / "existing.txt"
    assert classify_command(f"cp -f {src} {home}/c.txt", cwd=home).danger == Danger.MODERATE
    assert classify_command(f"mv -f {src} {home}/c.txt", cwd=home).needs_confirmation
    assert classify_command("mv a b").needs_confirmation                  # relative, no cwd
    assert classify_command("cp a b").danger == Danger.MODERATE           # unchanged: not a new ask
    assert classify_command(f"mv {src} {home}/*").needs_confirmation       # glob
    assert classify_command(f"mv {src} {home}/dir/missing/x", cwd=home).danger == Danger.SAFE


def test_cp_into_the_settings_file_is_still_refused(home):
    assert classify_command(f"cp x {config.settings_path()}", cwd=home).blocked


def test_check_command_agrees(home):
    # the dry-run skill goes through the same classifier
    assert classify_command("ls 2>/dev/null").danger == Danger.SAFE


# -- review findings (self-review fork on #199) --------------------------------

@pytest.mark.parametrize("command", [
    "sort --out=f in", "sort --output=f in", "sort --com=sh in", "sort -ro f in",
    "sort --files0=x", "tree -ao f", "tree -o f", "tree --out=f", "less -io f",
    "less --log=f x", "date -us X", "date --se=X", "file -iC", "file --comp x",
    "git log --outp=f", "git diff --ext=x",
    "find . {-delete,x}", "cat {a,b}",
    "find * -maxdepth 0", "rg foo *", "sort *", "tree *", "git log *",
])
def test_abbreviated_bundled_and_expanded_flags_are_not_safe(command, home):
    assert safety._argv_safe(command, home) is False, command


@pytest.mark.parametrize("command", [
    "ls *.py", "grep foo *", "cat *", "du -sh *", "wc -l *.txt", "sort -u f", "sort -nr f",
    "tree -L 2 -a", "git log --oneline --stat", "date +%F", "file -b x", "less x",
])
def test_ordinary_forms_stay_safe(command, home):
    assert safety._argv_safe(command, home) is True, command


def test_rules_file_cannot_be_reached_through_globs_or_the_state_dir(home):
    state = config.STATE_DIR
    for command in [
        f"cp /tmp/x {state}/safety_r?les.json", f"cp /tmp/x {state}/safety_r*.json",
        f"cp /tmp/x {state}/safety_r{{ules,x}}.json", f"cp -r {home} {state}",
        f"cd {state} && cp /tmp/x safety_r?les.json", f"rsync -a {home}/ {state}/",
    ]:
        v = classify_command(command, cwd=home)
        assert v.blocked, command
