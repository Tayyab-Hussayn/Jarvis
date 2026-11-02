"""Tests: inline interpreter code is DESTRUCTIVE, never "Always" (#206).

`python3 -c 'import shutil; shutil.rmtree(...)'` classified SAFE and ran with no
card, although hard-won-safety.md names interpreter one-liners as the obvious
workaround after a declined `rm`. Kravein does not parse per API: inline code
(-c/-e/-r flag, code on stdin, heredoc, eval) asks; `sh -c STRING` is judged by
its inner string; running a script *file* keeps its verdict (documented residue).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import audit, config, safety  # noqa: E402
from kravein.safety import Danger, classify_command  # noqa: E402

RM = 'import shutil; shutil.rmtree("/home/u/victim")'


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
    return home


INLINE = [
    # the reported cases
    f"python3 -c '{RM}'", "python3 -c 'import os; os.remove(\"/tmp/x\")'",
    "python -c 'open(\"/tmp/x\",\"w\")'", "python3.12 -c 'x'", "python2 -c 'x'", "pypy3 -c 'x'",
    # flag spellings
    "python3 -Bc 'x'", "python3 -W ignore -c 'x'", "python3 -X dev -c 'x'", "python3 -Ic 'x'",
    "python3 -c'x'", "python3 -Werror -c 'x'",
    "node -e 'x'", "node -p 'x'", "node -pe 'x'", "node --eval 'x'", "node --eval='x'",
    "nodejs -e 'x'", "node -r fs -e 'x'", "deno eval 'x'", "bun -e 'x'", "bun --eval 'x'",
    "ruby -e 'x'", "ruby -rjson -e 'x'", "perl -e 'x'", "perl -E 'say 1'", "perl -le 'x'",
    "perl -ne 'x' f", "perl -pe 'x' f", "perl -i -pe 's/a/b/' f", "perl -MList::Util=sum -e 'x'",
    "php -r 'x;'", "php -R 'x;'", "lua -e 'x'", "luajit -e 'x'", "osascript -e 'x'",
    "bash -c 'rm -f /tmp/x'", "sh -c 'rm x'", "zsh -c 'rm x'", "dash -c 'rm x'",
    "eval 'ls'", "eval $X",
    # stdin and heredoc forms
    "python3", "python3 -", "python3 < /tmp/x.py", "echo 'print(1)' | python3",
    "cat x.py | python3 -", "node", "node < x.js", "echo x | node", "ruby < x.rb", "perl < x.pl",
    "echo x | php", "bash", "sh", "echo 'rm x' | sh", "echo x | bash -s", "bash < x.sh",
    "python3 - <<'EOF'\nimport os\nos.remove('/tmp/x')\nEOF",
    "python3 <<EOF\nprint(1)\nEOF", "python3 <<< 'print(1)'", "bash <<EOF\nls\nEOF",
    "node <<EOF\nconsole.log(1)\nEOF", "perl <<EOF\nprint 1\nEOF",
    # through wrappers and compounds
    "env python3 -c 'x'", "env FOO=1 python3 -c 'x'", "env -i python3 -c 'x'",
    "env -S 'python3 -c x'", "nohup python3 -c 'x'", "nice -n 5 python3 -c 'x'",
    "timeout 5 python3 -c 'x'", "timeout -s KILL 5 python3 -c 'x'", "time python3 -c 'x'",
    "command python3 -c 'x'", "exec python3 -c 'x'", "stdbuf -o0 python3 -c 'x'",
    "setsid python3 -c 'x'", "xargs -I{} python3 -c 'x'", "xargs -n1 node -e 'x'",
    "busybox sh -c 'rm x'",
    "ls && python3 -c 'x'", "ls; python3 -c 'x'", "ls | python3 -c 'x'", "ls || node -e 'x'",
    "echo $(python3 -c 'x')", "echo `python3 -c 'x'`", "x=$(python3 -c 'x')",
    "python3 -c 'x' 2>/dev/null",
    "bash -c \"python3 -c 'x'\"", "sh -c \"sh -c 'rm x'\"",
    # awk with side effects
    "awk 'BEGIN{system(\"rm x\")}'", "awk '{print > \"out\"}' f", "awk '{print | \"sh\"}' f",
    "awk 'BEGIN{while((\"ls\" | getline l)>0) print l}'", "gawk -e 'BEGIN{system(\"x\")}'",
]


@pytest.mark.parametrize("command", INLINE)
def test_inline_interpreter_code_asks_and_never_offers_always(command, env):
    v = classify_command(command, cwd=env)
    assert not v.blocked, command
    assert v.needs_confirmation, command
    assert v.signature == "", command


SCRIPT_OR_READ = [
    "python3 script.py", "python3 script.py --retries 3", "python3 -m pytest -q",
    "python3 -m http.server", "python3 --version", "python3 -V", "python3 -h",
    "node server.js", "node --version", "node -v", "ruby script.rb", "ruby --version",
    "perl script.pl", "perl -Mfeature=say script.pl", "perl -v", "php script.php",
    "php -S localhost:8000", "php -f x.php", "lua script.lua", "bash script.sh", "sh ./run.sh",
    "bash --version", "awk '{print $1}' f", "awk -F, '{print $2}' f", "awk -f prog.awk f",
    "command -v python3", "which python3", "type python3", "whereis node", "pgrep python",
    "pgrep -f node", "ps aux | grep python", "grep -rn 'python3 -c' .", "echo python3 -c x",
    "ls python3", "man python3", "pip list", "git log --grep=python",
]


@pytest.mark.parametrize("command", SCRIPT_OR_READ)
def test_scripts_modules_help_and_lookups_keep_their_verdict(command, env):
    v = classify_command(command, cwd=env)
    assert not v.needs_confirmation, command


def test_script_file_is_the_documented_residue(env):
    # we do not read the file: a script that deletes things still runs unasked
    (env / "s.py").write_text("import shutil; shutil.rmtree('/tmp/zz')")
    assert classify_command(f"python3 {env}/s.py", cwd=env).danger == Danger.SAFE


# -- sh -c: judged by the inner string -----------------------------------------

@pytest.mark.parametrize("command", [
    "bash -c 'ls'", "sh -c 'ls -la 2>/dev/null'", "bash -c 'git status'",
    "sh -c 'cat /etc/hostname | wc -l'", "bash -c 'pwd && ls'",
])
def test_sh_c_with_a_safe_inner_string_stays_safe(command, env):
    assert classify_command(command, cwd=env).danger == Danger.SAFE, command


@pytest.mark.parametrize("command", [
    "bash -c 'rm -rf x'", "sh -c 'rm x'", "bash -c 'ls; rm x'", "bash -c 'find . -delete'",
    "bash -c 'echo hi > /tmp/exists.txt'", "sh -c 'python3 -c x'", "bash -c 'sed -i s/a/b/ f'",
    "bash -lc 'rm x'", "bash -ec 'rm x'",
])
def test_sh_c_with_an_unsafe_inner_string_asks(command, env):
    (Path("/tmp") / "exists.txt").touch()
    v = classify_command(command, cwd=env)
    assert v.needs_confirmation and v.signature == "", command


def test_sh_c_inner_hard_refusals_and_protected_paths_propagate(env):
    assert classify_command("bash -c 'rm -rf /'", cwd=env).blocked
    assert classify_command("sh -c 'mkfs.ext4 /dev/sda1'", cwd=env).blocked
    assert classify_command("bash -c 'cp /tmp/a /etc/x'", cwd=env).blocked
    # nesting is bounded, and a deep inner string is still judged
    deep_safe = "bash -c \"bash -c \\\"bash -c 'bash -c ls'\\\"\""
    assert classify_command(deep_safe, cwd=env).danger == Danger.SAFE
    deep_rm = "bash -c \"bash -c \\\"bash -c 'bash -c \\\\\"rm x\\\\\"'\\\"\""
    assert classify_command(deep_rm, cwd=env).needs_confirmation
    # a program path under a protected root is refused outright (older rule)
    v = classify_command("/usr/bin/python3 -c 'x'", cwd=env)
    assert v.blocked or v.needs_confirmation


def test_unit_creation_through_an_interpreter_still_asks(env):
    # the write-target guard sees the path inside inline code too, via the
    # inline-code rule rather than per-API parsing
    v = classify_command(
        f"python3 -c \"open('{env}/.config/autostart/e.desktop','w')\"", cwd=env)
    assert v.needs_confirmation and v.signature == ""


# -- the declined-target memory sees paths inside a one-liner -------------------

def _ask(command, env, *, answer, memory, targets=None):
    asked = []

    def confirm(a, d, level):
        asked.append(a)
        return answer

    verdict = classify_command(command, cwd=env)
    with safety.confirmer_scope(confirm, memory, principal="browser"):
        safety.enforce(verdict, f"run `{command}`", skill="run_command",
                       source="web", targets=targets)
    return asked


def test_declined_rm_retried_through_a_python_one_liner_is_refused(env):
    victim = env / "victim.txt"
    victim.write_text("keep")
    memory = safety.TurnMemory()
    with pytest.raises(safety.ConfirmationRequired):
        _ask(f"rm -f {victim}", env, answer=False, memory=memory)
    # same path, now through inline code, in the same request: refused, not asked
    for retry in (
        f"python3 -c 'import os; os.remove(\"{victim}\")'",
        f"python3 -c \"import shutil; shutil.rmtree('{victim}')\"",
        f"node -e \"require('fs').unlinkSync('{victim}')\"",
        f"perl -e 'unlink q({victim})'",
        f"ruby -e 'File.delete(%q({victim}))'",
        f"python3 -c 'from pathlib import Path; Path(\"{victim}\").unlink()'",
    ):
        asked = []

        def confirm(a, d, level, asked=asked):
            asked.append(a)
            return True

        verdict = classify_command(retry, cwd=env)
        with safety.confirmer_scope(confirm, memory, principal="browser"):
            with pytest.raises(safety.SafetyError):
                safety.enforce(verdict, f"run `{retry}`", skill="run_command", source="web")
        assert asked == [], retry
    assert memory.retried_declined is True
    assert victim.exists()


def test_declined_one_liner_blocks_a_later_rm_of_the_same_path(env):
    victim = env / "v.txt"
    victim.write_text("keep")
    memory = safety.TurnMemory()
    with pytest.raises(safety.ConfirmationRequired):
        _ask(f"python3 -c 'import os; os.remove(\"{victim}\")'", env, answer=False, memory=memory)
    with pytest.raises(safety.SafetyError):
        _ask(f"rm {victim}", env, answer=True, memory=memory)


def test_declining_one_path_does_not_lock_out_another(env):
    a, b = env / "a.txt", env / "b.txt"
    a.write_text("a")
    b.write_text("b")
    memory = safety.TurnMemory()
    with pytest.raises(safety.ConfirmationRequired):
        _ask(f"rm {a}", env, answer=False, memory=memory)
    asked = _ask(f"python3 -c 'import os; os.remove(\"{b}\")'", env, answer=True, memory=memory)
    assert len(asked) == 1                                  # delete A, keep B still works


def test_a_one_liner_is_refused_unattended_and_remembered(env):
    memory = safety.TurnMemory()
    command = f"python3 -c 'import os; os.remove(\"{env}/x\")'"
    verdict = classify_command(command, cwd=env)
    with safety.confirmer_scope(None, memory, principal="browser"):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(verdict, f"run `{command}`", skill="run_command", source="daemon-run")
        with pytest.raises(safety.SafetyError):
            safety.enforce(classify_command(f"rm {env}/x", cwd=env), f"run `rm {env}/x`",
                           skill="run_command", source="daemon-run")


def test_always_rule_cannot_cover_a_one_liner(env):
    # even with a stored pkill rule, an interpreter command is just DESTRUCTIVE
    safety.add_always_rule("pkill firefox", "browser")
    asked = []

    def confirm(a, d, level):
        asked.append(safety.always_offer())
        return safety.ALWAYS

    command = "python3 -c 'import os'"
    with safety.confirmer_scope(confirm, principal="browser"):
        safety.enforce(classify_command(command, cwd=env), f"run `{command}`",
                       skill="run_command", source="web")
    assert asked == [{"allowed": False, "signature": ""}]
    assert [r["signature"] for r in safety.list_rules("browser")] == ["pkill firefox"]


def test_target_keys_find_paths_inside_inline_code():
    keys = safety._target_keys("run `python3 -c 'os.remove(\"/home/u/f.txt\")'`")
    assert "/home/u/f.txt" in keys
    keys = safety._target_keys("run `node -e \"fs.unlinkSync('/home/u/g')\"`")
    assert "/home/u/g" in keys
    assert "/home/u/h" in safety._target_keys("run `rm /home/u/h`")      # unchanged route
