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
