"""#201: every spoken Live turn yields exactly one user line, for 2.5 and 3.1 event orders."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from kravein import audit  # noqa: E402
from kravein.live import LiveSession, NetworkAudio  # noqa: E402
from test_live_audio import _resp, _run_in_thread, _wait, backend  # noqa: E402,F401
from test_live_turns import _sc, _text  # noqa: E402


def _drive3(backend, monkeypatch, responses):
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)

    async def script(_s):
        for r in responses:
            yield r

    backend.script = script
    net = NetworkAudio()
    s = LiveSession(source=net, sink=net,
                    observer=lambda k, t, d=None: events.append((k, t, d or {})))
    t, _ = _run_in_thread(s)
    assert _wait(lambda: [e for e in events if e[0] == "listening"][1:] or False, 3)
    s.stop()
    t.join(3)
    return events


SHAPES = {
    # 2.5: partials during speech, answer, then both completion signals.
    "2.5": lambda: [
        _resp(server_content=_sc(input_transcription=_text("what is "))),
        _resp(server_content=_sc(input_transcription=_text("running"))),
        _resp(server_content=_sc(output_transcription=_text("Firefox."))),
        _resp(server_content=_sc(generation_complete=True)),
        _resp(server_content=_sc(turn_complete=True)),
    ],
    # 3.1: the transcript arrives only after the model answered, in one piece.
    "3.1-late": lambda: [
        _resp(server_content=_sc(output_transcription=_text("Firefox."))),
        _resp(server_content=_sc(generation_complete=True)),
        _resp(server_content=_sc(input_transcription=_text("what is running"))),
        _resp(server_content=_sc(turn_complete=True)),
    ],
    # 3.1: transcript after both completion signals.
    "3.1-after-complete": lambda: [
        _resp(server_content=_sc(output_transcription=_text("Firefox."))),
        _resp(server_content=_sc(generation_complete=True)),
        _resp(server_content=_sc(turn_complete=True)),
        _resp(server_content=_sc(input_transcription=_text("what is running"))),
    ],
    # 3.1: single final transcript before the answer, no partials.
    "3.1-final-only": lambda: [
        _resp(server_content=_sc(input_transcription=_text("what is running"))),
        _resp(server_content=_sc(output_transcription=_text("Firefox."))),
        _resp(server_content=_sc(turn_complete=True)),
    ],
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_one_user_line_per_spoken_turn(shape, backend, monkeypatch):
    events = _drive3(backend, monkeypatch, SHAPES[shape]())
    print(shape, events)
    finals = [(t, d) for k, t, d in events if k == "heard"]
    assert len(finals) == 1 and finals[0][0] == "what is running"
    assert finals[0][1].get("turn")
