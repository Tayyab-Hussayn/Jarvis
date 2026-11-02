"""Tests: Whisper gets a decoded array, never a path (PyAV 19 `metadata_errors`, #185),
and a Live session warms the translate model when it starts."""

from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import audit  # noqa: E402
from kravein.platform import stt  # noqa: E402
from test_live_audio import _run_in_thread, _wait, backend  # noqa: E402,F401


def _wav(path, samples, rate=16000, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def test_pcm16_wav_becomes_float32_array(tmp_path):
    arr = stt._audio_input(_wav(tmp_path / "a.wav", [0, 16384, -32768]))
    assert arr.dtype == np.float32
    assert np.allclose(arr, [0.0, 0.5, -1.0])


def test_stereo_is_mixed_down_and_other_rates_resampled(tmp_path):
    stereo = stt._audio_input(_wav(tmp_path / "s.wav", [100, 300, 100, 300], channels=2))
    assert len(stereo) == 2 and np.allclose(stereo, [200 / 32768] * 2)
    slow = stt._audio_input(_wav(tmp_path / "r.wav", [0] * 8000, rate=8000))
    assert len(slow) == 16000


def test_a_file_that_is_not_pcm_wav_is_passed_as_a_path(tmp_path):
    p = tmp_path / "x.mp3"
    p.write_bytes(b"not a wav")
    assert stt._audio_input(p) == str(p)


class _FakeModel:
    def __init__(self):
        self.seen = None

    def transcribe(self, audio, **kw):
        self.seen = audio
        return [SimpleNamespace(text=" hello ")], SimpleNamespace(language="en")


def test_translate_and_transcribe_never_hand_whisper_a_path(tmp_path, monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(stt, "translate_available", lambda: True)
    monkeypatch.setattr(stt, "_load_translate_model", lambda: model)
    monkeypatch.setattr(stt, "_load_faster_whisper", lambda: model)
    monkeypatch.setattr(stt, "backend_name", lambda: "faster-whisper")
    wav = _wav(tmp_path / "a.wav", [1, 2, 3])
    assert stt.translate_to_english(wav) == "hello"
    assert isinstance(model.seen, np.ndarray)
    model.seen = None
    assert stt.transcribe(wav) == "hello"
    assert isinstance(model.seen, np.ndarray)


def test_live_session_never_warms_the_translate_model(backend, monkeypatch):
    """#192: Whisper stays out of Live entirely (no warm-up thread, no model load)."""
    import time

    from kravein.live import LiveSession, NetworkAudio

    called = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(stt, "translate_available", lambda: called.append("avail") or True)
    monkeypatch.setattr(stt, "preload_translate", lambda: called.append("preload"))
    backend.script = lambda _s: iter(())
    net = NetworkAudio()
    events = []
    s = LiveSession(source=net, sink=net, observer=lambda k, t: events.append(k))
    t, _ = _run_in_thread(s)
    assert _wait(lambda: "info" in events, 5)
    time.sleep(0.3)
    s.stop()
    t.join(3)
    assert called == []
