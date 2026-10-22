"""Tests: voice.

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


# Voice
# ---------------------------------------------------------------------------

def test_tts_player_cannot_be_broken_by_user_config():
    from kravein.platform import tts

    player = tts._player()
    if player and player[0] == "mpv":
        # A user's mpv.conf with keep-open=yes would otherwise hang playback.
        assert "--no-config" in player
        assert "--keep-open=no" in player


def test_mic_rms_distinguishes_silence_from_signal():
    import array
    import math

    from kravein.platform import mic

    silence = array.array("h", [0] * 800).tobytes()
    assert mic._rms(silence) < 0.001

    tone = array.array(
        "h", [int(12000 * math.sin(i / 6.0)) for i in range(800)]
    ).tobytes()
    assert mic._rms(tone) > 0.1


def test_mic_writes_a_readable_wav(tmp_path):
    import array
    import wave

    from kravein.platform import mic

    pcm = array.array("h", [100] * mic.SAMPLE_RATE).tobytes()
    path = tmp_path / "x.wav"
    mic._write_wav(path, pcm)

    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == mic.SAMPLE_RATE
        assert handle.getnchannels() == mic.CHANNELS
        assert handle.getsampwidth() == mic.SAMPLE_WIDTH


def test_mic_record_commands_are_16k_mono():
    from kravein.platform import mic

    for tool in ("pw-record", "parec", "arecord", "ffmpeg"):
        command = mic._record_command(tool)
        joined = " ".join(command)
        assert str(mic.SAMPLE_RATE) in joined, f"{tool} is not 16 kHz"
        assert command[0] == tool


def test_voice_shortens_long_replies():
    from kravein.voice import _shorten_for_speech

    long_text = "First sentence here. " + ("padding words " * 200)
    out = _shorten_for_speech(long_text, 120)
    assert len(out) <= 121
    # Prefers to stop at a sentence boundary.
    assert out.endswith((".", "…"))

    short = "Just this."
    assert _shorten_for_speech(short, 500) == short


def test_voice_wake_word_gating():
    from kravein.voice import VoiceConfig, VoiceSession

    session = VoiceSession(settings=VoiceConfig(wake_word="kravein"))

    handled, command = session._wake_word_ok("kravein what is the time")
    assert handled and "kravein" not in command.lower()

    handled, _ = session._wake_word_ok("what is the time")
    assert not handled


def test_voice_without_wake_word_passes_everything_through():
    from kravein.voice import VoiceConfig, VoiceSession

    session = VoiceSession(settings=VoiceConfig(wake_word=""))
    handled, command = session._wake_word_ok("turn the volume down")
    assert handled and command == "turn the volume down"


def test_voice_stop_phrases_are_recognised():
    from kravein import voice

    assert any(p in "ok stop listening now" for p in voice.STOP_PHRASES)
    assert not any(p in "what is my disk usage" for p in voice.STOP_PHRASES)


def test_voice_confirmation_accepts_spoken_yes(monkeypatch):
    from kravein.voice import VoiceSession
    from kravein.safety import Danger

    session = VoiceSession()
    session.settings.speak_replies = False
    monkeypatch.setattr(session, "listen_once", lambda **k: "yes go ahead")
    assert session._confirm("delete a file", "destructive", Danger.DESTRUCTIVE)


def test_voice_confirmation_accepts_spoken_no(monkeypatch):
    from kravein.voice import VoiceSession
    from kravein.safety import Danger

    session = VoiceSession()
    session.settings.speak_replies = False
    monkeypatch.setattr(session, "listen_once", lambda **k: "no cancel that")
    assert not session._confirm("delete a file", "destructive", Danger.DESTRUCTIVE)


def test_voice_confirmation_cancels_when_nothing_is_heard(monkeypatch):
    from kravein.voice import VoiceSession
    from kravein.safety import Danger

    session = VoiceSession()
    session.settings.speak_replies = False
    monkeypatch.setattr(session, "listen_once", lambda **k: "")
    # Silence must never be read as approval.
    assert not session._confirm("delete a file", "destructive", Danger.DESTRUCTIVE)


def test_voice_session_stops_on_repeated_empty_capture(monkeypatch):
    from kravein import voice
    from kravein.platform import mic, stt

    monkeypatch.setattr(stt, "preload", lambda: "stubbed")
    monkeypatch.setattr(
        mic, "listen",
        lambda **k: mic.ListenResult(None, 0.0, aborted=True, reason="nothing said"),
    )
    monkeypatch.setattr(voice, "readiness", lambda: (True, []))

    session = voice.VoiceSession()
    session.settings.speak_replies = False
    # Must terminate rather than spin forever on a dead microphone.
    assert session.run() == 0


def test_voice_readiness_reports_each_missing_piece(monkeypatch):
    from kravein import voice
    from kravein.platform import mic, stt, tts

    monkeypatch.setattr(mic, "backend_name", lambda: "")
    monkeypatch.setattr(stt, "backend_name", lambda: "")
    monkeypatch.setattr(tts, "available", lambda: False)

    ok, problems = voice.readiness()
    assert not ok
    assert len(problems) == 3


def test_live_mode_requires_a_gemini_key(monkeypatch):
    from kravein import config, voice

    monkeypatch.setattr(config, "gemini_api_key", lambda: "")
    ok, why = voice.live_available()
    assert not ok
    assert "Gemini" in why


def test_live_uses_the_original_audio_constants():
    from kravein import live

    # These must match the Windows build exactly or the stream desyncs.
    assert live.SEND_SAMPLE_RATE == 16000
    assert live.RECEIVE_SAMPLE_RATE == 24000
    assert live.VOICE_NAME == "Charon"
    assert "native-audio" in live.LIVE_MODEL


def test_tts_voice_matches_the_original():
    from kravein import config

    # The Windows build used en-US-GuyNeural; keep the assistant sounding same.
    assert config.get("voice.tts_voice") == "en-US-GuyNeural"


# ---------------------------------------------------------------------------


# Voice pipeline selection
# ---------------------------------------------------------------------------

def _stub_pipelines(monkeypatch, *, live: bool, local: bool):
    from kravein import voice

    monkeypatch.setattr(
        voice, "live_available", lambda: (live, "" if live else "no Gemini key"))
    monkeypatch.setattr(
        voice, "readiness", lambda: (local, [] if local else ["no speech recognition"]))


def test_resolve_mode_auto_prefers_live_and_falls_back(monkeypatch):
    from kravein import config, voice

    monkeypatch.setattr(config, "get", lambda key, default=None: "auto")

    _stub_pipelines(monkeypatch, live=True, local=True)
    assert voice.resolve_mode() == ("live", "")

    _stub_pipelines(monkeypatch, live=False, local=True)
    assert voice.resolve_mode() == ("local", "")

    _stub_pipelines(monkeypatch, live=False, local=False)
    mode, problem = voice.resolve_mode()
    assert mode == ""
    assert "speech recognition" in problem


def test_resolve_mode_refuses_rather_than_switching_pipelines(monkeypatch):
    """An explicit choice is a requirement, not a hint.

    Quietly starting the metered cloud pipeline because the free one was
    unavailable — or the reverse — is a surprise worth refusing over.
    """
    from kravein import config, voice

    monkeypatch.setattr(config, "get", lambda key, default=None: "auto")

    _stub_pipelines(monkeypatch, live=True, local=False)
    assert voice.resolve_mode("local")[0] == ""

    _stub_pipelines(monkeypatch, live=False, local=True)
    assert voice.resolve_mode("live")[0] == ""


def test_resolve_mode_honours_the_setting_when_nothing_is_requested(monkeypatch):
    """`voice.mode` used to be read by nothing at all."""
    from kravein import config, voice

    _stub_pipelines(monkeypatch, live=True, local=True)

    monkeypatch.setattr(config, "get", lambda key, default=None: "local")
    assert voice.resolve_mode() == ("local", "")

    monkeypatch.setattr(config, "get", lambda key, default=None: "live")
    assert voice.resolve_mode() == ("live", "")

    # An unrecognised value must not disable voice.
    monkeypatch.setattr(config, "get", lambda key, default=None: "nonsense")
    assert voice.resolve_mode()[0] in {"live", "local"}


# ---------------------------------------------------------------------------


# Microphone capture
# ---------------------------------------------------------------------------

class _ScriptedRecorder:
    """Stands in for pw-record, replaying a fixed sequence of PCM chunks."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.stdout = self

    def read(self, _size):
        return self._chunks.pop(0) if self._chunks else b""

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _pcm(level: int, chunks: int):
    """`chunks` chunks of a constant-amplitude square wave."""
    from kravein.platform import mic

    frame = int(level).to_bytes(2, "little", signed=True)
    return [frame * mic.CHUNK_FRAMES] * chunks


def test_a_blip_does_not_end_the_listen(monkeypatch):
    """One noisy chunk must not consume the whole listen.

    It used to: the blip set `speaking`, the following 1.2s of quiet closed
    the "utterance", and the 30ms of audio was thrown away as "nothing said" —
    before `start_timeout` was ever consulted. Every listen died in about 1.3
    seconds, so voice mode gave up with "heard nothing usable 8 times in a
    row" while the microphone was working perfectly.
    """
    from kravein.platform import mic

    loud, quiet = 12000, 5
    script = [
        *_pcm(quiet, 5),
        *_pcm(loud, 1),        # the blip
        *_pcm(quiet, 60),      # 1.8s of silence — long enough to have ended it
        *_pcm(loud, 40),       # then somebody actually speaks
        *_pcm(quiet, 60),
    ]
    monkeypatch.setattr(mic.subprocess, "Popen", lambda *a, **k: _ScriptedRecorder(script))
    monkeypatch.setattr(mic, "backend_name", lambda: "pw-record")
    monkeypatch.setattr(mic, "_write_wav", lambda path, pcm: path.write_bytes(pcm))

    result = mic.listen(
        silence_threshold=0.05, silence_seconds=1.2,
        max_seconds=30, start_timeout=30, min_speech_seconds=0.35,
    )

    assert result.path is not None, f"lost the utterance: {result.reason}"
    assert result.seconds >= 0.35
    if result.path:
        result.path.unlink(missing_ok=True)


def test_a_dead_recorder_is_not_reported_as_silence(monkeypatch):
    """EOF on the capture pipe is a failure, not a pause."""
    from kravein.platform import mic

    monkeypatch.setattr(
        mic.subprocess, "Popen",
        lambda *a, **k: _ScriptedRecorder(_pcm(5, 400)),
    )
    monkeypatch.setattr(mic, "backend_name", lambda: "pw-record")

    result = mic.listen(
        silence_threshold=0.05, max_seconds=30, start_timeout=1.0,
    )
    assert result.path is None
    # The scripted recorder runs out of audio rather than waiting on a clock,
    # which is exactly the case that must not be reported as "nothing said":
    # a recorder that stopped is a different problem from a quiet room, and
    # conflating them sends the user debugging the wrong thing.
    assert "recorder stopped" in result.reason


# ---------------------------------------------------------------------------
