"""Tests: a Live user transcript is always English text, whatever script Gemini returns (#182)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import audit, live  # noqa: E402
from kravein.live import LiveSession, NetworkAudio  # noqa: E402
from test_live_audio import _run_in_thread, _wait, backend  # noqa: E402,F401
from test_live_turns import _speak_script  # noqa: E402

SCRIPTS = {
    "telugu": "హియర్ ఇస్ ఆంటీ గ్రావిటీ ఐడి",
    "devanagari": "हियर इज़ एंटी ग्रेविटी",
    "urdu": "ہیئر از اینٹی گریویٹی",
}


@pytest.mark.parametrize("name", list(SCRIPTS))
def test_clean_transcript_strips_every_foreign_script(name):
    assert live.clean_transcript(SCRIPTS[name]) == ""
    assert live.clean_transcript("open " + SCRIPTS[name] + " now") == "open now"


def _run(backend, monkeypatch, text):
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    backend.script = _speak_script([text])
    net = NetworkAudio()
    net.push_input(b"\x01\x00" * 16000)
    s = LiveSession(source=net, sink=net, observer=lambda k, t: events.append((k, t)))
    t, _ = _run_in_thread(s)
    assert _wait(lambda: len([e for e in events if e[0] == "listening"]) > 1, 5)
    s.stop()
    t.join(3)
    return events


@pytest.mark.parametrize("name", list(SCRIPTS))
def test_foreign_script_goes_through_llm_english(backend, monkeypatch, name):
    from kravein import llm

    monkeypatch.setattr(llm, "client", lambda: type("C", (), {
        "chat": lambda self, prompt, **k: "Here is Anti Gravity IDE installed."})())
    events = _run(backend, monkeypatch, SCRIPTS[name])
    assert [x for k, x in events if k == "heard"] == ["Here is Anti Gravity IDE installed."]
    shown = [x for k, x in events if k in ("heard", "heard_partial")]
    assert all(c < "ɐ" or c == "…" for x in shown for c in x)


def test_llm_failure_shows_nothing_foreign(backend, monkeypatch):
    from kravein import llm
    from kravein.platform import stt

    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(stt, "translate_available", lambda: False)
    monkeypatch.setattr(llm, "client", boom)
    events = _run(backend, monkeypatch, SCRIPTS["telugu"])
    assert not [x for k, x in events if k == "heard"]


# -- #188: heard frames carry the turn id; the deferred English line replaces it --

def _forbid_stt(monkeypatch):
    """The Live path must never touch Whisper (#192)."""
    from kravein.platform import stt

    def boom(*a, **k):
        raise AssertionError("Live voice touched stt/Whisper")

    for name in ("translate_available", "translate_ready", "preload_translate",
                 "translate_to_english", "transcribe"):
        if hasattr(stt, name):
            monkeypatch.setattr(stt, name, boom)


def _run3(backend, monkeypatch, script, listens):
    from kravein import llm

    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    _forbid_stt(monkeypatch)
    monkeypatch.setattr(llm, "client", lambda: type("C", (), {
        "chat": lambda self, prompt, **k: "Hey, are you listening to me?"})())
    backend.script = script
    net = NetworkAudio()
    net.push_input(b"\x01\x00" * 16000)
    s = LiveSession(source=net, sink=net, observer=lambda k, t, d: events.append((k, t, d)))
    t, _ = _run_in_thread(s)
    assert _wait(lambda: len([e for e in events if e[0] == "listening"]) > listens, 5)
    s.stop()
    t.join(3)
    return events


def test_latin_line_is_final_immediately_without_replace(backend, monkeypatch):
    events = _run3(backend, monkeypatch, _speak_script(["Open the", " browser"]), 1)
    heard = [(t, d) for k, t, d in events if k == "heard"]
    assert [t for t, _ in heard] == ["Open the browser"]
    assert heard[0][1]["final"] is True and "replace" not in heard[0][1]
    partials = [d for k, _, d in events if k == "heard_partial"]
    assert {d["turn"] for d in partials} == {heard[0][1]["turn"]}


def test_foreign_line_is_a_deferred_replace_with_the_turn_id(backend, monkeypatch):
    events = _run3(backend, monkeypatch, _speak_script([SCRIPTS["devanagari"]]), 1)
    heard = [(t, d) for k, t, d in events if k == "heard"]
    assert [t for t, _ in heard] == ["Hey, are you listening to me?"]
    assert heard[0][1]["final"] is True and heard[0][1]["replace"] is True
    partials = [d for k, _, d in events if k == "heard_partial"]
    assert partials and {d["turn"] for d in partials} == {heard[0][1]["turn"]}
    # it still arrives before `listening`, which closes the bubble
    kinds = [k for k, _, _ in events]
    assert kinds.index("heard") < kinds.index("listening", kinds.index("heard"))


def test_two_quick_turns_do_not_cross_ids(backend, monkeypatch):
    import asyncio
    from test_live_turns import _resp, _sc, _text

    async def script(_s):
        for word in (SCRIPTS["devanagari"], "second turn"):
            yield _resp(server_content=_sc(input_transcription=_text(word)))
            await asyncio.sleep(0.3)
            yield _resp(server_content=_sc(turn_complete=True))

    events = _run3(backend, monkeypatch, script, 2)
    heard = [d for k, _, d in events if k == "heard"]
    assert len(heard) == 2 and heard[0]["turn"] != heard[1]["turn"]
    partial_turns = [d["turn"] for k, _, d in events if k == "heard_partial"]
    assert partial_turns[0] == heard[0]["turn"] and partial_turns[-1] == heard[1]["turn"]


def test_session_has_no_whisper_state():
    s = LiveSession(source=NetworkAudio(), sink=NetworkAudio())
    assert not hasattr(s, "_mic") and not hasattr(s, "_preload_translate")
