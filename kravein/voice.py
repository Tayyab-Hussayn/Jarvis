"""Voice conversation.

Two modes, because the original's mechanism needs a key the user may not have:

``local`` (default)
    mic → faster-whisper → the normal agent loop → edge-tts.
    Works with any LLM backend, including the Cline CLI, and needs no key.

``live``
    The original Kravein pipeline: a single Gemini Live websocket doing
    speech-in, reasoning and speech-out (``response_modalities=["AUDIO"]``,
    voice ``Charon``). Restores the exact original behaviour, but requires a
    Gemini key with native-audio access.

Both speak through the same edge-tts voice the Windows build used.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import audit, config
from .agent import Agent, _normalize_observer, make_agent
from .platform import mic, stt, tts
from .safety import Confirmer

# (kind, text) — kind is: listening, heard, thinking, speaking, error, info
VoiceObserver = Callable[..., None]   # (kind, text) or (kind, text, data)

# Things the user can say to end the session.
STOP_PHRASES = (
    "stop listening", "exit voice", "quit voice", "stop voice",
    "voice off", "goodbye kravein", "that's all", "thats all",
)

# Said before a destructive action when there is no terminal to prompt in.
CONFIRM_ASK = "That is a destructive action. Say yes to continue, or no to cancel."
YES_WORDS = ("yes", "yeah", "yep", "go ahead", "do it", "confirm", "affirmative", "sure")
NO_WORDS = ("no", "nope", "cancel", "stop", "don't", "dont", "negative")


@dataclass
class VoiceConfig:
    """Tunables for a voice session."""

    speak_replies: bool = True
    silence_seconds: float = 1.2
    max_utterance_seconds: float = 30.0
    start_timeout: float = 300.0
    wake_word: str = ""
    confirm_by_voice: bool = True
    max_reply_chars: int = 700
    # 0 means calibrate against the room; set it to override a bad auto value.
    silence_threshold: float = 0.0

    @classmethod
    def from_settings(cls) -> "VoiceConfig":
        return cls(
            speak_replies=bool(config.get("voice.speak_replies", True)),
            silence_seconds=float(config.get("voice.silence_seconds", 1.2)),
            max_utterance_seconds=float(config.get("voice.max_utterance_seconds", 30.0)),
            start_timeout=float(config.get("voice.start_timeout", 300.0)),
            wake_word=str(config.get("voice.wake_word", "") or ""),
            confirm_by_voice=bool(config.get("voice.confirm_by_voice", True)),
            max_reply_chars=int(config.get("voice.max_reply_chars", 700)),
            silence_threshold=float(config.get("voice.silence_threshold", 0.0) or 0.0),
        )


def readiness() -> tuple[bool, list[str]]:
    """Can we run a voice session? Returns (ok, problems)."""
    problems: list[str] = []

    if not mic.backend_name():
        problems.append(mic.status())
    if not stt.backend_name():
        problems.append(stt.status())
    if not tts.available():
        problems.append("No text-to-speech available. Install:  sudo pacman -S mpv")

    return (not problems), problems


def _mode_line() -> str:
    """The configured preference *and* what it currently resolves to.

    Printing the setting alone was misleading once "auto" existed — it says
    what you asked for, not what would actually start.
    """
    preference = str(config.get("voice.mode", "auto") or "auto")
    resolved, problem = resolve_mode()
    if not resolved:
        return f"{preference} — cannot start: {problem.splitlines()[0]}"
    return preference if resolved == preference else f"{preference} → {resolved}"


def status() -> str:
    """Human-readable voice readiness report."""
    lines = [
        f"Mode      : {_mode_line()}",
        f"Microphone: {mic.status()}",
        f"Recognition: {stt.status()}",
        f"Speech    : {'ready' if tts.available() else 'unavailable'} "
        f"(voice {config.get('voice.tts_voice', 'en-US-GuyNeural')})",
    ]
    ok, problems = readiness()
    if ok:
        lines.append("Voice mode is ready.")
    else:
        lines.append("Not ready:")
        lines += [f"  - {p}" for p in problems]
    return "\n".join(lines)


def self_test(observer: VoiceObserver | None = None) -> str:
    """Exercise the whole chain once and report each stage on its own.

    Microphone, recognition and speech output are checked independently: a
    machine with no microphone used to stop at the first stage, so a broken TTS
    (edge-tts 403) stayed hidden behind it. Live capture is the only part that
    needs someone actually speaking, and it runs only when mic and recognition
    both came up.
    """
    def say(kind: str, text: str) -> None:
        if observer:
            observer(kind, text)

    lines: list[str] = []
    verdict: dict[str, str] = {}

    # 1. Input device health.
    mic_ok = False
    if not mic.backend_name():
        lines.append(f"--- microphone ---\nUnavailable: {mic.status()}")
        verdict["microphone"] = "FAILED"
    else:
        say("info", "checking the microphone (stay quiet for 3 seconds)…")
        try:
            report = mic.health(3.0)
            lines.append("--- microphone ---")
            lines.append(report.report())
            mic_ok = True
            verdict["microphone"] = "ok"
        except mic.MicError as exc:
            lines.append(f"--- microphone ---\nMicrophone check failed: {exc}")
            verdict["microphone"] = "FAILED"

    # 2. Speech recognition model.
    stt_ok = False
    if not stt.backend_name():
        lines.append(f"\n--- recognition ---\nUnavailable: {stt.status()}")
        verdict["recognition"] = "FAILED"
    else:
        say("info", "loading the speech model…")
        try:
            stt.preload()
            lines.append(f"\n--- recognition ---\n{stt.status()}")
            stt_ok = True
            verdict["recognition"] = "ok"
        except stt.STTError as exc:
            lines.append(f"\n--- recognition ---\nRecognition FAILED: {exc}")
            verdict["recognition"] = "FAILED"

    # 3. Speech output - always reached, whatever happened above.
    lines.append("\n--- speech output ---")
    if not tts.available():
        lines.append("No text-to-speech available. Install:  sudo pacman -S mpv")
        verdict["speech output"] = "FAILED"
    else:
        say("info", "testing speech output…")
        prompt = "Say a short sentence now." if (mic_ok and stt_ok) else "Voice test."
        spoken = tts.speak(prompt, blocking=True)
        lines.append(spoken)
        problem = tts_problem(spoken)
        if problem:
            lines.append(f"Speech output problem: {problem}")
            lines.append(
                "Fix: upgrade edge-tts (pip install -U 'edge-tts>=7.3.1'; 7.2.x gets "
                "HTTP 403 from Microsoft) or install an offline voice "
                "(sudo pacman -S espeak-ng)."
            )
            verdict["speech output"] = "FAILED"
        else:
            verdict["speech output"] = "ok"

    # 4. Real capture and transcription, only when both inputs are usable.
    if mic_ok and stt_ok:
        lines.append(_live_capture_stage(say, verdict))
    else:
        lines.append("\n--- live capture ---\nSkipped: needs a working microphone and recognition.")
        verdict["live capture"] = "skipped"

    lines.append("\n--- summary ---")
    lines += [f"{name:14}: {state}" for name, state in verdict.items()]
    if all(v == "ok" for v in verdict.values()):
        lines.append("All four stages worked. Voice mode is good to go.")
    return "\n".join(lines)


def _live_capture_stage(say, verdict: dict[str, str]) -> str:
    """Record one utterance, transcribe it. Returns its report section."""
    verdict["live capture"] = "FAILED"
    out = ["\n--- live capture ---"]
    say("info", "say a short sentence now…")
    result = mic.listen(
        silence_threshold=float(config.get("voice.silence_threshold", 0.0) or 0.0),
        silence_seconds=1.2,
        max_seconds=15.0,
        start_timeout=20.0,
    )
    if not result.ok:
        out.append(f"Captured nothing ({result.reason}).")
        out.append(
            "If you did speak, the threshold is too high for this input. Set it "
            'by hand:  {"voice": {"silence_threshold": 0.35}}'
        )
        return "\n".join(out)

    out.append(f"Captured {result.seconds:.1f}s of audio.")
    try:
        heard = stt.transcribe(result.path)
    except stt.STTError as exc:
        out.append(f"Transcription FAILED: {exc}")
        return "\n".join(out)
    finally:
        try:
            result.path.unlink(missing_ok=True)
        except OSError:
            pass

    out.append(f"Transcribed: {heard!r}")
    if not heard.strip():
        out.append("Audio was captured but no words were recognised.")
        return "\n".join(out)
    say("heard", heard)
    verdict["live capture"] = "ok"
    return "\n".join(out)


_TTS_FAILURES = ("Text-to-speech is unavailable", "No audio player", "Could not play")


def tts_problem(result: str) -> str:
    """Return a human-readable problem if a ``tts.speak`` result means no sound.

    ``tts.speak`` reports failure as a plain string, and edge-tts 7.2.x gets a
    403 from Microsoft's endpoint (seen 2026-10-25, fixed by edge-tts>=7.3.1),
    so callers that ignored the result went mute without any sign of it. An
    espeak fallback is audible but is still worth flagging: edge-tts is down.
    """
    result = result or ""
    if result.startswith(_TTS_FAILURES):
        return result
    if "fallback" in result:
        return f"edge-tts failed, used the offline voice ({result})"
    return ""


def _shorten_for_speech(text: str, limit: int) -> str:
    """Trim a long reply so the assistant does not monologue."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text

    # Cut at a sentence boundary if there is one nearby.
    window = text[:limit]
    for stop in (". ", "! ", "? "):
        index = window.rfind(stop)
        if index > limit * 0.5:
            return window[: index + 1]
    return window.rstrip() + "…"


class VoiceSession:
    """One hands-free conversation."""

    def __init__(
        self,
        *,
        agent: Agent | None = None,
        settings: VoiceConfig | None = None,
        observer: VoiceObserver | None = None,
        confirmer: Confirmer | None = None,
    ):
        self.settings = settings or VoiceConfig.from_settings()
        self.observer = _normalize_observer(observer)
        self._turn_seq = 0
        self._stop = threading.Event()
        self._external_confirmer = confirmer

        self.agent = agent or make_agent(
            source="voice",
            confirmer=self._confirm,
        )
        # Reuse an injected agent but make it confirm through voice.
        if agent is not None and confirmer is None:
            self.agent.confirmer = self._confirm

        self.turns = 0

    # -- output ----------------------------------------------------------

    def _emit(self, kind: str, text: str, data: dict | None = None) -> None:
        if self.observer:
            try:
                self.observer(kind, text, data or {})
            except Exception:
                pass

    def say(self, text: str, *, blocking: bool = True) -> None:
        """Speak, if speaking is enabled."""
        if not text:
            return
        self._emit("speaking", text)
        if self.settings.speak_replies:
            problem = tts_problem(tts.speak(text, blocking=blocking))
            if problem:
                audit.record("voice.tts_problem", problem=problem)
                self._emit("error", problem)

    # -- input -----------------------------------------------------------

    def listen_once(self, *, prompt_state: bool = True) -> str:
        """Capture one utterance and return its text ('' if nothing usable)."""
        if prompt_state:
            self._emit("listening", "")

        result = mic.listen(
            silence_threshold=self.settings.silence_threshold,
            silence_seconds=self.settings.silence_seconds,
            max_seconds=self.settings.max_utterance_seconds,
            start_timeout=self.settings.start_timeout,
            stop_event=self._stop,
        )

        if not result.ok:
            # A capture that reports 'stopped' means the session is ending;
            # treat it as a stop rather than looping on an empty microphone.
            if result.reason == "stopped":
                self._stop.set()
            elif result.reason:
                self._emit("info", result.reason)
            return ""

        try:
            text = stt.transcribe(result.path)
        except stt.STTError as exc:
            self._emit("error", str(exc))
            return ""
        finally:
            try:
                result.path.unlink(missing_ok=True)
            except OSError:
                pass

        text = (text or "").strip()
        if text:
            # Same contract as Live: heard frames name their turn.
            self._turn_seq += 1
            self._emit("heard", text, {"turn": f"vt{self._turn_seq}", "final": True})
        return text

    # -- confirmation ----------------------------------------------------

    def _confirm(self, action: str, detail: str, danger) -> bool:
        """Ask for approval out loud, since there is no prompt in voice mode."""
        if self._external_confirmer is not None:
            return self._external_confirmer(action, detail, danger)

        if not self.settings.confirm_by_voice:
            self._emit("info", f"refused (voice confirmation disabled): {action}")
            return False

        self._emit("info", f"confirm: {action}")
        # Block until spoken, so the question is not talked over by the mic.
        self.say(f"{action}. {CONFIRM_ASK}", blocking=True)

        for _ in range(2):
            answer = self.listen_once().lower().strip()
            if not answer:
                continue
            if any(word in answer for word in YES_WORDS):
                audit.record("voice.confirmed", action=action, heard=answer)
                return True
            if any(word in answer for word in NO_WORDS):
                audit.record("voice.declined", action=action, heard=answer)
                return False
            self.say("Sorry, was that a yes or a no?", blocking=True)

        audit.record("voice.confirm_timeout", action=action)
        self.say("I did not catch that, so I am cancelling.", blocking=True)
        return False

    # -- loop ------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _wake_word_ok(self, text: str) -> tuple[bool, str]:
        """Apply the wake word, returning (should_handle, command_text)."""
        wake = self.settings.wake_word.strip().lower()
        if not wake:
            return True, text

        lowered = text.lower()
        if wake not in lowered:
            return False, ""

        # Strip the wake word so it does not confuse the model.
        index = lowered.find(wake)
        remainder = (text[:index] + text[index + len(wake):]).strip(" ,.!?")
        return True, remainder or text

    def run(self, greeting: str = "") -> int:
        """Run until stopped. Returns the number of exchanges handled."""
        ok, problems = readiness()
        if not ok:
            for problem in problems:
                self._emit("error", problem)
            return 0

        audit.record("voice.session_start", mode="local")

        # Load the recogniser before the first utterance, otherwise the user
        # waits several extra seconds on their opening sentence.
        try:
            self._emit("info", "loading speech model…")
            stt.preload()
        except stt.STTError as exc:
            self._emit("error", str(exc))
            return 0

        if greeting:
            self.say(greeting, blocking=True)

        # A microphone that never produces anything usable must not spin the
        # loop; give up after a run of empty captures.
        empty_streak = 0
        max_empty = int(config.get("voice.max_empty_captures", 8))

        try:
            while not self._stop.is_set():
                text = self.listen_once()
                if self._stop.is_set():
                    break

                if not text:
                    empty_streak += 1
                    if empty_streak >= max_empty:
                        self._emit(
                            "error",
                            f"Heard nothing usable {empty_streak} times in a row — "
                            "leaving voice mode. Check your microphone.",
                        )
                        break
                    continue

                empty_streak = 0

                if any(phrase in text.lower() for phrase in STOP_PHRASES):
                    self.say("Stopping voice mode.", blocking=True)
                    break

                handle, command = self._wake_word_ok(text)
                if not handle:
                    continue

                self._emit("thinking", command)
                try:
                    turn = self.agent.ask(command)
                except Exception as exc:
                    self._emit("error", str(exc))
                    self.say("Something went wrong handling that.", blocking=True)
                    continue

                self.turns += 1
                reply = turn.reply or turn.error or "I have nothing to say about that."
                self._emit("answer", reply)
                self.say(
                    _shorten_for_speech(reply, self.settings.max_reply_chars),
                    blocking=True,
                )
        except KeyboardInterrupt:
            self._emit("info", "interrupted")
        finally:
            tts.stop()
            audit.record("voice.session_stop", turns=self.turns)

        return self.turns


# ---------------------------------------------------------------------------
# Gemini Live mode (the original pipeline)
# ---------------------------------------------------------------------------

def resolve_mode(requested: str = "") -> tuple[str, str]:
    """Decide which pipeline to run. Returns ``(mode, problem)``.

    One resolver for every surface — the REPL, the CLI and the web all end up
    here, so `voice.mode` in settings genuinely governs the default instead of
    each caller having its own idea of one. It used to be read nowhere at all:
    the CLI hardcoded local and the browser guessed from a readiness snapshot,
    which meant a machine with a Gemini key but no local speech model had voice
    disabled in the UI while `kravein voice live` worked fine.

    An *explicit* request that cannot run is a refusal, not a downgrade —
    quietly starting a metered cloud pipeline because the free one was
    unavailable, or vice versa, is exactly the kind of surprise this should not
    produce. Only "auto" falls back.
    """
    choice = (requested or str(config.get("voice.mode", "auto") or "auto")).strip().lower()
    if choice not in {"auto", "live", "local"}:
        choice = "auto"

    live_ok, live_why = live_available()
    local_ok, local_problems = readiness()

    if choice == "live":
        return ("live", "") if live_ok else ("", live_why)
    if choice == "local":
        return ("local", "") if local_ok else ("", "; ".join(local_problems))

    if live_ok:
        return "live", ""
    if local_ok:
        return "local", ""
    return "", "; ".join([*local_problems, live_why.splitlines()[0] if live_why else ""]).strip("; ")


def live_available() -> tuple[bool, str]:
    """Can the original Gemini Live pipeline run?"""
    if not config.gemini_api_key():
        return False, (
            "Gemini Live needs a Gemini API key with native-audio access.\n"
            f"Add gemini_api_key to {config.CONFIG_DIR / 'api_keys.json'}, "
            "or use the default local voice mode."
        )
    import importlib.util

    if importlib.util.find_spec("sounddevice") is None:
        return False, "sounddevice is not installed. Run: .venv/bin/pip install sounddevice"
    if importlib.util.find_spec("google.genai") is None:
        return False, "google-genai is not installed."
    return True, ""


def run_live(observer: VoiceObserver | None = None) -> int:
    """Run the original Gemini Live voice session.

    Kept faithful to the Windows build: 16 kHz in, 24 kHz out, native audio
    responses in the ``Charon`` voice, with Kravein's skills passed as tool
    declarations so the model can act mid-conversation.
    """
    ok, why = live_available()
    if not ok:
        if observer:
            observer("error", why)
        return 0

    from .agent import make_voice_agent
    from .agents.history import principal_thread_id
    from .live import LiveSession

    # The CLI's own thread; the Orchestrator context only when the switch is on.
    agent = make_voice_agent(observer=None, thread_id=principal_thread_id("cli", "local"))
    session = LiveSession(observer=observer, agent=agent)
    return session.run()
