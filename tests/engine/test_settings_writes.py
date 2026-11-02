"""Tests: settings merge isolation, atomic writes, TTS failure reporting (#163)."""

from __future__ import annotations

import copy
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import audit, config, safety, voice  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(safety, "_auto_until", None)
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)


def test_deep_merge_does_not_alias_defaults():
    before = copy.deepcopy(config.DEFAULT_SETTINGS)
    loaded = config.load_settings(reload=True)
    loaded["safety"]["confirmation_mode"] = "mutated"
    loaded["llm"]["brand_new"] = 1
    assert config.DEFAULT_SETTINGS == before
    again = config.load_settings(reload=True)
    assert again["safety"]["confirmation_mode"] != "mutated"
    assert "brand_new" not in again["llm"]


def test_deep_merge_does_not_alias_override():
    override = {"llm": {"x": {"y": 1}}}
    out = config._deep_merge({}, override)
    out["llm"]["x"]["y"] = 2
    assert override["llm"]["x"]["y"] == 1


def test_concurrent_writers_lose_no_update():
    """Two writers on different keys: both must land (old load/merge/save lost one)."""
    config.save_settings({})
    n = 25
    barrier = threading.Barrier(2)

    def web_writer():
        barrier.wait()
        for i in range(n):
            config.update_settings(lambda s: s.setdefault("web_probe", {}).update(n=i))

    def mode_writer():
        barrier.wait()
        for i in range(n):
            safety.set_confirmation_mode("decline" if i % 2 == 0 else "ask", by="t")

    threads = [threading.Thread(target=web_writer), threading.Thread(target=mode_writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    on_disk = json.loads(config.settings_path().read_text())
    assert on_disk["web_probe"]["n"] == n - 1
    assert on_disk["safety"]["confirmation_mode"] == "decline"  # last i = 24 is even


def test_update_settings_accepts_replacement_dict():
    config.update_settings(lambda s: {**s, "marker": 1})
    assert config.load_settings(reload=True)["marker"] == 1


@pytest.mark.parametrize("result", [
    "Text-to-speech is unavailable (no edge-tts and no espeak-ng).",
    "No audio player found. Install mpv:  sudo pacman -S mpv",
    "Could not play speech: boom",
    "Spoke (espeak fallback).",
])
def test_tts_problem_flags_failures(result):
    assert voice.tts_problem(result)


@pytest.mark.parametrize("result", ["Spoke.", "Speaking.", ""])
def test_tts_problem_ignores_success(result):
    assert voice.tts_problem(result) == ""
