"""Text to speech.

Ports the original's Edge-TTS voice. Synthesis was already cross-platform;
only playback was Windows-bound — it drove ``winmm.mciSendStringW``. Here the
MP3 goes to ``mpv``/``ffplay``/``paplay`` instead.

Falls back to ``espeak-ng`` when there is no network for Edge TTS, so the
assistant can still speak offline.
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from .. import audit, config
from . import shell

_lock = threading.Lock()
_current: subprocess.Popen | None = None
_current_file: Path | None = None


def available() -> bool:
    return bool(_player()) or shell.have("espeak-ng") or shell.have("espeak")


def _player() -> list[str] | None:
    """Pick a command that can play an MP3 quietly and then exit.

    ``--no-config`` matters: a user's mpv.conf commonly sets ``keep-open=yes``,
    which makes mpv linger after playback and would hang every spoken reply.
    The remaining flags are set explicitly so behaviour never depends on the
    user's own player settings.
    """
    if shell.have("mpv"):
        return [
            "mpv", "--no-config", "--no-terminal", "--no-video",
            "--really-quiet", "--keep-open=no", "--idle=no",
        ]
    if shell.have("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
    if shell.have("paplay"):
        return ["paplay"]
    if shell.have("pw-play"):
        return ["pw-play"]
    return None


def _synthesize(text: str, voice: str) -> Path | None:
    """Render text to an MP3 with Edge TTS."""
    try:
        import edge_tts
    except ImportError:
        return None

    path = Path(tempfile.gettempdir()) / f"brahma-tts-{uuid.uuid4().hex}.mp3"
    try:
        communicate = edge_tts.Communicate(text, voice=voice)
        communicate.save_sync(str(path))
    except Exception as exc:
        audit.record("tts.synth_failed", error=str(exc))
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    if not path.exists() or path.stat().st_size == 0:
        return None
    return path


def _espeak(text: str, blocking: bool) -> bool:
    """Offline fallback."""
    binary = shell.first_available("espeak-ng", "espeak")
    if not binary:
        return False
    args = [binary, "-s", "165", "--", text]
    if blocking:
        shell.run(args, timeout=120)
    else:
        shell.spawn(args, detach=False)
    return True


def stop() -> str:
    """Stop any speech currently playing."""
    global _current, _current_file
    with _lock:
        proc, path = _current, _current_file
        _current, _current_file = None, None

    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
            except OSError:
                pass

    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return "Speech stopped."


def speak(text: str, *, voice: str = "", blocking: bool = False) -> str:
    """Speak a line of text.

    Args:
        text: What to say.
        voice: Edge TTS voice id; defaults to the configured one.
        blocking: Wait for playback to finish.
    """
    text = (text or "").strip()
    if not text:
        return "Nothing to say."

    voice = voice or config.get("voice.tts_voice", "en-US-GuyNeural")

    # Only one utterance at a time, matching the original's behaviour.
    stop()

    path = _synthesize(text, voice)
    if path is None:
        if _espeak(text, blocking):
            return "Spoke (espeak fallback)."
        return "Text-to-speech is unavailable (no edge-tts and no espeak-ng)."

    player = _player()
    if player is None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if _espeak(text, blocking):
            return "Spoke (espeak fallback; no audio player for MP3)."
        return "No audio player found. Install mpv:  sudo pacman -S mpv"

    audit.record("tts.speak", chars=len(text), voice=voice)

    try:
        proc = subprocess.Popen(
            [*player, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return f"Could not play speech: {exc}"

    if blocking:
        try:
            proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return "Spoke."

    with _lock:
        globals()["_current"] = proc
        globals()["_current_file"] = path

    # Clean the temp file up once playback ends, without blocking the caller.
    def _reap() -> None:
        try:
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    threading.Thread(target=_reap, name="brahma-tts-reap", daemon=True).start()
    return "Speaking."


def list_voices(filter_text: str = "en-") -> str:
    """List Edge TTS voices."""
    try:
        import asyncio

        import edge_tts
    except ImportError:
        return "edge-tts is not installed."

    try:
        voices = asyncio.run(edge_tts.list_voices())
    except Exception as exc:
        return f"Could not list voices: {exc}"

    names = [
        v["ShortName"] for v in voices
        if filter_text.lower() in v["ShortName"].lower()
    ]
    if not names:
        return f"No voices matched '{filter_text}'."
    return f"{len(names)} voice(s):\n" + "\n".join(f"  {n}" for n in sorted(names))
