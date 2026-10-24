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
               "BASH_SOURCE"}
    # Names inside single-quoted heredocs/echo text (e.g. \$USER) are not code.
    unbound = used - assigned - builtin - {"USER"}
    assert not unbound, unbound


def test_installer_web_step_is_opt_in_and_private():
    text = INSTALLER.read_text()
    step = text.split("6d.")[1].split('bold "7.')[0]
    assert "read -r -p" in step and '"${reply,,}" == "y"' in step
    assert "umask 077" in step and 'chmod 600 "$WEB_ENV"' in step
    assert "systemctl --user enable --now kravein-web.service" in step
    assert "sudo" not in step
    assert "disable --now kravein-web.service" in step     # prints how to turn it off


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
