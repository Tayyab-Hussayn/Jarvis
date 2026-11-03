"""#218: run_command keeps its cwd, spills big output, kills the process tree, runs in the background."""

from __future__ import annotations

import os
import stat
import time
import types
from pathlib import Path

import pytest

from kravein import audit, config, registry
from kravein.platform import shell as platform_shell
from kravein.skills import shell as sh


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(sh.tempfile, "tempdir", str(tmp_path))
    yield home
    # A job left running would outlive the test and hold files open.
    sh.kill_all_jobs()
    sh._jobs.clear()


def agent(principal="p1"):
    """Stands in for the calling Agent: tool_state and principal are all that matter."""
    return types.SimpleNamespace(tool_state={}, principal=principal)


def run(ctx, command, **kw):
    return sh.run_command(command, _context=ctx, **kw)


def outputs(tmp_path) -> list[Path]:
    d = tmp_path / "workspace" / "outputs"
    return sorted(d.iterdir()) if d.exists() else []


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def job_id(text: str) -> str:
    import re
    return re.search(r"job-[0-9a-f]{6}", text).group(0)


# --- persistent cwd ----------------------------------------------------------------

def test_cwd_persists_per_agent_and_agents_are_isolated(env):
    (env / "sub").mkdir()
    a, b = agent(), agent()
    out = run(a, "cd sub")
    assert out.endswith(f"[cwd: {env / 'sub'}]")
    assert f"{env / 'sub'}\n" in run(a, "pwd")          # the next command starts there
    assert run(b, "pwd").splitlines()[1] == str(env)    # another agent still starts at home


def test_cd_chain_and_failure_leave_the_right_cwd(env):
    (env / "a" / "b").mkdir(parents=True)
    ctx = agent()
    run(ctx, "cd a && cd b")
    assert ctx.tool_state["cwd"] == str(env / "a" / "b")
    out = run(ctx, "cd nope")
    assert "[exit 1]" in out and ctx.tool_state["cwd"] == str(env / "a" / "b")
    out = run(ctx, "cd .. && false")                    # failed command: no update
    assert ctx.tool_state["cwd"] == str(env / "a" / "b")


@pytest.mark.parametrize("command", [
    "cd -", "cd -P a", "cd a; ls", "cd a || ls", "cd a | cat", "ls && cd a",
    "cd a && make && cd ..", "cd $HOME", "echo cd a",
])
def test_cwd_after_ignores_anything_it_cannot_follow(env, command):
    (env / "a").mkdir()
    assert sh.cwd_after(command, env) is None


def test_cwd_after_handles_home_and_relative(env):
    (env / "a").mkdir()
    assert sh.cwd_after("cd a", env) == env / "a"
    assert sh.cwd_after("cd a && ls -l", env) == env / "a"
    assert sh.cwd_after("cd", env / "a") == env
    assert sh.cwd_after("cd ~/a", env) == env / "a"


def test_explicit_working_directory_sets_it(env):
    (env / "w").mkdir()
    ctx = agent()
    run(ctx, "true", working_directory=str(env / "w"))
    assert ctx.tool_state["cwd"] == str(env / "w")
    assert run(ctx, "pwd").splitlines()[1] == str(env / "w")


def test_cli_without_a_context_is_unchanged(env):
    out = sh.run_command("pwd")
    assert "[cwd:" not in out and str(env) in out
    sh.run_command("cd /tmp")
    assert "[cwd:" not in sh.run_command("pwd")


def test_classification_gets_the_effective_cwd(env, monkeypatch):
    (env / "sub").mkdir()
    seen = []
    real = sh.classify_command
    monkeypatch.setattr(sh, "classify_command",
                        lambda c, cwd=None: (seen.append(cwd), real(c, cwd=cwd))[1])
    ctx = agent()
    run(ctx, "cd sub")
    run(ctx, "ls")
    assert seen[-1] == env / "sub"


# --- output spill --------------------------------------------------------------------

def test_small_output_leaves_no_file(tmp_path):
    out = run(agent(), "echo hello")
    assert "hello" in out and "full stdout" not in out
    assert outputs(tmp_path) == []


def test_big_output_spills_with_preview_and_a_private_file(tmp_path):
    out = run(agent(), "seq 1 20000")
    assert "full stdout:" in out and "(20,000 lines)" in out
    assert "read it with read_file or search_text" in out
    assert out.splitlines()[1] == "1" and "20000" in out and "bytes not shown" in out
    files = outputs(tmp_path)
    assert len(files) == 1
    assert files[0].read_text().splitlines()[-1] == "20000"
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(files[0].parent.stat().st_mode) == 0o700
    assert str(files[0]) in out


def test_spill_files_rotate(tmp_path):
    d = tmp_path / "workspace" / "outputs"
    d.mkdir(parents=True)
    for i in range(60):
        f = d / f"old-{i:02d}.out"
        f.write_text("x")
        os.utime(f, (1000 + i, 1000 + i))
    run(agent(), "seq 1 20000")
    left = sorted(p.name for p in d.iterdir())
    assert len(left) == sh._KEEP_SPILLS
    assert "old-00.out" not in left and "old-59.out" in left


def test_secret_read_is_never_spilled_nor_left_on_disk(env, tmp_path):
    secret = env / "proj" / ".env"
    secret.parent.mkdir()
    secret.write_text("TOKEN=" + "A" * 30000 + "\n")
    out = run(agent(), f"cat {secret}", _confirm=True)
    assert "output truncated" in out and "full stdout" not in out
    assert outputs(tmp_path) == []
    assert not list(tmp_path.glob("kravein-secret-*"))


def test_secret_read_cannot_go_to_the_background(env, tmp_path):
    secret = env / ".env"
    secret.write_text("TOKEN=abc\n")
    out = run(agent(), f"cat {secret}", background=True, _confirm=True)
    assert out.startswith("Refused") and "background" in out
    assert sh._jobs == {}


# --- process tree -----------------------------------------------------------------------

def test_kill_group_takes_the_grandchild_too(tmp_path):
    script = tmp_path / "child.sh"
    pidfile = tmp_path / "grandchild.pid"
    script.write_text(f"sleep 60 &\necho $! > {pidfile}\nwait\n")
    proc = platform_shell.start_group(f"sh {script}", cwd=tmp_path,
                                      stdout_path=tmp_path / "o", stderr_path=tmp_path / "e")
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.05)
    grandchild = int(pidfile.read_text())
    assert alive(grandchild)
    assert platform_shell.wait(proc, 0.2) is None       # still running
    assert platform_shell.kill_group(proc, grace=1)
    for _ in range(100):
        if not alive(grandchild):
            break
        time.sleep(0.05)
    assert not alive(grandchild) and proc.poll() is not None


def test_kill_group_escalates_to_kill_for_a_term_ignorer(tmp_path):
    proc = platform_shell.start_group("trap '' TERM; sleep 60 & wait", cwd=tmp_path,
                                      stdout_path=tmp_path / "o", stderr_path=tmp_path / "e")
    time.sleep(0.3)
    platform_shell.kill_group(proc, grace=0.3)
    assert proc.poll() is not None


def kill_by_cmdline(*words: str) -> None:
    """Test cleanup for a deliberately orphaned process."""
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as handle:
                    if handle.read().decode().split("\0")[:-1] == list(words):
                        os.kill(int(entry), 9)
            except (OSError, ValueError):
                pass


def test_a_lingering_grandchild_does_not_hang_the_foreground(tmp_path):
    t0 = time.time()
    try:
        out = run(agent(), "sleep 31 & echo started")
        assert "started" in out and time.time() - t0 < 10   # pipes would have waited 31 s
    finally:
        kill_by_cmdline("sleep", "31")


# --- background jobs ----------------------------------------------------------------------

def test_background_start_status_and_stop():
    ctx = agent()
    started = run(ctx, "echo hi; sleep 60", background=True)
    jid = job_id(started)
    assert "Started background job" in started
    time.sleep(0.3)
    status = sh.command_job(jid, _context=ctx)
    assert "running" in status and "hi" in status and "full output:" in status
    pid = sh._jobs[jid].proc.pid
    stopped = sh.command_job(jid, action="stop", _context=ctx)
    assert "killed (stopped by request)" in stopped
    assert not alive(pid)


def test_status_and_stop_are_the_same_skill_with_fixed_actions():
    ctx = agent()
    jid = job_id(run(ctx, "sleep 60", background=True))
    # The alias preset, not the caller, picks the action.
    out = registry.dispatch("command_status", {"job_id": jid, "action": "stop"}, context=ctx)
    assert "running" in out and sh._jobs[jid].running
    out = registry.dispatch("stop_command", {"job_id": jid}, context=ctx)
    assert "killed (stopped by request)" in out


def test_finished_job_reports_exit_code():
    ctx = agent()
    jid = job_id(run(ctx, "echo done; exit 3", background=True))
    for _ in range(100):
        if not sh._jobs[jid].running:
            break
        time.sleep(0.05)
    out = sh.command_job(jid, _context=ctx)
    assert "exited with code 3" in out and "done" in out


def test_jobs_belong_to_their_principal():
    jid = job_id(run(agent("p1"), "sleep 60", background=True))
    assert sh.command_job(jid, _context=agent("p2")).startswith("No background job")
    assert "running" in sh.command_job(jid, _context=agent("p1"))


def test_slot_limit_is_per_principal():
    ctx = agent("p1")
    for _ in range(sh.MAX_JOBS):
        assert "Started" in run(ctx, "sleep 60", background=True)
    out = run(ctx, "sleep 60", background=True)
    assert out.startswith("Refused") and "3 background jobs" in out
    assert "Started" in run(agent("p2"), "sleep 60", background=True)


def test_timeout_moves_the_command_to_a_background_job():
    ctx = agent()
    out = run(ctx, "echo early; sleep 60", timeout_seconds=1)
    assert "did not finish in 1s; moved to background job job-" in out
    assert "check with command_status" in out and "early" in out
    jid = job_id(out)
    assert sh._jobs[jid].running                       # not killed


def test_timeout_without_a_free_slot_kills_it():
    ctx = agent()
    for _ in range(sh.MAX_JOBS):
        run(ctx, "sleep 60", background=True)
    t0 = time.time()
    out = run(ctx, "sleep 60", timeout_seconds=1)
    assert "timed out after 1s and was killed" in out and "3 background jobs" in out
    assert time.time() - t0 < 10 and len(sh._jobs) == sh.MAX_JOBS


def test_secret_read_that_times_out_is_killed_not_backgrounded(env):
    secret = env / ".env"
    secret.write_text("TOKEN=abc\n")
    out = run(agent(), f"cat {secret}; sleep 60", timeout_seconds=1, _confirm=True)
    assert "killed" in out and "reads a secret file" in out and sh._jobs == {}


def test_shutdown_kills_every_job():
    ctx = agent()
    jid = job_id(run(ctx, "sleep 60", background=True))
    pid = sh._jobs[jid].proc.pid
    sh.kill_all_jobs()
    for _ in range(100):
        if not alive(pid):
            break
        time.sleep(0.05)
    assert not alive(pid)
    assert "Kravein exited" in sh.command_job(jid, _context=ctx)


def test_lifetime_limit_kills_the_job(monkeypatch):
    monkeypatch.setattr(sh, "JOB_LIFETIME", 1)
    ctx = agent()
    jid = job_id(run(ctx, "sleep 60", background=True))
    job = sh._jobs[jid]
    job.thread.join(timeout=10)
    assert not job.running and "1 h limit".replace("1 h", "0 h") in sh.command_job(jid, _context=ctx)


def test_jobs_are_audited_at_start_and_end(monkeypatch):
    events = []
    monkeypatch.setattr(audit, "record", lambda event, **kw: events.append((event, kw)))
    ctx = agent()
    jid = job_id(run(ctx, "sleep 60", background=True))
    sh.command_job(jid, action="stop", _context=ctx)
    names = [e for e, _ in events]
    assert "shell.job_start" in names and "shell.job_end" in names
    assert next(kw for e, kw in events if e == "shell.job_start")["job"] == jid
    assert next(kw for e, kw in events if e == "shell.job_end")["reason"] == "stopped"


def test_stop_declares_the_job_as_its_target(monkeypatch):
    ctx = agent()
    jid = job_id(run(ctx, "sleep 60", background=True))
    calls = []
    monkeypatch.setattr(sh, "enforce", lambda verdict, desc, **kw: calls.append((desc, kw)))
    sh.command_job(jid, action="stop", _context=ctx)
    assert calls[0][1]["targets"] == [f"job:{jid}"] and calls[0][1]["skill"] == "stop_command"


# --- safety applies to background commands too ---------------------------------------------

def test_background_destructive_command_is_denied_without_a_confirmer(env):
    victim = env / "victim"
    victim.mkdir()
    (victim / "f").write_text("x")
    out = registry.dispatch("run_command", {"command": f"rm -r {victim}", "background": True},
                            context=agent())
    assert "refused" in out.lower() or "confirm" in out.lower()
    assert (victim / "f").exists() and sh._jobs == {}


def test_background_destructive_command_runs_after_a_confirmation(env):
    victim = env / "victim"
    victim.mkdir()
    (victim / "f").write_text("x")
    out = registry.dispatch("run_command", {"command": f"rm -r {victim}", "background": True},
                            context=agent(), confirm=True)
    assert "Started background job" in out
    for _ in range(100):
        if not victim.exists():
            break
        time.sleep(0.05)
    assert not victim.exists()


def test_schema_documents_background_and_cwd():
    schema = registry.build_schema(sh.run_command)
    props = schema["parameters"]["properties"]
    assert props["background"]["type"] == "boolean" and "_context" not in props
    assert "persists" in schema["description"] and "command_status" in schema["description"]
    assert "Use for servers" in props["background"]["description"]


# --- review fixes ------------------------------------------------------------------------------

def test_slot_limit_holds_under_concurrency(monkeypatch):
    import threading
    # enforce() can wait on an owner card; every caller sits in it at once.
    monkeypatch.setattr(sh, "enforce", lambda *a, **k: time.sleep(0.4))
    results = []

    def go():
        results.append(run(agent("p1"), "sleep 60", background=True))

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum("Started" in r for r in results) == sh.MAX_JOBS
    assert sum(r.startswith("Refused") for r in results) == 3
    assert len(sh._running_for("p1")) == sh.MAX_JOBS


def test_a_declined_background_card_releases_its_slot(monkeypatch):
    from kravein.safety import ConfirmationRequired
    def deny(*a, **k):
        raise ConfirmationRequired("no")
    monkeypatch.setattr(sh, "enforce", deny)
    for _ in range(sh.MAX_JOBS + 2):
        with pytest.raises(ConfirmationRequired):
            run(agent("p1"), "sleep 60", background=True)
    monkeypatch.undo()
    assert "Started" in run(agent("p1"), "sleep 60", background=True)


def test_children_that_outlive_the_shell_keep_the_job_running_and_are_killed(tmp_path):
    ctx = agent()
    try:
        jid = job_id(run(ctx, "sleep 77 & echo hi", background=True))
        job = sh._jobs[jid]
        for _ in range(100):
            if job.proc.poll() is not None:
                break
            time.sleep(0.05)
        assert job.proc.poll() == 0 and job.running            # shell gone, child alive
        report = sh.command_job(jid, _context=ctx)
        assert "child processes are still running" in report
        assert len(sh._running_for("p1")) == 1                  # it still holds a slot
        out = sh.command_job(jid, action="stop", _context=ctx)
        assert "killed (stopped by request)" in out
        assert not job.running
    finally:
        kill_by_cmdline("sleep", "77")


def test_shutdown_kills_children_that_outlived_the_shell():
    ctx = agent()
    try:
        jid = job_id(run(ctx, "sleep 78 & echo hi", background=True))
        job = sh._jobs[jid]
        for _ in range(100):
            if job.proc.poll() is not None:
                break
            time.sleep(0.05)
        sh.kill_all_jobs()
        assert not job.running
    finally:
        kill_by_cmdline("sleep", "78")


def test_rotation_never_deletes_an_inflight_foreground_output(tmp_path):
    d = tmp_path / "workspace" / "outputs"
    d.mkdir(parents=True)
    quiet = d / "cmd-running.out"
    quiet.write_text("")
    os.utime(quiet, (1, 1))
    for i in range(60):
        f = d / f"new-{i}.out"
        f.write_text("x")
    sh._inflight.add(quiet)
    try:
        sh._rotate()
    finally:
        sh._inflight.discard(quiet)
    assert quiet.exists()


def test_a_command_in_flight_registers_its_files_as_inflight(tmp_path):
    seen = []
    real = sh._render
    def spy(*a, **k):
        seen.append(set(sh._inflight))
        return real(*a, **k)
    sh._render = spy
    try:
        run(agent(), "echo x")
    finally:
        sh._render = real
    assert seen and len(seen[0]) == 2 and sh._inflight == set()


def test_relative_working_directory_follows_the_persistent_cwd(env):
    (env / "proj" / "sub").mkdir(parents=True)
    ctx = agent()
    run(ctx, "cd proj")
    out = run(ctx, "pwd", working_directory="sub")
    assert out.splitlines()[1] == str(env / "proj" / "sub")
    assert ctx.tool_state["cwd"] == str(env / "proj" / "sub")
    assert "does not exist" in run(ctx, "pwd", working_directory="missing")


def test_failures_and_timeouts_are_audited(monkeypatch):
    events = []
    monkeypatch.setattr(audit, "record", lambda event, **kw: events.append((event, kw)))
    run(agent(), "ls /definitely/not/here")
    run(agent(), "sleep 60", timeout_seconds=1)
    names = [e for e, _ in events]
    assert "shell.failed" in names and "shell.timeout" in names
