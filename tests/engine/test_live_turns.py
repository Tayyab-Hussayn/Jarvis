"""Tests: Gemini Live turn hygiene (#97) — control tokens, empty turns, script/language."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import audit, config, live  # noqa: E402
from kravein.live import LiveSession, NetworkAudio  # noqa: E402
from test_live_audio import _resp, _run_in_thread, _wait, backend  # noqa: E402,F401


def _sc(**kw):
    base = dict(input_transcription=None, output_transcription=None,
                interrupted=False, turn_complete=False, generation_complete=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _text(t):
    return SimpleNamespace(text=t)


def _drive(backend, monkeypatch, responses, tool_call=None):
    """Run a session against scripted responses; return (events, audit records)."""
    events: list[tuple[str, str]] = []
    records: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda ev, **kw: records.append((ev, kw)))

    async def script(_s):
        for r in responses:
            yield r

    backend.script = script
    net = NetworkAudio()
    s = LiveSession(source=net, sink=net, observer=lambda k, t: events.append((k, t)))
    t, _ = _run_in_thread(s)
    assert _wait(lambda: [e for e in events if e[0] == "listening"][1:] or False, 3)
    s.stop()
    t.join(3)
    return events, records


def _answers(events):
    return [t for k, t in events if k == "answer"]


@pytest.mark.parametrize("raw,clean", [
    ("<ctrl46><ctrl46>", ""),
    ("Done<ctrl46> now", "Done now"),
    ("<unused12>hi", "hi"),
    ("a <start_of_turn>b<end_of_turn>", "a b"),
    ("plain text <not a token>", "plain text <not a token>"),
])
def test_control_tokens_are_stripped(raw, clean):
    assert live.strip_control_tokens(raw) == clean


def test_devanagari_transcript_becomes_latin():
    out = live.transliterate_devanagari("स्विच टू वर्क स्पेस वन")
    assert out == "svich tu vark spes van"
    assert live.transliterate_devanagari("hello workspace 4") == "hello workspace 4"
    assert not any("ऀ" <= c <= "ॿ" for c in live.clean_transcript("नमस्ते <ctrl46>"))


def test_reply_of_only_control_tokens_is_replaced_by_a_retry_prompt(backend, monkeypatch):
    events, records = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(input_transcription=_text("स्विच टू वर्क स्पेस वन"))),
        _resp(server_content=_sc(output_transcription=_text("<ctrl46>"))),
        _resp(server_content=_sc(output_transcription=_text("<ctrl46>"), turn_complete=True)),
    ])
    assert _answers(events) == [live.EMPTY_REPLY_TEXT]
    assert not any("<ctrl" in t for _, t in events)
    assert [t for k, t in events if k == "heard"] == ["svich tu vark spes van"]
    [(_, kw)] = [r for r in records if r[0] == "live.empty_reply"]
    assert kw["control_only"] is True


def test_a_silent_turn_after_user_speech_is_reported(backend, monkeypatch):
    events, records = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(input_transcription=_text("open chrome"), turn_complete=True)),
    ])
    assert _answers(events) == [live.EMPTY_REPLY_TEXT]
    assert [r for r in records if r[0] == "live.empty_reply"]


def test_a_normal_reply_is_untouched(backend, monkeypatch):
    events, records = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(output_transcription=_text("Done."))),
        _resp(data=b"\x01\x00", server_content=_sc(turn_complete=True)),
    ])
    assert _answers(events) == ["Done."]
    assert not [r for r in records if r[0] == "live.empty_reply"]


def test_audio_only_reply_is_not_empty(backend, monkeypatch):
    events, records = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(input_transcription=_text("hello"))),
        _resp(data=b"\x01\x00\x02\x00"),
        _resp(server_content=_sc(turn_complete=True)),
    ])
    assert _answers(events) == []
    assert not [r for r in records if r[0] == "live.empty_reply"]


def test_idle_turn_complete_is_not_an_empty_reply(backend, monkeypatch):
    events, records = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(turn_complete=True)),
        _resp(server_content=_sc(generation_complete=True)),
    ])
    assert _answers(events) == []
    assert not [r for r in records if r[0] == "live.empty_reply"]


def test_a_turn_that_called_a_tool_is_not_empty(backend, monkeypatch):
    from kravein import registry

    monkeypatch.setattr(registry, "dispatch", lambda name, args, source="": "ok")
    call = SimpleNamespace(id="1", name="switch_workspace", args={"workspace": "1"})
    events, records = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(input_transcription=_text("switch to workspace one"))),
        SimpleNamespace(data=None, server_content=None,
                        tool_call=SimpleNamespace(function_calls=[call])),
        _resp(server_content=_sc(turn_complete=True)),
    ])
    assert _answers(events) == []
    assert not [r for r in records if r[0] == "live.empty_reply"]


def test_barge_in_is_not_an_empty_reply(backend, monkeypatch):
    events, records = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(input_transcription=_text("wait"), interrupted=True)),
        _resp(server_content=_sc(turn_complete=True)),
    ])
    assert _answers(events) == []
    assert not [r for r in records if r[0] == "live.empty_reply"]


@pytest.mark.parametrize("configured,roman,code", [
    ("", True, "en-US"),
    ("en+ur-roman", True, "en-US"),
    ("EN+UR-ROMAN", True, "en-US"),
    ("fr", False, "fr-FR"),
])
def test_language_setting(monkeypatch, configured, roman, code):
    real = config.get
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: configured if key == "voice.language" else real(key, default),
    )
    s = LiveSession()
    assert s._roman() is roman
    assert s._language() == code


def test_roman_preset_puts_the_language_rule_in_the_prompt(monkeypatch):
    real = config.get
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: "" if key == "voice.language" else real(key, default),
    )
    assert "Roman Urdu" in LiveSession()._system_prompt()
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: "fr" if key == "voice.language" else real(key, default),
    )
    assert "Roman Urdu" not in LiveSession()._system_prompt()
