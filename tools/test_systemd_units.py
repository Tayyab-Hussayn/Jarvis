"""Static checks on the shipped systemd units and the installer that deploys them.

The installer is never run in CI, so a typo in a unit or an unbound variable
only shows up on the owner's machine. These tests make that fail here.
Run: .venv/bin/python -m pytest tools/test_systemd_units.py -q
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UNIT = REPO / "systemd" / "kravein-web.service"
INSTALLER = REPO / "install-linux.sh"


def _unit() -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for line in UNIT.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            keys.setdefault(k.strip(), []).append(v.strip())
    return keys


def test_web_unit_runs_lan_serve_with_explicit_host():
    exec_start = _unit()["ExecStart"][0]
    assert exec_start.startswith("%h/code/Kravein/.venv/bin/python")
    assert exec_start.endswith(
        "-m kravein serve --lan --host ${KRAVEIN_LAN_HOST} --no-open")


def test_web_unit_env_file_is_required_and_restart_on_failure():
    unit = _unit()
    # No leading "-": a missing file must fail the start, not expand --host to "".
    assert unit["EnvironmentFile"] == ["%h/.config/kravein/web.env"]
    assert unit["Restart"] == ["on-failure"]
    assert "network-online.target" in unit["After"][0]
    assert unit["WantedBy"] == ["default.target"]


def test_installer_has_valid_syntax():
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)


def test_installer_uses_only_defined_variables():
    # `set -u` aborts on an unbound name; VENV_DIR was used but never defined,
    # which killed the install at the WhatsApp step.
    text = INSTALLER.read_text()
    assert "set -euo pipefail" in text
    assigned = set(re.findall(r"^\s*([A-Z_][A-Z0-9_]*)=", text, re.M))
    assigned |= set(re.findall(r"for ([A-Z_][A-Z0-9_]*) in", text))
    used = set(re.findall(r"\$\{?([A-Z_][A-Z0-9_]*)", text))
    assigned |= set(re.findall(r"mapfile -t ([A-Z_][A-Z0-9_]*)", text))
    builtin = {"HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
               "BASH_SOURCE", "PATH"}
    # Names inside single-quoted heredocs/echo text (e.g. \$USER) are not code.
    unbound = used - assigned - builtin - {"USER"}
    assert not unbound, unbound


def _web_step() -> str:
    body = INSTALLER.read_text().split('bold "6d.')[1].split('bold "7.')[0]
    return body.split("\n", 1)[1]          # drop the rest of the title line


def test_installer_web_step_installs_but_never_enables():
    step = _web_step()
    # Owner decision: manual on/off only. Only the comments may say "enable".
    code = "\n".join(l for l in step.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"systemctl --user enable\b[^\n]*kravein-web", code)
    assert not re.search(r"\benable\b", code.replace("is-enabled", "").replace("disable", ""))
    assert "kravein-web.service" in step and "daemon-reload" in step
    assert "umask 077" in step and 'chmod 600 "$WEB_ENV"' in step
    assert "sudo" not in step
    assert "systemctl --user disable kravein-web.service" in step   # legacy #74 enable


def test_installer_wrapper_execs_venv_python_and_warns_off_path(tmp_path):
    step = _web_step()
    block = step.split("# The LAN address")[0]     # wrapper + PATH check only
    assert 'exec "%s/bin/python" -m kravein "$@"' in block
    assert "is not on your PATH" in block
    script = tmp_path / "wrap.sh"
    script.write_text(
        'set -euo pipefail\nok(){ :; }\nwarn(){ echo "WARN $*"; }\n'
        f'VENV="/opt/k/.venv"\nHOME="{tmp_path}"\n' + block)
    env = {"PATH": "/usr/bin:/bin"}
    for _ in range(2):                              # idempotent on re-run
        out = subprocess.run(["bash", str(script)], capture_output=True,
                             text=True, env=env, check=True).stdout
    wrapper = tmp_path / ".local" / "bin" / "kravein"
    lines = wrapper.read_text().splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert lines[-1] == 'exec "/opt/k/.venv/bin/python" -m kravein "$@"'
    assert wrapper.stat().st_mode & 0o111
    assert "not on your PATH" in out

    # A foreign file at that path is left alone.
    wrapper.write_text("#!/bin/sh\necho mine\n")
    subprocess.run(["bash", str(script)], capture_output=True, text=True,
                   env=env, check=True)
    assert wrapper.read_text() == "#!/bin/sh\necho mine\n"


@pytest.mark.skipif(shutil.which("systemd-analyze") is None,
                    reason="needs systemd-analyze")
def test_web_unit_passes_systemd_verify(tmp_path):
    # Verify the unit as the installer deploys it: template path substituted.
    deployed = tmp_path / "kravein-web.service"
    deployed.write_text(UNIT.read_text().replace("%h/code/Kravein", str(REPO)))
    result = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(deployed)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0 and not result.stderr.strip(), result.stderr
