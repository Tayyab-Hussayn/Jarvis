"""Tests: app-name normalisation and ambiguity in the shared matcher (#181)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein.platform import apps  # noqa: E402


def _write(d: Path, fname: str, name: str, exe: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nExec={exe} %U\n"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    user = tmp_path / "user"
    system = tmp_path / "usr"
    _write(user / "applications", "antigravity.desktop", "Antigravity", "antigravity")
    _write(system / "applications", "antigravity-ide.desktop",
           "Antigravity IDE", "/opt/ag/antigravity-ide")
    _write(system / "applications", "firefox.desktop", "Firefox", "firefox")
    _write(tmp_path / "flat" / "applications", "org.x.Zed.desktop", "Zed", "zed")
    monkeypatch.setenv("XDG_DATA_HOME", str(user))
    monkeypatch.setenv("XDG_DATA_DIRS", f"{system}:{tmp_path / 'flat'}")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "nohome")
    apps.index.cache_clear()
    yield
    apps.index.cache_clear()


@pytest.mark.parametrize("q", ["Anti Gravity", "anti-gravity", "AntiGravity",
                               "anti_gravity", "anti.gravity"])
def test_spelling_variants_pick_exact_antigravity(home, q):
    entry, msg = apps.resolve(q)
    assert entry is not None and entry.name == "Antigravity", msg


def test_ide_phrase_picks_the_ide(home):
    entry, _ = apps.resolve("Anti Gravity IDE")
    assert entry.name == "Antigravity IDE"


def test_search_finds_both_for_spaced_name(home):
    names = {e.name for e in apps.find("Anti Gravity")}
    assert names == {"Antigravity", "Antigravity IDE"}


def test_ambiguous_without_exact_refuses_and_lists(home):
    entry, msg = apps.resolve("antigrav")
    assert entry is None
    assert "Antigravity" in msg and "Antigravity IDE" in msg and "several" in msg


def test_typo_still_found_by_fuzzy(home):
    assert apps.find("antigravty")


def test_not_found_suggests_closest_names(home):
    msg = apps.not_found_message("antigravty2x")
    assert "Closest installed" in msg and "Antigravity" in msg


def test_flatpak_style_dir_is_indexed(home):
    entry, _ = apps.resolve("zed")
    assert entry is not None and entry.name == "Zed"


def test_launch_by_name_does_not_pick_first_of_several(home, monkeypatch):
    launched = []
    monkeypatch.setattr(apps, "launch", lambda e, **k: launched.append(e) or "ok")
    out = apps.launch_by_name("antigrav")
    assert not launched and "several" in out
