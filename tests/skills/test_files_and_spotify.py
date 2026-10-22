"""Tests: files and spotify.

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


# Spotify URIs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ask,uri", [
    ("liked songs", "spotify:collection:tracks"),
    ("my liked songs", "spotify:collection:tracks"),
    ("", "spotify:collection:tracks"),
    ("spotify:playlist:abc", "spotify:playlist:abc"),
])
def test_spotify_uri_mapping(ask, uri):
    from kravein.skills.media import _spotify_uri

    assert _spotify_uri(ask) == uri


def test_spotify_free_text_becomes_a_search():
    from kravein.skills.media import _spotify_uri

    assert _spotify_uri("lo-fi beats").startswith("spotify:search:")


# ---------------------------------------------------------------------------


# File manager and Spotify navigation
# ---------------------------------------------------------------------------

def test_playerctl_calls_use_a_utf8_locale():
    """shell.run forces LC_ALL=C, which mangles track titles.

    Metadata came back as "Invalid byte sequence in conversion input" for any
    title with an em dash or an accent — which is most of them.
    """
    import inspect

    from kravein.platform import audio

    source = inspect.getsource(audio)
    assert '_UTF8' in source
    for fn in (audio.metadata, audio.open_uri, audio.now_playing, audio.players):
        assert "env=_UTF8" in inspect.getsource(fn), f"{fn.__name__} still runs under LC_ALL=C"


@pytest.mark.parametrize("ask,uri", [
    ("liked songs", "spotify:collection:tracks"),
    ("your library", "spotify:collection"),
    ("saved albums", "spotify:collection:albums"),
    ("playlists", "spotify:collection:playlists"),
    ("podcasts", "spotify:collection:podcasts"),
    ("home", "spotify:home"),
    ("queue", "spotify:queue"),
])
def test_spotify_views_are_reachable_as_uris(ask, uri):
    """Sidebar navigation without touching the GUI."""
    from kravein.skills.media import _spotify_uri

    assert _spotify_uri(ask) == uri


def test_rename_keeps_the_file_in_place(tmp_path):
    from kravein.skills import filemanager as fm

    target = tmp_path / "notes.txt"
    target.write_text("hi")
    with safety.confirmer_scope(lambda *_: True):
        out = fm.rename_path(path=str(target), new_name="renamed.txt")
    assert (tmp_path / "renamed.txt").exists()
    assert not target.exists()
    assert "Renamed" in out


def test_rename_refuses_a_path_as_a_name(tmp_path):
    from kravein.skills import filemanager as fm

    target = tmp_path / "notes.txt"
    target.write_text("hi")
    out = fm.rename_path(path=str(target), new_name="../escaped.txt")
    assert "not a path" in out
    assert target.exists()


def test_rename_refuses_to_overwrite(tmp_path):
    from kravein.skills import filemanager as fm

    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    out = fm.rename_path(path=str(tmp_path / "a.txt"), new_name="b.txt")
    assert "already exists" in out
    assert (tmp_path / "a.txt").read_text() == "a"
    assert (tmp_path / "b.txt").read_text() == "b"


def test_file_manager_only_replaces_a_window_it_opened(monkeypatch):
    """Navigating must not accumulate windows, and must not kill a stranger's.

    Dolphin opens a new window per invocation and exposes no D-Bus method to
    retarget one, so Kravein replaces the window it opened — identified by pid
    *and* verified by command line, so a reused pid is left alone.
    """
    from kravein.skills import filemanager as fm

    killed = []

    class _Proc:
        def __init__(self, cmdline):
            self._cmdline = cmdline

        def cmdline(self):
            return self._cmdline

        def terminate(self):
            killed.append(self._cmdline)

        def wait(self, timeout=None):
            return 0

    import psutil

    fm._last_window.update(pid=4242, manager="dolphin")
    monkeypatch.setattr(psutil, "Process", lambda pid: _Proc(["something-else"]))
    fm._replace_previous("dolphin")
    assert killed == [], "terminated a process that was not the file manager"

    fm._last_window.update(pid=4242, manager="dolphin")
    monkeypatch.setattr(psutil, "Process", lambda pid: _Proc(["dolphin", "/tmp"]))
    fm._replace_previous("dolphin")
    assert killed and "dolphin" in killed[0]


# ---------------------------------------------------------------------------
