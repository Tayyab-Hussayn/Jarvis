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
def test_whisper_unavailable_falls_back_to_llm_english(backend, monkeypatch, name):
    from kravein import llm
    from kravein.platform import stt

    monkeypatch.setattr(stt, "translate_available", lambda: False)
    monkeypatch.setattr(stt, "translate_ready", lambda: False)
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

def _run3(backend, monkeypatch, script, listens):
    from kravein.platform import stt

    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(stt, "translate_available", lambda: True)
    monkeypatch.setattr(stt, "translate_ready", lambda: True)
    monkeypatch.setattr(stt, "preload_translate", lambda: None)
    monkeypatch.setattr(stt, "translate_to_english", lambda pcm: "Hey, are you listening to me?")
    backend.script = script
    net = NetworkAudio()
    net.push_input(b"\x01\x00" * 16000)
    s = LiveSession(source=net, sink=net, observer=lambda k, t, d: events.append((k, t, d)))
    t, _ = _run_in_thread(s)
    assert _wait(lambda: len([e for e in events if e[0] == "listening"]) > listens, 5)
    s.stop()
    t.join(3)
    return events


def test_deferred_heard_keeps_the_partials_turn_id(backend, monkeypatch):
    events = _run3(backend, monkeypatch, _speak_script(["Here are you", " listening"]), 1)
    partials = [d for k, _, d in events if k == "heard_partial"]
    heard = [d for k, _, d in events if k == "heard"]
    assert partials and heard
    assert {d["turn"] for d in partials} == {heard[0]["turn"]}
    assert heard[0]["final"] is True and heard[0]["replace"] is True
    # it still arrives before `listening`, which closes the bubble
    kinds = [k for k, _, _ in events]
    assert kinds.index("heard") < kinds.index("listening", kinds.index("heard"))


def test_two_quick_turns_do_not_cross_ids(backend, monkeypatch):
    import asyncio
    from test_live_turns import _resp, _sc, _text

    async def script(_s):
        for word in ("first turn", "second turn"):
            yield _resp(server_content=_sc(input_transcription=_text(word)))
            await asyncio.sleep(0.3)
            yield _resp(server_content=_sc(turn_complete=True))

    events = _run3(backend, monkeypatch, script, 2)
    heard = [d for k, _, d in events if k == "heard"]
    assert len(heard) == 2 and heard[0]["turn"] != heard[1]["turn"]
    partial_turns = [d["turn"] for k, _, d in events if k == "heard_partial"]
    assert partial_turns == [heard[0]["turn"], heard[1]["turn"]]
