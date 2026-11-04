"""Tests: file checkpoints and undo_changes (#223)."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import audit, config, registry, safety  # noqa: E402
from kravein.agents import checkpoints as ck, delegate as dg, tasks  # noqa: E402
from conftest import _agent_with  # noqa: E402
from test_delegate import (  # noqa: E402
    _zz_skills, call, orchestrate, say, setting,  # noqa: F401  (fixture is autouse there)
)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "CONFIG_DIR", home / ".config" / "kravein")
    monkeypatch.setattr(config, "DATA_DIR", home / ".local" / "share" / "kravein")
    monkeypatch.setattr(config, "WORKSPACE_DIR", home / ".local" / "share" / "kravein" / "workspace")
    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit, "_fd", None)
    monkeypatch.setattr(ck, "_run_bytes", {})
    work = tmp_path / "work"
    work.mkdir()
    yield work
    monkeypatch.setattr(audit, "_fd", None)
    safety.set_confirmer(None)


def agent(run="r1", principal="owner-a"):
    a, _ = _agent_with([], profile="system-ops")
    a.run_id, a.run_principal = run, principal
    return a


class Cards:
    """A confirmer that records every card and answers as told."""

    def __init__(self, answer=True):
        self.answer = answer
        self.cards: list[tuple[str, str]] = []

    def __call__(self, action, detail, level):
        self.cards.append((action, detail))
        return self.answer


def use(ag, cards, name, **params):
    """Dispatch a skill the way the agent does: its own context, a card confirmer."""
    with safety.confirmer_scope(cards, principal=ck.principal_of(ag)):
        return registry.dispatch(name, params, context=ag, source="web")


def read(ag, cards, path):
    return use(ag, cards, "read_file", path=str(path))


def text(p: Path) -> str:
    return p.read_text()


# -- each kind of change, and back --------------------------------------------------

def test_write_creates_then_undo_removes(env):
    ag, cards = agent(), Cards()
    f = env / "new.txt"
    out = use(ag, cards, "write_file", path=str(f), content="hello")
    assert f.read_text() == "hello" and "Not checkpointed" not in out
    out = use(ag, cards, "undo_changes")
    assert not f.exists() and "removed 1 created by it" in out
    action, detail = cards.cards[-1]
    assert "r1" in action and str(f) in detail and "created by the run" in detail


def test_write_overwrite_then_undo_restores(env):
    ag, cards = agent(), Cards()
    f = env / "a.txt"
    f.write_text("original")
    read(ag, cards, f)
    use(ag, cards, "write_file", path=str(f), content="changed")
    assert f.read_text() == "changed"
    out = use(ag, cards, "undo_changes", task="r1")
    assert f.read_text() == "original" and "restored 1 file(s)" in out
    assert str(f) in cards.cards[-1][1]


def test_edit_then_undo_restores_content_and_mode(env):
    ag, cards = agent(), Cards()
    f = env / "script.sh"
    f.write_text("echo one\n")
    os.chmod(f, 0o750)
    read(ag, cards, f)
    use(ag, cards, "edit_file", path=str(f), old_text="one", new_text="two")
    assert f.read_text() == "echo two\n"
    use(ag, cards, "undo_changes")
    assert f.read_text() == "echo one\n" and stat.S_IMODE(f.stat().st_mode) == 0o750


def test_two_edits_of_one_file_undo_to_the_first_state(env):
    ag, cards = agent(), Cards()
    f = env / "a.txt"
    f.write_text("v0")
    read(ag, cards, f)
    use(ag, cards, "write_file", path=str(f), content="v1")
    use(ag, cards, "write_file", path=str(f), content="v2")
    out = use(ag, cards, "undo_changes")
    assert f.read_text() == "v0" and "changed since" not in out


def test_delete_file_then_undo_restores_it(env):
    ag, cards = agent(), Cards()
    f = env / "gone.txt"
    f.write_text("precious")
    use(ag, cards, "delete_path", path=str(f), permanent=True)
    assert not f.exists()
    use(ag, cards, "undo_changes")
    assert f.read_text() == "precious"


def test_delete_directory_tree_then_undo_restores_everything(env):
    ag, cards = agent(), Cards()
    d = env / "proj"
    (d / "sub" / "deep").mkdir(parents=True)
    (d / "empty").mkdir()
    (d / "a.txt").write_text("A")
    (d / "sub" / "b.txt").write_text("B")
    (d / "sub" / "deep" / "c.txt").write_text("C")
    (d / "link").symlink_to("a.txt")
    use(ag, cards, "delete_path", path=str(d), permanent=True)
    assert not d.exists()
    out = use(ag, cards, "undo_changes")
    assert text(d / "a.txt") == "A" and text(d / "sub" / "b.txt") == "B"
    assert text(d / "sub" / "deep" / "c.txt") == "C"
    assert (d / "empty").is_dir() and os.readlink(d / "link") == "a.txt"
    assert "restored 4 file(s)" in out


def test_move_then_undo_puts_it_back_and_removes_the_destination(env):
    ag, cards = agent(), Cards()
    src, dst = env / "a.txt", env / "moved" / "a.txt"
    src.write_text("A")
    use(ag, cards, "move_path", source=str(src), destination=str(dst))
    assert dst.exists() and not src.exists()
    use(ag, cards, "undo_changes")
    assert text(src) == "A" and not dst.exists()


def test_move_over_an_existing_file_restores_both(env):
    ag, cards = agent(), Cards()
    src, dst = env / "a.txt", env / "b.txt"
    src.write_text("A")
    dst.write_text("B")
    use(ag, cards, "move_path", source=str(src), destination=str(dst))
    assert text(dst) == "A"
    use(ag, cards, "undo_changes")
    assert text(src) == "A" and text(dst) == "B"


def test_move_a_directory_then_undo(env):
    ag, cards = agent(), Cards()
    src, dst = env / "d1", env / "d2"
    (src / "x").mkdir(parents=True)
    (src / "x" / "f.txt").write_text("F")
    use(ag, cards, "move_path", source=str(src), destination=str(dst))
    assert (dst / "x" / "f.txt").exists() and not src.exists()
    use(ag, cards, "undo_changes")
    assert text(src / "x" / "f.txt") == "F" and not dst.exists()


def test_rename_then_undo(env):
    ag, cards = agent(), Cards()
    f = env / "old.txt"
    f.write_text("x")
    use(ag, cards, "rename_path", path=str(f), new_name="new.txt")
    assert (env / "new.txt").exists()
    use(ag, cards, "undo_changes")
    assert f.exists() and not (env / "new.txt").exists()


def test_copy_new_and_copy_over_then_undo(env):
    ag, cards = agent(), Cards()
    src, fresh, over = env / "s.txt", env / "copy.txt", env / "over.txt"
    src.write_text("S")
    over.write_text("OLD")
    use(ag, cards, "copy_path", source=str(src), destination=str(fresh))
    use(ag, cards, "copy_path", source=str(src), destination=str(over))
    assert text(fresh) == "S" and text(over) == "S"
    use(ag, cards, "undo_changes")
    assert not fresh.exists() and text(over) == "OLD" and text(src) == "S"


def test_copy_a_directory_into_an_existing_one_then_undo(env):
    ag, cards = agent(), Cards()
    src, dst = env / "src", env / "dst"
    (src / "n").mkdir(parents=True)
    (src / "n" / "new.txt").write_text("N")
    (src / "keep.txt").write_text("from src")
    (dst / "src").mkdir(parents=True)              # copy_path puts a source dir *inside* an existing one
    (dst / "src" / "keep.txt").write_text("mine")
    use(ag, cards, "copy_path", source=str(src), destination=str(dst))
    merged = dst / "src"
    assert text(merged / "keep.txt") == "from src" and (merged / "n" / "new.txt").exists()
    use(ag, cards, "undo_changes")
    assert text(merged / "keep.txt") == "mine" and not (merged / "n").exists()


def test_organize_then_undo_restores_every_file_and_removes_the_folders(env):
    ag, cards = agent(), Cards()
    d = env / "downloads"
    d.mkdir()
    for name in ("a.png", "b.pdf", "c.zip", "d.xyz"):
        (d / name).write_text(name)
    use(ag, cards, "organize_directory", path=str(d))
    assert sorted(p.name for p in d.iterdir()) == ["Archives", "Documents", "Images", "Others"]
    out = use(ag, cards, "undo_changes")
    assert sorted(p.name for p in d.iterdir()) == ["a.png", "b.pdf", "c.zip", "d.xyz"]
    assert text(d / "a.png") == "a.png" and "restored 4 file(s)" in out


def test_undo_is_one_shot_and_nothing_else_is_touched(env):
    ag, cards = agent(), Cards()
    a, other = env / "a.txt", env / "other.txt"
    a.write_text("A")
    other.write_text("untouched")
    read(ag, cards, a)
    use(ag, cards, "write_file", path=str(a), content="A2")
    use(ag, cards, "undo_changes")
    n = len(cards.cards)
    out = use(ag, cards, "undo_changes")
    assert "Nothing to undo" in out and len(cards.cards) == n          # no second card
    assert text(other) == "untouched" and text(a) == "A"


# -- changed since / force -----------------------------------------------------------

def test_a_file_changed_after_the_run_is_skipped_then_forced_on_a_second_card(env):
    ag, cards = agent(), Cards()
    a, b = env / "a.txt", env / "b.txt"
    a.write_text("a0")
    b.write_text("b0")
    read(ag, cards, a)
    read(ag, cards, b)
    use(ag, cards, "write_file", path=str(a), content="a1")
    use(ag, cards, "write_file", path=str(b), content="b1")
    a.write_text("owner edited later")                    # the owner, after the run

    out = use(ag, cards, "undo_changes")
    assert text(b) == "b0" and text(a) == "owner edited later"
    assert "Skipped 1 file(s) changed since the run" in out and str(a) in out
    assert "force=True" in out
    first = cards.cards[-1]
    assert str(b) in first[1] and "changed since the run and will be skipped" in first[1]

    before = len(cards.cards)
    out = use(ag, cards, "undo_changes", force=True)
    assert text(a) == "a0" and "restored 1 file(s)" in out
    assert len(cards.cards) == before + 1                 # b was already undone: only the force card
    action, detail = cards.cards[-1]
    assert "over newer changes" in action and str(a) in detail and "newer content will be lost" in detail


def test_force_needs_its_own_card_and_a_no_keeps_the_newer_file(env):
    ag = agent()
    a = env / "a.txt"
    a.write_text("a0")
    read(ag, Cards(), a)
    use(ag, Cards(), "write_file", path=str(a), content="a1")
    a.write_text("newer")
    out = use(ag, Cards(answer=False), "undo_changes", force=True)
    assert text(a) == "newer" and "Cancelled" in out


def test_a_file_edited_between_the_card_and_the_restore_is_not_overwritten(env, monkeypatch):
    ag = agent()
    a = env / "a.txt"
    a.write_text("a0")
    cards = Cards()
    read(ag, cards, a)
    use(ag, cards, "write_file", path=str(a), content="a1")

    def edit_during_card(action, detail, level):
        a.write_text("raced")
        return True

    out = use(ag, edit_during_card, "undo_changes")
    assert text(a) == "raced" and "changed while undoing" in out


def test_undo_never_writes_to_a_protected_path(env, monkeypatch):
    ag, cards = agent(), Cards()
    f = env / "a.txt"
    f.write_text("a0")
    read(ag, cards, f)
    use(ag, cards, "write_file", path=str(f), content="a1")
    monkeypatch.setattr(config, "get", lambda d, default=None: [str(env)] if d == "safety.protected_paths"
                        else config.DEFAULT_SETTINGS.get(d.split(".")[0], {}).get(d.split(".")[-1], default)
                        if "." in d else default)
    out = use(ag, cards, "undo_changes")
    assert text(f) == "a1" and "Refused 1 protected path" in out


# -- caps, retention, isolation -----------------------------------------------------

def test_a_file_over_the_per_file_cap_is_not_checkpointed_and_the_result_says_so(env, monkeypatch):
    setting(monkeypatch, agents__checkpoint_file_max_mb=0.001)        # ~1 KB
    ag, cards = agent(), Cards()
    big = env / "big.txt"
    big.write_text("x" * 5000)
    read(ag, cards, big)
    out = use(ag, cards, "write_file", path=str(big), content="y")
    assert "Not checkpointed" in out and "per-file limit" in out
    out = use(ag, cards, "undo_changes")
    assert text(big) == "y" and "never checkpointed" in out
    small = env / "small.txt"
    assert "Not checkpointed" not in use(ag, cards, "write_file", path=str(small), content="ok")


def test_the_per_run_cap_stops_checkpointing_and_says_so(env, monkeypatch):
    setting(monkeypatch, agents__checkpoint_run_max_mb=0.003)         # ~3 KB
    ag, cards = agent(), Cards()
    files = [env / f"f{i}.txt" for i in range(3)]
    for f in files:
        f.write_text("z" * 2000)
        read(ag, cards, f)
    outs = [use(ag, cards, "write_file", path=str(f), content="n") for f in files]
    assert "Not checkpointed" not in outs[0]
    assert "run already holds" in outs[1] and "run already holds" in outs[2]
    use(ag, cards, "undo_changes")
    assert text(files[0]) == "z" * 2000 and text(files[1]) == "n"


def test_a_checkpoint_failure_never_blocks_the_change(env, monkeypatch):
    ag, cards = agent(), Cards()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ck.Handle, "_snap_one", boom)
    f = env / "a.txt"
    out = use(ag, cards, "write_file", path=str(f), content="still written")
    assert f.read_text() == "still written" and "checkpoint could not be written" in out


def test_blobs_and_directories_are_private(env):
    ag, cards = agent(), Cards()
    f = env / "secret.txt"
    f.write_text("s3cret")
    read(ag, cards, f)
    use(ag, cards, "write_file", path=str(f), content="x")
    run_dir = ck._run_dir("owner-a", "r1")
    blobs = list((run_dir / "blobs").iterdir())
    assert blobs and all(stat.S_IMODE(b.stat().st_mode) == 0o600 for b in blobs)
    assert stat.S_IMODE((run_dir / "blobs").stat().st_mode) == 0o700
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((run_dir / "run.json").stat().st_mode) == 0o600


def test_retention_drops_old_runs_and_keeps_the_newest_twenty(env):
    now = time.time()
    for i in range(23):
        d = ck._run_dir("owner-a", f"run{i}")
        ck._mkdir(d / "blobs")
        (d / "run.json").write_text(
            '{"id": "run%d", "principal": "owner-a", "started": %f}' % (i, now - i * 60))
    old = ck._run_dir("owner-a", "ancient")
    ck._mkdir(old / "blobs")
    (old / "run.json").write_text(
        '{"id": "ancient", "principal": "owner-a", "started": %f}' % (now - 8 * 86400))
    other = ck._run_dir("owner-b", "mine")
    ck._mkdir(other / "blobs")
    (other / "run.json").write_text('{"id": "mine", "principal": "owner-b", "started": 1.0}')
    removed = ck.prune("owner-a")
    ids = {rid for _, rid, _ in ck._runs("owner-a")}
    assert removed == 4 and "ancient" not in ids and len(ids) == 20
    assert ids == {f"run{i}" for i in range(20)}                      # the newest 20 stay
    assert other.exists()                                             # another owner's run is not ours to prune


def test_starting_a_run_prunes(env):
    old = ck._run_dir("owner-a", "ancient")
    ck._mkdir(old / "blobs")
    (old / "run.json").write_text(
        '{"id": "ancient", "principal": "owner-a", "started": %f}' % (time.time() - 9 * 86400))
    ag, cards = agent("fresh"), Cards()
    use(ag, cards, "write_file", path=str(env / "x.txt"), content="x")
    assert not old.exists() and ck.find_run("owner-a", "fresh")


def test_one_owner_cannot_see_or_undo_anothers_run(env):
    a_agent, b_agent, cards = agent("shared-id", "owner-a"), agent("other", "owner-b"), Cards()
    f = env / "a.txt"
    use(a_agent, cards, "write_file", path=str(f), content="A's file")
    out = use(b_agent, cards, "undo_changes", task="shared-id")
    assert "No checkpointed run" in out and f.exists() and cards.cards == []
    assert "no checkpointed file changes" in use(b_agent, cards, "undo_changes").lower()
    assert ck.find_run("owner-b", "shared-id") is None
    # The same run id under another owner is a different run with its own files.
    g = env / "b.txt"
    use(agent("shared-id", "owner-b"), cards, "write_file", path=str(g), content="B")
    use(a_agent, cards, "undo_changes", task="shared-id")
    assert not f.exists() and g.exists()


def test_without_an_agent_nothing_is_checkpointed(env):
    f = env / "a.txt"
    out = registry.dispatch("write_file", {"path": str(f), "content": "cli"}, source="cli")
    assert f.read_text() == "cli" and "Not checkpointed" not in out
    assert not ck._root().exists()


def test_empty_task_means_this_runs_changes_else_the_newest_run(env):
    cards = Cards()
    one, two = agent("run-one"), agent("run-two")
    f1, f2 = env / "one.txt", env / "two.txt"
    use(one, cards, "write_file", path=str(f1), content="1")
    time.sleep(0.01)
    use(two, cards, "write_file", path=str(f2), content="2")
    use(one, cards, "undo_changes")                       # run-one has changes: its own
    assert not f1.exists() and f2.exists()
    orchestrator = agent("no-changes")
    use(orchestrator, cards, "undo_changes")              # nothing of its own: the newest run
    assert not f2.exists()


# -- who gets undo_changes and how it is reported ---------------------------------------

def test_undo_is_a_destructive_skill_for_system_ops_and_the_orchestrator_only():
    entry = registry.get_skill("undo_changes")
    assert entry.danger == safety.Danger.DESTRUCTIVE
    assert "undo_changes" not in {d["name"] for d in registry.tool_declarations()}
    from kravein.agents import load_profile
    for name, expect in (("system-ops", True), ("orchestrator", True), ("research", False),
                         ("reviewer", False)):
        assert ("undo_changes" in registry.resolve_tools(load_profile(name).tools)) is expect


def test_the_delegate_report_and_task_status_offer_the_undo(env, monkeypatch):
    f = env / "made.txt"
    agent_, router = orchestrate(
        [call("delegate", agent="system-ops", task="make a file", wait=True), say("ok")],
        [call("write_file", path=str(f), content="hi"), say("OUTCOME: done\nSUMMARY: made it\n")],
        confirmer=lambda *a: True,
    )
    agent_.ask("make a file")
    report = [m for m in agent_.messages if m["role"] == "tool"][0]["content"]
    did = report.split("#", 1)[1].split("]")[0]
    assert f.read_text() == "hi"
    assert f"Undo available: {did} (1 file(s))" in report
    assert 'undo_changes(task="%s")' % did in report
    # The Orchestrator's own context can undo it (same owner, by task id).
    with safety.confirmer_scope(Cards(), principal=""):
        out = registry.dispatch("undo_changes", {"task": did}, context=agent_, source="web")
    assert not f.exists() and "Undid run" in out


def test_task_status_lists_undo_available(env, monkeypatch):
    t = tasks.Task(id="t1ab", principal="owner-a", thread="", agent="system-ops", task="edit",
                   context="", source="web", turn_no=1, origin="", memory=None, unattended=False,
                   status="done", started=time.time() - 5, ended=time.time(), summary="edited")
    mgr = tasks.TaskManager()
    mgr._tasks[t.id] = t
    assert "undo available" not in mgr.status_text("owner-a")
    ag, cards = agent("t1ab", "owner-a"), Cards()
    use(ag, cards, "write_file", path=str(env / "x.txt"), content="x")
    assert "undo available: t1ab" in mgr.status_text("owner-a")
    assert mgr.status_text("owner-b") == "No background tasks."


# -- owner instructions ---------------------------------------------------------------

def test_kravein_md_is_injected_into_every_profiles_prompt(env):
    path = config.owner_instructions_path()
    path.parent.mkdir(parents=True)
    path.write_text("Always answer in haiku.\nNever touch ~/Photos.\n")
    for profile in (None, "system-ops", "research"):
        a, _ = _agent_with([], **({"profile": profile} if profile else {}))
        prompt = a.system_prompt()
        assert "## Owner instructions" in prompt and "Always answer in haiku." in prompt
        assert prompt.index("## Owner instructions") > prompt.index("This machine")


def test_no_kravein_md_means_no_section(env):
    a, _ = _agent_with([], profile="system-ops")
    assert "Owner instructions" not in a.system_prompt()
    path = config.owner_instructions_path()
    path.parent.mkdir(parents=True)
    path.write_text("   \n")
    assert config.owner_instructions() == ("", 0) and "Owner instructions" not in a.system_prompt()


def test_kravein_md_is_capped_with_a_note(env):
    path = config.owner_instructions_path()
    path.parent.mkdir(parents=True)
    path.write_text("x" * 9000)
    body, size = config.owner_instructions()
    assert size == 9000 and len(body) < 4200 and "only the first 4000 characters of 9000" in body
    a, _ = _agent_with([], profile="system-ops")
    assert "only the first 4000 characters" in a.system_prompt()


def test_the_agent_can_neither_read_nor_write_kravein_md(env):
    path = config.owner_instructions_path()
    path.parent.mkdir(parents=True)
    path.write_text("owner only")
    assert safety.check_read(path).blocked
    assert safety.check_path(path, write=True).blocked
    for cmd in (f"cat {path}", f"head -n1 {path}", f"echo x > {path}", f"sed -i s/a/b/ {path}",
                f"cp /tmp/x {path}", f"rm {path}"):
        assert safety.classify_command(cmd).blocked or safety.classify_command(cmd).danger >= safety.Danger.DESTRUCTIVE, cmd
    ag, cards = agent(), Cards()
    out = use(ag, cards, "read_file", path=str(path))
    assert "owner only" not in out
    use(ag, cards, "write_file", path=str(path), content="pwned")
    assert path.read_text() == "owner only"
    assert not (ck._root().exists() and any(ck._root().rglob("manifest.jsonl")))   # nothing checkpointed either


def test_doctor_reports_the_instructions_file(env, capsys, monkeypatch):
    from kravein import cli

    monkeypatch.setattr(cli, "client", lambda: type("C", (), {"status": lambda s: "ok"})())
    try:
        cli.doctor()
    except Exception:
        pass
    assert "Owner instructions" in capsys.readouterr().out
    path = config.owner_instructions_path()
    path.parent.mkdir(parents=True)
    path.write_text("hello owner")
    try:
        cli.doctor()
    except Exception:
        pass
    assert "11 characters" in capsys.readouterr().out


# -- safety.shell_tokens ---------------------------------------------------------------

def test_shell_tokens_is_public_and_matches_the_classifiers_view():
    assert safety.shell_tokens("cd /tmp && ls -la") == ["cd", "/tmp", "&&", "ls", "-la"]
    assert safety.shell_tokens("echo 'a b' > out.txt") == ["echo", "a b", ">", "out.txt"]
    assert safety.shell_tokens("echo $HOME") is None            # cannot tell: not "safe"
    assert safety.shell_tokens("echo `id`") is None
    assert safety.shell_tokens("echo 'unbalanced") is None
    assert safety.shell_tokens("a\nb") is None
    assert safety.shell_tokens("echo a#b; rm x") == ["echo", "a#b", ";", "rm", "x"]
