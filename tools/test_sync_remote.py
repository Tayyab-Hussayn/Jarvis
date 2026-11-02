"""sync-remote.sh: every protect filter must be its own rsync argument.

A missing space once glued two --filter args into one, so .build.log and
__pycache__ were silently not protected from --delete. Run the real script
against a local directory (host "-") and check what --delete spares.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "sync-remote.sh"

PROTECTED = [
    ".venv/x", "web/node_modules/x", "web/.next/x", "kravein/web-dist/x",
    ".pip-cache/x", ".npm-cache/x", ".install.log", ".build.log",
    "pkg/__pycache__/x.pyc", ".pytest_cache/x",
]


def test_each_protect_filter_survives_delete(tmp_path):
    dest = tmp_path / "dest"
    for rel in PROTECTED + ["stale-upstream-file.txt"]:
        f = dest / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    r = subprocess.run(["bash", str(SCRIPT), "-", str(dest)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    missing = [p for p in PROTECTED if not (dest / p).exists()]
    assert not missing, f"--delete removed protected paths: {missing}"
    assert not (dest / "stale-upstream-file.txt").exists()


def test_remote_destination_still_restricted():
    r = subprocess.run(["bash", str(SCRIPT), "somehost", "/etc"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 2
