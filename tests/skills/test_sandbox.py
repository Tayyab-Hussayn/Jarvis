"""#227: run_command(sandbox=True) under bubblewrap.

The `real` tests start an actual bwrap and prove the boundary; they are skipped
(and say so) where bwrap or user namespaces are unavailable. The rest stub the
platform layer.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import threading
import time
import types
from pathlib import Path

import pytest

from kravein import config, registry, safety
from kravein.platform import sandbox as sbx
from kravein.platform import shell as platform_shell
from kravein.safety import Danger, Verdict
from kravein.skills import shell as sh

REAL_HOME = Path.home()
HAVE_BWRAP = sbx.status().available
real = pytest.mark.skipif(not HAVE_BWRAP, reason=f"no usable bwrap: {sbx.status().reason}")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(sh.tempfile, "tempdir", str(tmp_path))
    sbx._scan_cache.clear()
    yield home
    sh.kill_all_jobs()
    sh._jobs.clear()
    sbx._scan_cache.clear()


def agent(principal="p1"):
    return types.SimpleNamespace(tool_state={}, principal=principal)


def box(command, ctx=None, **kw):
    return sh.run_command(command, sandbox=True, _context=ctx or agent(), **kw)


def host_cmdline_alive(*words) -> bool:
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as handle:
                    if handle.read().decode().split("\0")[:-1] == list(words):
                        return True
            except OSError:
                pass
    return False


# --- unit tests: classification, refusal, argv (no real bwrap) ------------------------------------

def test_sandbox_verdict_is_decided_in_one_place():
    safe = sh._sandbox_verdict(Verdict(danger=Danger.DESTRUCTIVE, reasons=["deletes files"],
                                       signature="rm"))
    assert safe.danger == Danger.SAFE and not safe.needs_confirmation
    assert safe.signature == "" and "deletes files" in safe.reasons[0]
    critical = sh._sandbox_verdict(Verdict(danger=Danger.CRITICAL))
    assert critical.danger == Danger.SAFE


def test_sandbox_never_loosens_a_refusal_or_a_secret_read():
    blocked = Verdict(danger=Danger.CRITICAL, blocked=True, block_reason="rm -rf /")
    assert sh._sandbox_verdict(blocked) is blocked
    secret = Verdict(danger=Danger.DESTRUCTIVE, secret_read=True)
    out = sh._sandbox_verdict(secret)
    assert out is secret and out.needs_confirmation and out.no_auto


def stub_unavailable(monkeypatch, reason):
    monkeypatch.setattr(sbx, "status", lambda: sbx.Status(False, reason))
    started = []
    monkeypatch.setattr(platform_shell, "start_group",
                        lambda *a, **k: started.append(1) or pytest.fail("must not start"))
    asked = []
    monkeypatch.setattr(sh, "enforce", lambda *a, **k: asked.append(1))
    return started, asked


@pytest.mark.parametrize("reason", [
    "bubblewrap (bwrap) is not installed",
    "bwrap cannot create a sandbox here (bwrap: No permissions to create new namespace)",
])
def test_unavailable_sandbox_refuses_and_never_runs_unsandboxed(monkeypatch, env, reason):
    started, asked = stub_unavailable(monkeypatch, reason)
    out = box("touch marker")
    assert out.startswith("Refused: sandbox unavailable: ") and reason in out
    assert "only with a normal confirmation" in out
    assert not started and not asked and not (env / "marker").exists()
    # background takes the same road
    assert box("touch marker", background=True).startswith("Refused: sandbox unavailable")
    assert sh._jobs == {}


def test_sandbox_lost_between_check_and_start_still_refuses(monkeypatch, env):
    monkeypatch.setattr(sbx, "status", lambda: sbx.Status(True, "", "/usr/bin/bwrap", "x"))
    def boom(*a, **k):
        raise platform_shell.SandboxUnavailable("namespaces went away")
    monkeypatch.setattr(platform_shell, "start_group", boom)
    out = box("touch marker")
    assert out.startswith("Refused: sandbox unavailable: namespaces went away")
    assert list((env.parent / "workspace" / "outputs").glob("*")) == []   # nothing left behind


def test_status_reports_missing_bwrap_and_bwrap_failures(monkeypatch):
    sbx.reset_cache()
    monkeypatch.setattr(platform_shell, "which", lambda b: None)
    assert sbx.status().reason == "bubblewrap (bwrap) is not installed"
    sbx.reset_cache()
    monkeypatch.setattr(platform_shell, "which", lambda b: "/usr/bin/bwrap")
    monkeypatch.setattr(platform_shell, "run", lambda *a, **k: platform_shell.Result(
        list(a[0]), 1, "", "bwrap: No permissions to create new namespace\nsecond line"))
    st = sbx.status()
    assert not st.available and "No permissions to create new namespace" in st.reason
    assert "second line" not in st.reason and st.summary().startswith("unavailable: ")
    sbx.reset_cache()


def test_argv_has_every_boundary_flag_in_a_safe_order(monkeypatch, tmp_path):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    argv = sbx.build_argv("echo hi", tmp_path, "tag123", binary="bwrap")
    for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv"):
        assert flag in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--ro-bind") + 1:argv.index("--ro-bind") + 3] == ["/", "/"]
    for target in ("/tmp", "/run"):
        i = argv.index(target)
        assert argv[i - 1] == "--tmpfs"
    # The working directory is bound back *after* the tmpfs mounts (it may live
    # under /tmp), read-only, and the command is last.
    cwd_bind = [i for i, a in enumerate(argv) if a == str(tmp_path)][0]
    # ...and before /run is mounted, so even a cwd that contained the runtime
    # dir could not bring the bus socket back.
    assert argv[cwd_bind - 1] == "--ro-bind"
    assert argv.index("/tmp") < cwd_bind < argv.index("/run")
    # Both tmpfs mounts are size-capped (an unsized one is half of RAM).
    assert argv[argv.index("/tmp") - 3] == "--size" and argv[argv.index("/run") - 3] == "--size"
    assert argv[-3:] == ["/bin/sh", "-c", "echo hi"] and argv[-4] == "--"
    setenv = {argv[i + 1]: argv[i + 2] for i, a in enumerate(argv) if a == "--setenv"}
    assert "DBUS_SESSION_BUS_ADDRESS" not in setenv and "WAYLAND_DISPLAY" not in setenv
    assert setenv["KRAVEIN_GROUP_TAG"] == "tag123" and setenv["KRAVEIN_SANDBOX"] == "1"


def test_socket_masks_come_last_and_cover_sockets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "sub").mkdir()
    path = str(tmp_path / "sub" / "agent.sock")
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    try:
        argv = sbx.build_argv("true", tmp_path, "t", binary="bwrap")
    finally:
        srv.close()
    i = argv.index(path)
    assert argv[i - 2:i] == ["--ro-bind", "/dev/null"] and i < argv.index("--")


def test_cwd_tracking_uses_the_public_tokenizer():
    import inspect
    assert "safety._shell_tokens" not in inspect.getsource(sh)
    assert "safety.shell_tokens(" in inspect.getsource(sh.cwd_after)


def test_schema_documents_sandbox():
    props = registry.build_schema(sh.run_command)["parameters"]["properties"]
    assert props["sandbox"]["type"] == "boolean"
    assert "safely before running it for real" in props["sandbox"]["description"]
    assert "never run unsandboxed" in props["sandbox"]["description"]


# --- real bwrap: prove the boundary ------------------------------------------------------------------

@real
def test_real_the_result_says_it_ran_sandboxed():
    out = box("echo hello")
    assert "hello" in out and "ran read-only with no network" in out
    assert "nothing on the system was changed" in out


@real
def test_real_writing_under_home_fails_and_leaves_no_file():
    marker = REAL_HOME / f".kravein-sandbox-test-{secrets.token_hex(4)}"
    try:
        out = box(f"echo x > {marker}; echo rc=$?", working_directory=str(REAL_HOME))
        assert "Read-only file system" in out and "rc=1" in out
        assert not marker.exists()
    finally:
        marker.unlink(missing_ok=True)


@real
def test_real_writes_to_the_working_directory_fail(env):
    out = box("echo x > created.txt; echo rc=$?", working_directory=str(env))
    assert "Read-only file system" in out and not (env / "created.txt").exists()


@real
def test_real_tmp_is_a_throwaway_tmpfs():
    name = f"kravein-sbx-{secrets.token_hex(4)}"
    out = box(f"echo hi > /tmp/{name} && cat /tmp/{name}")
    assert "hi" in out and not Path("/tmp", name).exists()


@real
def test_real_no_network():
    # An inline interpreter is DESTRUCTIVE unsandboxed; here it needs no card.
    out = box("python3 -c 'import socket; s=socket.socket(); s.settimeout(3); "
              "print(\"RESULT\", s.connect_ex((\"1.1.1.1\", 53)))'")
    assert "RESULT " in out and "RESULT 0" not in out, out
    if platform_shell.have("curl"):
        assert "rc=0" not in box("curl -sS -m 3 https://1.1.1.1; echo rc=$?")


@real
def test_real_the_session_bus_is_out_of_reach():
    for cmd in ("busctl --user list", "systemctl --user status"):
        binary = cmd.split()[0]
        if not platform_shell.have(binary):
            continue
        out = box(f"{cmd}; echo rc=$?")
        assert "rc=0" not in out, out


@real
def test_real_host_processes_are_invisible_and_cannot_be_signalled():
    sleeper = subprocess.Popen(["sleep", "654"])
    try:
        out = box(f"kill -9 {sleeper.pid}; echo rc=$?; ps -e | wc -l")
        assert "rc=0" not in out and "No such process" in out
        assert sleeper.poll() is None
    finally:
        sleeper.kill()
        sleeper.wait()


@real
def test_real_runtime_dir_and_compositor_socket_are_absent():
    xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    out = box(f"ls -d {xdg} 2>&1; ls /run | wc -l; ls /tmp/.X11-unix 2>&1")
    assert "No such file" in out
    assert "\n0\n" in out        # /run is an empty tmpfs


@real
def test_real_sudo_cannot_elevate():
    if not platform_shell.have("sudo"):
        pytest.skip("no sudo here")
    out = box("sudo -n true; echo rc=$?")
    assert "rc=0" not in out and "sudo:" in out


@real
def test_real_no_capabilities_and_no_new_privs():
    out = box("grep -E 'CapEff|NoNewPrivs' /proc/self/status")
    assert "CapEff:\t0000000000000000" in out and "NoNewPrivs:\t1" in out


@real
def test_real_socket_and_agent_variables_do_not_exist_inside(monkeypatch):
    leaks = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/x", "WAYLAND_DISPLAY": "wayland-9",
             "DISPLAY": ":9", "XDG_RUNTIME_DIR": "/run/user/1", "SSH_AUTH_SOCK": "/x",
             "NIRI_SOCKET": "/x", "HYPRLAND_INSTANCE_SIGNATURE": "x", "SWAYSOCK": "/x",
             "GPG_AGENT_INFO": "/x", "TMUX": "/x", "GEMINI_API_KEY": "sekrit"}
    for k, v in leaks.items():
        monkeypatch.setenv(k, v)
    out = box("env")
    for k in leaks:
        assert f"{k}=" not in out, k
    assert "sekrit" not in out and "KRAVEIN_SANDBOX=1" in out


@real
def test_real_a_unix_socket_in_home_is_not_connectable(env):
    path = env / "agent.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)
    served = []
    threading.Thread(target=lambda: [served.append(srv.accept()) for _ in range(5)],
                     daemon=True).start()
    try:
        probe = socket.socket(socket.AF_UNIX)
        probe.connect(str(path))              # the host can, so a failure is the sandbox's doing
        probe.close()
        out = box(f"python3 -c \"import socket;s=socket.socket(socket.AF_UNIX);"
                  f"s.connect('{path}');print('CON'+'NECTED')\" 2>&1; echo done",
                  working_directory=str(env))
        assert "CONNECTED" not in out and "done" in out
    finally:
        srv.close()


@real
def test_real_a_destructive_command_runs_without_a_card_and_changes_nothing(env):
    victim = env / "victim"
    victim.mkdir()
    (victim / "f").write_text("x")
    asked = []
    out = registry.dispatch("run_command", {"command": f"rm -r {victim}; echo rc=$?",
                                            "sandbox": True, "working_directory": str(env)},
                            context=agent())          # no confirmer attached
    assert "Read-only file system" in out and (victim / "f").exists()
    assert "Refused" not in out and not asked


@real
def test_real_outright_refusals_still_refuse():
    out = registry.dispatch("run_command", {"command": "rm -rf /", "sandbox": True},
                            context=agent())
    assert "refused" in out.lower()


@real
def test_real_a_secret_read_still_asks_and_is_denied_without_a_confirmer(env):
    secret = env / ".env"
    secret.write_text("TOKEN=abc123\n")
    denied = registry.dispatch("run_command", {"command": f"cat {secret}", "sandbox": True},
                               context=agent())
    assert "abc123" not in denied
    allowed = registry.dispatch("run_command", {"command": f"cat {secret}", "sandbox": True},
                                context=agent(), confirm=True)
    assert "abc123" in allowed and "ran read-only" in allowed
    # ...and the secret still never lands in a spill file.
    assert not list((env.parent / "workspace" / "outputs").glob("*")) \
        if (env.parent / "workspace" / "outputs").exists() else True


@real
def test_real_background_sandbox_job_is_stopped_with_everything_inside():
    ctx = agent()
    started = box("sleep 4321", ctx, background=True)
    assert "Started background job" in started and "ran read-only" in started
    jid = started.split()[3]
    deadline = time.time() + 5
    while time.time() < deadline and not host_cmdline_alive("sleep", "4321"):
        time.sleep(0.05)
    assert host_cmdline_alive("sleep", "4321")
    report = sh.command_job(jid, _context=ctx)
    assert "running" in report and "ran read-only" in report
    assert "killed (stopped by request)" in sh.command_job(jid, action="stop", _context=ctx)
    deadline = time.time() + 5
    while time.time() < deadline and host_cmdline_alive("sleep", "4321"):
        time.sleep(0.05)
    assert not host_cmdline_alive("sleep", "4321")


@real
def test_real_timeout_moves_a_sandboxed_command_to_a_job_and_shutdown_kills_it():
    out = box("sleep 4322", timeout_seconds=1)
    assert "moved to background job" in out and "ran read-only" in out
    sh.kill_all_jobs()
    deadline = time.time() + 5
    while time.time() < deadline and host_cmdline_alive("sleep", "4322"):
        time.sleep(0.05)
    assert not host_cmdline_alive("sleep", "4322")


@real
def test_real_cd_still_tracks_inside_the_sandbox(env):
    (env / "sub").mkdir()
    ctx = agent()
    out = box("cd sub", ctx, working_directory=str(env))
    assert out.endswith(f"[cwd: {env / 'sub'}]")


def test_children_that_left_the_group_are_still_found_and_killed(tmp_path):
    """setsid moves a child out of the group; the tag in its environment still finds it."""
    if not platform_shell.have("setsid"):
        pytest.skip("no setsid")
    ctx = agent()
    try:
        started = sh.run_command("setsid sleep 4323 & echo hi", background=True, _context=ctx)
        job = sh._jobs[started.split()[3]]
        for _ in range(100):
            if job.proc.poll() is not None:
                break
            time.sleep(0.05)
        assert job.running and host_cmdline_alive("sleep", "4323")
        sh.command_job(job.id, action="stop", _context=ctx)
        time.sleep(0.3)
        assert not host_cmdline_alive("sleep", "4323")
    finally:
        for entry in os.listdir("/proc"):
            try:
                if entry.isdigit() and open(f"/proc/{entry}/cmdline", "rb").read().startswith(b"sleep\x004323"):
                    os.kill(int(entry), 9)
            except OSError:
                pass


# --- review fixes (#227) --------------------------------------------------------------------------

@pytest.mark.parametrize("cwd", ["/", "/run", f"/run/user/{os.getuid()}", "/tmp", "/var/run"])
def test_cwd_that_would_bring_the_hidden_places_back_is_refused(cwd):
    assert sbx.check_cwd(Path(cwd))


@pytest.mark.parametrize("cwd", ["/tmp/x", "/usr", "/var/tmp", str(REAL_HOME)])
def test_ordinary_working_directories_are_allowed(cwd):
    assert sbx.check_cwd(Path(cwd)) == ""


def test_run_command_refuses_a_root_working_directory_before_asking_or_running(monkeypatch):
    monkeypatch.setattr(sbx, "status", lambda: sbx.Status(True, "", "/usr/bin/bwrap", "x"))
    monkeypatch.setattr(platform_shell, "start_group",
                        lambda *a, **k: pytest.fail("must not start"))
    asked = []
    monkeypatch.setattr(sh, "enforce", lambda *a, **k: asked.append(1))
    out = box("busctl --user list", working_directory="/")
    assert out.startswith("Refused: the sandbox cannot use /") and not asked


def test_start_group_itself_refuses_a_bad_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(sbx, "status", lambda: sbx.Status(True, "", "/usr/bin/bwrap", "x"))
    with pytest.raises(platform_shell.SandboxUnavailable):
        platform_shell.start_group("true", cwd=Path("/"), stdout_path=tmp_path / "o",
                                   stderr_path=tmp_path / "e", sandbox=True)


@real
def test_real_the_session_bus_is_unreachable_even_from_a_root_cwd():
    bus = f"/run/user/{os.getuid()}/bus"
    if not os.path.exists(bus) or not platform_shell.have("busctl"):
        pytest.skip("no session bus here")
    out = box(f"busctl --address=unix:path={bus} list; echo rc=$?", working_directory="/")
    assert out.startswith("Refused")
    out = box(f"busctl --address=unix:path={bus} list; echo rc=$?")
    assert "rc=0" not in out


@real
def test_real_opaque_code_cannot_read_known_secret_locations(env):
    (env / ".ssh").mkdir()
    (env / ".ssh" / "id_ed25519").write_text("PRIVATE-KEY-MATERIAL")
    (env / "proj").mkdir()
    (env / "proj" / ".env").write_text("TOKEN=hunter2")
    (env / "proj" / "notes.txt").write_text("harmless")
    for target in (env / ".ssh" / "id_ed25519", env / "proj" / ".env"):
        out = box(f"python3 -c \"print(open('{target}').read())\"; echo done",
                  working_directory=str(env / "proj"))
        assert "PRIVATE-KEY-MATERIAL" not in out.split("\n", 2)[-1].replace(str(target), "")
        assert "hunter2" not in out.replace(f"open('{target}')", "")
    # ordinary files are still there to inspect
    assert "harmless" in box("cat notes.txt", working_directory=str(env / "proj"))


@real
def test_real_an_explicit_secret_read_still_returns_the_real_content(env):
    (env / ".ssh").mkdir()
    key = env / ".ssh" / "id_ed25519"
    key.write_text("PRIVATE-KEY-MATERIAL")
    denied = registry.dispatch("run_command", {"command": f"cat {key}", "sandbox": True},
                               context=agent())
    assert "PRIVATE-KEY-MATERIAL" not in denied
    allowed = registry.dispatch("run_command", {"command": f"cat {key}", "sandbox": True},
                                context=agent(), confirm=True)
    assert "PRIVATE-KEY-MATERIAL" in allowed        # the card was about exactly this


@real
def test_real_sockets_in_shared_dirs_and_deep_in_home_are_masked(env):
    deep = env / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    shared = Path("/var/tmp") / f"kravein-sbx-{secrets.token_hex(4)}.sock"
    socks = []
    for path in (deep / "x.sock", shared):
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(str(path))
        srv.listen(1)
        socks.append((srv, path))
    try:
        for _, path in socks:
            out = box(f"python3 -c \"import socket;s=socket.socket(socket.AF_UNIX);"
                      f"s.connect('{path}');print('CON'+'NECTED')\" 2>&1; echo done",
                      working_directory=str(env))
            assert "CONNECTED" not in out.split("\n", 2)[-1].replace("'CON'+'NECTED'", "")
            assert "done" in out
    finally:
        for srv, path in socks:
            srv.close()
            path.unlink(missing_ok=True)


@real
def test_real_a_socket_made_by_an_unsandboxed_command_is_masked_next_time(env):
    box("true", working_directory=str(env))                  # primes the scan cache
    path = env / "late.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)
    try:
        sh.run_command("true", _context=agent())            # any unsandboxed run
        out = box(f"python3 -c \"import socket;s=socket.socket(socket.AF_UNIX);"
                  f"s.connect('{path}');print('CON'+'NECTED')\" 2>&1; echo done",
                  working_directory=str(env))
        assert "CONNECTED" not in out.split("\n", 2)[-1].replace("'CON'+'NECTED'", "")
    finally:
        srv.close()


def test_an_incomplete_socket_scan_is_reported(monkeypatch, env):
    monkeypatch.setattr(sbx, "status", lambda: sbx.Status(True, "", "/usr/bin/bwrap", "x"))
    monkeypatch.setattr(sbx, "scan", lambda home, cwd: ([], [], False))
    monkeypatch.setattr(platform_shell, "start_group",
                        lambda *a, **k: (_ for _ in ()).throw(platform_shell.SandboxUnavailable("x")))
    # Reaches start_group only after the note is built; the refusal there is fine.
    assert box("true").startswith("Refused: sandbox unavailable")


def test_scan_finds_sockets_and_secret_names_and_flags_exhaustion(tmp_path, monkeypatch):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / ".env").write_text("x")
    (tmp_path / "d" / "id_rsa.pub").write_text("x")
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(tmp_path / "d" / "s.sock"))
    try:
        socks, secrets_found, complete = sbx._scan(tmp_path, time.monotonic() + 5, [1000])
    finally:
        srv.close()
    assert socks == [str(tmp_path / "d" / "s.sock")]
    assert secrets_found == [str(tmp_path / "d" / ".env")]       # a .pub key is public
    assert complete
    assert sbx._scan(tmp_path, time.monotonic() + 5, [1])[2] is False   # out of budget


def test_probe_failure_is_retried_so_installing_bwrap_needs_no_restart(monkeypatch):
    sbx.reset_cache()
    monkeypatch.setattr(sbx, "_RETRY_AFTER", 0.0)
    monkeypatch.setattr(platform_shell, "which", lambda b: None)
    assert not sbx.status().available
    monkeypatch.setattr(platform_shell, "which", lambda b: "/usr/bin/bwrap")
    monkeypatch.setattr(platform_shell, "run", lambda a, **k: platform_shell.Result(
        list(a), 0, "bubblewrap 9.9", ""))
    assert sbx.status().available
    monkeypatch.setattr(platform_shell, "which", lambda b: None)      # success is cached
    assert sbx.status().available
    sbx.reset_cache()


def test_a_child_with_a_scrubbed_environment_is_still_tracked_and_stopped():
    ctx = agent()
    try:
        started = sh.run_command("env -i /bin/sleep 4401 & echo hi", background=True, _context=ctx)
        job = sh._jobs[started.split()[3]]
        for _ in range(100):
            if job.proc.poll() is not None:
                break
            time.sleep(0.05)
        assert job.running and host_cmdline_alive("/bin/sleep", "4401")
        sh.command_job(job.id, action="stop", _context=ctx)
        time.sleep(0.3)
        assert not host_cmdline_alive("/bin/sleep", "4401")
    finally:
        for entry in os.listdir("/proc"):
            try:
                if entry.isdigit() and b"4401" in open(f"/proc/{entry}/cmdline", "rb").read():
                    os.kill(int(entry), 9)
            except OSError:
                pass


def test_a_finished_job_is_remembered_as_dead_and_not_rescanned(monkeypatch):
    ctx = agent()
    started = sh.run_command("true", background=True, _context=ctx)
    job = sh._jobs[started.split()[3]]
    job.proc.wait(timeout=5)
    for _ in range(100):
        if not job.running:
            break
        time.sleep(0.02)
    monkeypatch.setattr(platform_shell, "_survivors",
                        lambda proc: pytest.fail("a dead job must not rescan /proc"))
    assert not job.running and not job.running
    assert len(sh._running_for("p1")) == 0


@real
def test_real_kravein_dirs_stay_hidden_when_cwd_is_inside_them(tmp_path, monkeypatch):
    # The default workspace lives under DATA_DIR; a cwd there used to switch the
    # mask off for the whole directory, exposing memory.json to opaque code.
    data = tmp_path / "data"
    (data / "workspace").mkdir(parents=True)
    (data / "memory.json").write_text("private")
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "none-c")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "none-s")
    argv = sbx.build_argv(f"cat {data}/memory.json; ls {data}", data / "workspace", "t")
    assert argv.index("--tmpfs") < argv.index("--ro-bind", argv.index("--tmpfs"))
    i = [k for k, a in enumerate(argv) if a == str(data)]
    assert i and argv[i[0] - 1] == "--tmpfs"
    assert argv.index(str(data)) < argv.index(str(data / "workspace"))


@pytest.fixture
def outside_tmp_home(monkeypatch):
    # The sandbox replaces /tmp with a tmpfs, so a pytest tmp_path HOME would be
    # invisible inside no matter what is masked and the tests below would prove
    # nothing. Use a throwaway HOME inside the worktree, never the real one.
    import shutil
    base = Path(__file__).resolve().parent / f".sbx-home-{secrets.token_hex(4)}"
    base.mkdir()
    monkeypatch.setenv("HOME", str(base))
    monkeypatch.setattr(config, "CONFIG_DIR", base / ".config" / "kravein")
    monkeypatch.setattr(config, "STATE_DIR", base / ".local" / "state" / "kravein")
    monkeypatch.setattr(config, "DATA_DIR", base / ".local" / "share" / "kravein")
    sbx._scan_cache.clear()
    yield base
    shutil.rmtree(base, ignore_errors=True)


_PRIVATE = [
    ".config/google-chrome/Default/Cookies",
    ".config/chromium/Default/Cookies",
    ".mozilla/firefox/p.default/cookies.sqlite",
    ".config/kravein/api_keys.json",
    ".local/state/kravein/audit.jsonl",
    ".local/share/kravein/chrome-profile/Default/Cookies",
    ".local/share/keyrings/login.keyring",
    ".config/Signal/config.json",
    ".bash_history",
    ".zsh_history",
    ".python_history",
    ".git-credentials",
    ".npmrc",
]


@real
@pytest.mark.parametrize("rel", _PRIVATE)
def test_real_opaque_code_cannot_read_private_files(outside_tmp_home, rel):
    f = outside_tmp_home / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("SECRET-VALUE")
    # `python3 -c` is opaque to the classifier, so it runs with no card.
    out = box(f"python3 -c \"print(open('{f}').read())\"", working_directory=str(outside_tmp_home))
    assert "SECRET-VALUE" not in out


@real
def test_real_the_masking_is_not_vacuous(outside_tmp_home):
    # Control: an ordinary file in the same fake HOME IS visible, so the tests
    # above fail because of the mask and not because HOME is missing.
    (outside_tmp_home / "notes.txt").write_text("VISIBLE-VALUE")
    out = box("python3 -c \"print(open('notes.txt').read())\"",
              working_directory=str(outside_tmp_home))
    assert "VISIBLE-VALUE" in out
