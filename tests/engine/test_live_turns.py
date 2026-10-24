"""Tests: Gemini Live turn hygiene (#97) — control tokens, empty turns, script/language."""

from __future__ import annotations

import asyncio
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


def test_foreign_script_is_never_displayed_or_transliterated():
    assert live.clean_transcript("नमस्ते <ctrl46>") == ""
    assert live.clean_transcript("open फ्लैशलाइट now") == "open now"
    assert live.clean_transcript("hello workspace 4") == "hello workspace 4"
    assert not hasattr(live, "transliterate_devanagari")


def _speak_script(deltas, tail=()):
    async def script(_s):
        for d in deltas:
            yield _resp(server_content=_sc(input_transcription=_text(d)))
        await asyncio.sleep(0.3)  # let the pushed mic audio reach the turn buffer
        yield _resp(server_content=_sc(turn_complete=True))
        for r in tail:
            yield r
    return script


def _translated_session(backend, monkeypatch, deltas, translate, listen_after=1):
    from kravein.platform import stt

    events: list[tuple[str, str]] = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(stt, "translate_available", lambda: True)
    monkeypatch.setattr(stt, "translate_ready", lambda: True)
    monkeypatch.setattr(stt, "preload_translate", lambda: None)
    monkeypatch.setattr(stt, "translate_to_english", translate)
    backend.script = _speak_script(deltas)
    net = NetworkAudio()
    net.push_input(b"\x01\x00" * 16000)  # one second
    s = LiveSession(source=net, sink=net, observer=lambda k, t: events.append((k, t)))
    t, _ = _run_in_thread(s)
    assert _wait(lambda: len([e for e in events if e[0] == "listening"]) > listen_after, 5)
    s.stop()
    t.join(3)
    return events


def test_user_turn_is_shown_as_whispers_english_not_geminis_devanagari(backend, monkeypatch):
    seen = {}

    def translate(path):
        import wave
        with wave.open(str(path)) as w:
            seen["frames"] = w.getnframes()
            seen["rate"] = w.getframerate()
        return "Hey, open the flashlight on my mobile."

    events = _translated_session(backend, monkeypatch, ["हे ओपन ", "फ्लैशलाइट"], translate)
    heard = [t for k, t in events if k == "heard"]
    assert heard == ["Hey, open the flashlight on my mobile."]
    assert seen["rate"] == 16000 and seen["frames"] >= 16000
    # Devanagari never reaches the client, not even as a partial.
    shown = [t for k, t in events if k in ("heard", "heard_partial")]
    assert not any("ऀ" <= c <= "ॿ" for t in shown for c in t)
    assert live.HEARING_TEXT in [t for k, t in events if k == "heard_partial"]
    # The bubble closes only after the English text arrived.
    order = [k for k, _ in events if k in ("heard", "listening")]
    assert order[-2:] == ["heard", "listening"]


def test_translate_failure_falls_back_to_geminis_latin_text(backend, monkeypatch):
    def boom(path):
        raise RuntimeError("model download failed")

    events = _translated_session(backend, monkeypatch, ["turn off the light"], boom)
    assert [t for k, t in events if k == "heard"] == ["turn off the light"]


def test_translate_failure_with_devanagari_shows_nothing_foreign(backend, monkeypatch):
    def boom(path):
        raise RuntimeError("offline")

    events = _translated_session(backend, monkeypatch, ["टर्न ऑफ"], boom)
    assert [t for k, t in events if k == "heard"] == []
    assert [e for e in events if e[0] == "listening"][1:]  # the turn still closed


def test_a_slow_transcript_does_not_delay_the_reply(backend, monkeypatch):
    import time

    from kravein.platform import stt

    gate = threading.Event()
    monkeypatch.setattr(stt, "translate_available", lambda: True)
    monkeypatch.setattr(stt, "translate_ready", lambda: True)
    monkeypatch.setattr(stt, "preload_translate", lambda: None)
    monkeypatch.setattr(stt, "translate_to_english", lambda p: (gate.wait(5), "late text")[1])
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    events: list[tuple[str, str]] = []

    async def script(_s):
        yield _resp(server_content=_sc(input_transcription=_text("नमस्ते")))
        await asyncio.sleep(0.3)
        yield _resp(server_content=_sc(turn_complete=True))
        yield _resp(server_content=_sc(output_transcription=_text("Done.")))
        yield _resp(server_content=_sc(turn_complete=True))

    backend.script = script
    net = NetworkAudio()
    net.push_input(b"\x01\x00" * 16000)
    s = LiveSession(source=net, sink=net, observer=lambda k, t: events.append((k, t)))
    t, _ = _run_in_thread(s)
    try:
        assert _wait(lambda: "Done." in _answers(events), 3)  # reply flowed while Whisper ran
        assert not [e for e in events if e[0] == "heard"]
    finally:
        gate.set()
    assert _wait(lambda: ("heard", "late text") in events, 3)
    s.stop()
    t.join(3)


def test_reply_of_only_control_tokens_is_replaced_by_a_retry_prompt(backend, monkeypatch):
    events, records = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(input_transcription=_text("स्विच टू वर्क स्पेस वन"))),
        _resp(server_content=_sc(output_transcription=_text("<ctrl46>"))),
        _resp(server_content=_sc(output_transcription=_text("<ctrl46>"), turn_complete=True)),
    ])
    assert _answers(events) == [live.EMPTY_REPLY_TEXT]
    assert not any("<ctrl" in t for _, t in events)
    assert [t for k, t in events if k == "heard"] == []
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


def test_model_still_downloading_means_no_wait_and_gemini_fallback(backend, monkeypatch):
    from kravein.platform import stt

    called = []
    monkeypatch.setattr(stt, "translate_available", lambda: True)
    monkeypatch.setattr(stt, "translate_ready", lambda: False)
    monkeypatch.setattr(stt, "preload_translate", lambda: None)
    monkeypatch.setattr(stt, "translate_to_english", lambda p: called.append(p) or "x")
    events, _ = _drive(backend, monkeypatch, [
        _resp(server_content=_sc(input_transcription=_text("switch to workspace one"))),
        _resp(server_content=_sc(output_transcription=_text("Done."), turn_complete=True)),
    ])
    assert [t for k, t in events if k == "heard"] == ["switch to workspace one"]
    assert not called
