"""live_serve.sh must only ever stop the server it started."""
import os
import socket
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("live_serve.sh")


def _run(cwd, port, *args):
    env = {**os.environ, "LIVE_SERVE_PORT": str(port)}
    # cwd must not be a git checkout of ours: the script cds to its own toplevel
    # (git) or its parent dir; a copy under tmp_path keeps .qa-state in tmp.
    return subprocess.run(["bash", str(cwd / "tools" / "live_serve.sh"), *args],
                          capture_output=True, text=True, env=env, cwd=cwd)


def _sandbox(tmp_path):
    (tmp_path / "tools").mkdir()
    dst = tmp_path / "tools" / "live_serve.sh"
    dst.write_text(SCRIPT.read_text())
    return tmp_path


def test_refuses_when_port_held_by_someone_else(tmp_path):
    box = _sandbox(tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        r = _run(box, s.getsockname()[1], "label")
    assert r.returncode == 3 and "refusing" in r.stderr
    # nothing was started or recorded
    assert not (box / ".qa-state" / "live_serve.pid").exists()


def test_stop_never_touches_unrelated_pid(tmp_path):
    box = _sandbox(tmp_path)
    other = subprocess.Popen(["sleep", "30"])
    try:
        (box / ".qa-state").mkdir()
        (box / ".qa-state" / "live_serve.pid").write_text(str(other.pid))
        r = _run(box, 1, "stop")
        assert r.returncode == 0
        assert other.poll() is None, "stopped a process that is not a kravein server"
        assert not (box / ".qa-state" / "live_serve.pid").exists()
    finally:
        other.kill()


def test_stop_without_pidfile_is_a_noop(tmp_path):
    assert _run(_sandbox(tmp_path), 1, "stop").returncode == 0
