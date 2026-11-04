"""Regression tests from the #223 self-review: restore safety and source checks."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import config, registry, safety  # noqa: E402
from kravein.agents import checkpoints as ck  # noqa: E402
from test_checkpoints import Cards, agent, env, read, text, use  # noqa: E402,F401  (env is autouse)


def test_a_truncated_tree_snapshot_never_marks_the_owners_files_as_created(env, monkeypatch):
    monkeypatch.setattr(ck, "MAX_TREE_ENTRIES", 5)
    ag, cards = agent(), Cards()
    src, dst = env / "src", env / "dst"
    src.mkdir()
    (src / "new.txt").write_text("N")
    (dst / "src").mkdir(parents=True)
    for i in range(12):                                     # the owner's files, past the cap
        (dst / "src" / f"mine{i:02d}.txt").write_text(f"mine {i}")
    out = use(ag, cards, "copy_path", source=str(src), destination=str(dst))
    assert "Not checkpointed" in out and "only the first 5" in out
    use(ag, cards, "undo_changes")
    survivors = sorted(p.name for p in (dst / "src").iterdir())
    assert all(f"mine{i:02d}.txt" in survivors for i in range(12))
    assert all(text(dst / "src" / f"mine{i:02d}.txt") == f"mine {i}" for i in range(12))


def test_a_failed_restore_leaves_the_file_alone_and_a_retry_works(env, monkeypatch):
    ag, cards = agent(), Cards()
    f = env / "a.txt"
    f.write_text("ORIGINAL CONTENT")
    read(ag, cards, f)
    use(ag, cards, "write_file", path=str(f), content="changed")
    real = shutil.copyfileobj
    calls = {"n": 0}

    def flaky(src, dst, *a, **k):
        calls["n"] += 1
        dst.write(src.read(5))
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copyfileobj", flaky)
    out = use(ag, cards, "undo_changes")
    assert "could not be restored" in out and "disk full" in out
    assert text(f) == "changed"                                   # not a half-copied file
    assert not [p for p in env.iterdir() if "kravein-undo" in p.name]   # no temp litter
    monkeypatch.setattr(shutil, "copyfileobj", real)
    out = use(ag, cards, "undo_changes")                          # a second undo recovers, no force
    assert text(f) == "ORIGINAL CONTENT" and "restored 1 file(s)" in out


def test_a_restored_private_file_is_never_world_readable(env, monkeypatch):
    ag, cards = agent(), Cards()
    f = env / "private.txt"
    f.write_text("secret")
    os.chmod(f, 0o600)
    read(ag, cards, f)
    use(ag, cards, "write_file", path=str(f), content="x")
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(stat.S_IMODE(os.stat(src).st_mode))      # the mode when it appears
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    use(ag, cards, "undo_changes")
    assert seen == [0o600] and stat.S_IMODE(f.stat().st_mode) == 0o600 and text(f) == "secret"


def test_a_folder_that_became_a_link_elsewhere_is_never_written_through(env):
    ag, cards = agent(), Cards()
    d = env / "proj"
    d.mkdir()
    f = d / "a.txt"
    f.write_text("a0")
    read(ag, cards, f)
    use(ag, cards, "write_file", path=str(f), content="a1")
    elsewhere = env / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.txt").write_text("a1")                        # identical content over there
    shutil.rmtree(d)
    d.symlink_to(elsewhere)
    out = use(ag, cards, "undo_changes")
    assert text(elsewhere / "a.txt") == "a1"
    assert "Refused 1 protected path" in out and "points somewhere else" in out


def test_an_unchanged_oversized_file_is_not_listed_as_unrestorable(env, monkeypatch):
    from kravein.agents import checkpoints
    ag, cards = agent(), Cards()
    big = env / "big.txt"
    big.write_text("x" * 5000)
    monkeypatch.setattr(config, "get", lambda d, default=None:
                        0.001 if d == "agents.checkpoint_file_max_mb" else default)
    with checkpoints.track(ag, big, kind="touch-only"):
        pass                                                      # looked at, never changed
    out = use(ag, cards, "undo_changes")
    assert "never checkpointed" not in out and "Nothing to undo" in out


# -- copy_path / move_path hold their source to the read rules ------------------------

def private_file(name="api_keys.json"):
    p = config.CONFIG_DIR / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("sk-secret")
    return p


def test_copy_path_cannot_carry_kravein_private_files_into_the_workspace(env):
    ag, cards = agent(), Cards()
    for name in ("api_keys.json", "KRAVEIN.md"):
        src = private_file(name)
        dst = env / f"copy-{name}"
        out = use(ag, cards, "copy_path", source=str(src), destination=str(dst))
        assert not dst.exists() and "may not read" in out, out


def test_move_path_cannot_move_kravein_private_files_out(env):
    ag, cards = agent(), Cards()
    src = private_file("KRAVEIN.md")
    dst = env / "out.md"
    out = use(ag, cards, "move_path", source=str(src), destination=str(dst))
    assert src.exists() and not dst.exists() and out
    assert "Kravein's own config" in out


def test_copying_or_moving_a_secret_asks_on_a_card_that_auto_never_answers(env, monkeypatch):
    home = Path(os.environ["HOME"])
    key = home / ".ssh" / "id_ed25519"
    key.parent.mkdir()
    key.write_text("PRIVATE KEY")
    ag, cards = agent(), Cards()
    dst = env / "stolen"
    use(ag, cards, "copy_path", source=str(key), destination=str(dst))
    assert dst.read_text() == "PRIVATE KEY"
    assert cards.cards and "sent to the model" in cards.cards[-1][1]
    out = use(ag, Cards(answer=False), "copy_path", source=str(key), destination=str(env / "no"))
    assert not (env / "no").exists() and "Cancelled" in out
    # Register the restore of `_auto_until` BEFORE turning auto on, so teardown
    # puts back "off". (Calling monkeypatch.setattr after enabling it records
    # the enabled value as the one to restore and leaks auto-approve into later
    # tests: test_proposals then waits forever for a card nobody is shown.)
    monkeypatch.setattr(safety, "_auto_until", None)
    safety.set_confirmation_mode("auto", by="test")
    try:
        asked = Cards()
        use(ag, asked, "copy_path", source=str(key), destination=str(env / "auto-copy"))
        assert asked.cards                                          # auto-approve did not answer it
    finally:
        safety._auto_until = None


def test_ordinary_copies_and_moves_are_unaffected(env):
    ag, cards = agent(), Cards()
    a = env / "a.txt"
    a.write_text("A")
    use(ag, cards, "copy_path", source=str(a), destination=str(env / "b.txt"))
    use(ag, cards, "move_path", source=str(env / "b.txt"), destination=str(env / "c.txt"))
    assert text(env / "c.txt") == "A" and not (env / "b.txt").exists()
    assert not [c for c in cards.cards if "sent to the model" in c[1]]


# -- undo_changes(force=True) is an owner-only card (orchestrator review) ----------------

@pytest.fixture
def auto_on(monkeypatch):
    # Register the restore BEFORE enabling auto, so teardown puts back "off".
    monkeypatch.setattr(safety, "_auto_until", None)
    safety.set_confirmation_mode("auto", by="test")
    yield
    safety._auto_until = None


def _run_with_newer_edit(env, only_changed=False):
    """A run that changed a.txt (then the owner edited it again) and b.txt."""
    ag, cards = agent(), Cards()
    a, b = env / "a.txt", env / "b.txt"
    a.write_text("a0")
    read(ag, cards, a)
    use(ag, cards, "write_file", path=str(a), content="a1")
    if not only_changed:
        b.write_text("b0")
        read(ag, cards, b)
        use(ag, cards, "write_file", path=str(b), content="b1")
    a.write_text("owner's newer work")
    return ag, a, b


def test_the_verdict_flags():
    assert not safety.Verdict(safety.Danger.DESTRUCTIVE).no_auto
    assert safety.Verdict(safety.Danger.DESTRUCTIVE, secret_read=True).no_auto
    assert safety.Verdict(safety.Danger.DESTRUCTIVE, owner_only=True).no_auto


def test_auto_mode_answers_the_normal_undo_card_but_never_the_force_card(env, auto_on):
    ag, a, b = _run_with_newer_edit(env)
    asked = Cards()
    out = use(ag, asked, "undo_changes", force=True)
    assert text(a) == "a0" and text(b) == "b0"
    assert [c[0] for c in asked.cards] == [asked.cards[0][0]]        # exactly one card reached the owner
    assert "over newer changes" in asked.cards[0][0] and str(a) in asked.cards[0][1]
    assert "restored 2 file(s)" in out


def test_a_declined_force_card_keeps_the_newer_work_in_auto_mode(env, auto_on):
    ag, a, b = _run_with_newer_edit(env)
    out = use(ag, Cards(answer=False), "undo_changes", force=True)
    assert text(a) == "owner's newer work" and "Cancelled" in out
    assert text(b) == "b0"                                           # the ordinary part was auto-approved


def test_the_force_card_is_denied_with_no_confirmer_even_in_auto_mode(env, auto_on):
    ag, a, b = _run_with_newer_edit(env, only_changed=True)
    with safety.confirmer_scope(None, principal="owner-a"):
        out = registry.dispatch("undo_changes", {"force": True}, context=ag, source="web")
    assert text(a) == "owner's newer work" and "needs confirmation" in out


def test_an_always_rule_cannot_answer_an_owner_only_card(env):
    safety.add_always_rule("some signature", "owner-a")
    v = safety.Verdict(safety.Danger.DESTRUCTIVE, reasons=["overwrites newer work"],
                       owner_only=True, signature="some signature")
    asked = Cards()
    with safety.confirmer_scope(asked, principal="owner-a"):
        safety.enforce(v, "overwrite", skill="undo_changes", source="web")
    assert asked.cards and safety.always_offer() == {"allowed": False, "signature": ""}
