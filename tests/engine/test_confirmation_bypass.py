"""Tests: confirmation bypass.

Split out of the legacy tests/test_linux_port.py with no change to any test body.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import llm, registry, safety  # noqa: E402,F401
from kravein.safety import Danger  # noqa: E402,F401


# Confirmation bypass — found by a live model, not by review
# ---------------------------------------------------------------------------
#
# Asked to delete a file over the web UI, a model had four attempts denied
# (`delete_path`, `delete_path permanent`, `rm -f`, `rm`) and then reached for
# `unlink`, which no pattern matched. It ran unconfirmed and the file was
# deleted. Both halves of the fix are covered here: the missing verbs, and the
# denial memory that makes the verb list stop being the only thing standing in
# the way.


@pytest.mark.parametrize("command", [
    "unlink /home/user/notes.txt",
    "find /home/user -name '*.log' -delete",
    "find /home/user -name '*.log' -exec rm {} ;",
    "ls /tmp | xargs rm",
    "rsync -a --delete /src/ /dst/",
    "busybox rm /home/user/notes.txt",
    "gio trash /home/user/notes.txt",
    "trash-put /home/user/notes.txt",
    "sed -i 's/a/b/' /home/user/notes.txt",
    "python3 -c \'import os; os.unlink(\"/home/user/notes.txt\")\'",
    "perl -e \'unlink \"/home/user/notes.txt\"\'",
])
def test_every_way_to_delete_a_file_needs_confirmation(command):
    verdict = safety.classify_command(command)
    assert verdict.danger >= Danger.DESTRUCTIVE, (
        f"{command!r} classified as {verdict.danger.name} — it would run "
        "without asking"
    )


def test_denying_once_refuses_the_same_target_by_another_verb():
    """The actual attack: same file, five different verbs, one denial."""
    asked = []

    def deny(action, detail, danger):
        asked.append(action)
        return False

    with safety.confirmer_scope(deny):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(
                safety.check_delete("/home/user/notes.txt"),
                "move to trash /home/user/notes.txt",
            )

        # Every later spelling of "delete that same file" is refused outright.
        retries = [
            (safety.check_delete("/home/user/notes.txt"),
             "permanently delete /home/user/notes.txt"),
            (safety.classify_command("rm -f /home/user/notes.txt"),
             "run `rm -f /home/user/notes.txt`"),
            (safety.classify_command("unlink /home/user/notes.txt"),
             "run `unlink /home/user/notes.txt`"),
        ]
        for verdict, action in retries:
            assert verdict.danger >= Danger.DESTRUCTIVE, action
            with pytest.raises(safety.SafetyError) as caught:
                safety.enforce(verdict, action)
            assert "already declined" in str(caught.value)

    assert len(asked) == 1, f"the user was asked {len(asked)} times, not once"


def test_denial_memory_does_not_block_a_different_target():
    """Deny A, approve B — a blanket per-turn lockout would break this."""
    answers = {"deny": "/home/user/keep.txt"}

    def confirmer(action, detail, danger):
        return answers["deny"] not in action

    with safety.confirmer_scope(confirmer):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(
                safety.check_delete("/home/user/keep.txt"),
                "move to trash /home/user/keep.txt",
            )
        # A different file is still allowed through.
        safety.enforce(
            safety.check_delete("/home/user/other.txt"),
            "move to trash /home/user/other.txt",
        )


def test_denial_memory_is_cleared_between_turns():
    """A refusal applies to the request it was given in, not forever."""
    def deny(action, detail, danger):
        return False

    with safety.confirmer_scope(deny):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(
                safety.check_delete("/home/user/notes.txt"),
                "move to trash /home/user/notes.txt",
            )

    asked = []

    def approve(action, detail, danger):
        asked.append(action)
        return True

    with safety.confirmer_scope(approve):
        safety.enforce(
            safety.check_delete("/home/user/notes.txt"),
            "move to trash /home/user/notes.txt",
        )
    assert asked, "the next turn must ask again rather than stay refused"


# ---------------------------------------------------------------------------
