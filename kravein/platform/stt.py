"""Speech to text.

The original streamed audio to Gemini Live, which needs a paid key and a
constant websocket. Since the default LLM backend here is a text-only CLI,
recognition runs locally instead:

  * ``faster-whisper``  — CTranslate2 Whisper, CPU-friendly, the default
  * ``whisper.cpp``     — if the user already has the binary
  * ``vosk``            — light, streaming-oriented
  * Gemini              — used only if a key happens to be configured

The model is loaded once and cached; the first run downloads weights.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .. import audit, config
from . import shell

DEFAULT_MODEL = "base.en"

# Whisper's translate task needs a multilingual model: the ".en" models only
# know English and cannot turn Urdu/Hindi speech into English text.
TRANSLATE_MODEL = "base"

_model = None
_model_name = ""
_tmodel = None
_tmodel_name = ""
_lock = threading.Lock()


class STTError(RuntimeError):
    """Recognition is unavailable or failed."""


def _have_faster_whisper() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


def _whisper_cpp_binary() -> str | None:
    return shell.first_available("whisper-cli", "whisper-cpp", "whisper")


def backend_name() -> str:
    """Which recogniser will be used, or '' if none."""
    configured = str(config.get("voice.stt_backend", "auto") or "auto").lower()

    if configured not in ("auto", ""):
        return configured

    if _have_faster_whisper():
        return "faster-whisper"
    if _whisper_cpp_binary():
        return "whisper.cpp"

    import importlib.util

    if importlib.util.find_spec("vosk") is not None:
        return "vosk"
    if config.gemini_api_key():
        return "gemini"
    return ""


def status() -> str:
    backend = backend_name()
    if not backend:
        return (
            "Speech recognition is not installed.\n"
            "Install it with:  .venv/bin/pip install faster-whisper"
        )

    detail = f"Speech recognition ready via {backend}"
    if backend == "faster-whisper":
        detail += f" (model: {config.get('voice.stt_model', DEFAULT_MODEL)})"
        if _model is None:
            detail += " — loads on first use"
    return detail + "."


def model_size() -> str:
    return str(config.get("voice.stt_model", DEFAULT_MODEL))


def preload() -> str:
    """Load the model now, so the first utterance is not slow."""
    backend = backend_name()
    if backend != "faster-whisper":
        return status()
    _load_faster_whisper()
    return f"Whisper model '{model_size()}' loaded."


def _load_faster_whisper():
    """Load and cache the CTranslate2 Whisper model."""
    global _model, _model_name

    wanted = model_size()
    with _lock:
        if _model is not None and _model_name == wanted:
            return _model

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTError(
                "faster-whisper is not installed. "
                "Run:  .venv/bin/pip install faster-whisper"
            ) from exc

        audit.record("stt.model_load", model=wanted)
        try:
            _model = WhisperModel(
                wanted,
                device="cpu",
                # int8 keeps this usable on a laptop CPU without a GPU.
                compute_type="int8",
                download_root=str(config.CACHE_DIR / "whisper"),
            )
        except Exception as exc:
            raise STTError(f"could not load Whisper model '{wanted}': {exc}") from exc

        _model_name = wanted
        return _model


def translate_model_size() -> str:
    return str(config.get("voice.translate_model", TRANSLATE_MODEL) or TRANSLATE_MODEL)


def translate_available() -> bool:
    """Whether :func:`translate_to_english` can run (faster-whisper only)."""
    return backend_name() == "faster-whisper"


def translate_ready() -> bool:
    """True once the translate model is in memory (no download or load pending)."""
    return _tmodel is not None and _tmodel_name == translate_model_size()


def _load_translate_model():
    """Load and cache the multilingual model, separate from the transcribe one."""
    global _tmodel, _tmodel_name

    wanted = translate_model_size()
    with _lock:
        if _tmodel is not None and _tmodel_name == wanted:
            return _tmodel
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTError("faster-whisper is not installed.") from exc

        audit.record("stt.model_load", model=wanted, task="translate")
        try:
            _tmodel = WhisperModel(
                wanted, device="cpu", compute_type="int8",
                download_root=str(config.CACHE_DIR / "whisper"),
            )
        except Exception as exc:
            raise STTError(f"could not load Whisper model '{wanted}': {exc}") from exc
        _tmodel_name = wanted
        return _tmodel


def preload_translate() -> None:
    """Load (and on first use download) the translate model ahead of need."""
    _load_translate_model()


def translate_to_english(path: str | Path) -> str:
    """Turn a WAV file of speech in any language into English text.

    The language is detected per clip. Whisper emits English for English
    speech too, so one call covers both of the owner's ways of speaking.
    """
    audio = Path(path)
    if not audio.exists():
        raise STTError(f"audio file not found: {audio}")
    if not translate_available():
        raise STTError("translation needs the faster-whisper backend")
    model = _load_translate_model()
    try:
        segments, info = model.transcribe(
            str(audio), task="translate", language=None, beam_size=1,
            vad_filter=True, condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as exc:
        raise STTError(f"translation failed: {exc}") from exc
    audit.record(
        "stt.translated", language=getattr(info, "language", ""), chars=len(text),
    )
    return text


def transcribe(path: str | Path, language: str = "") -> str:
    """Turn a WAV file into text."""
    audio = Path(path)
    if not audio.exists():
        raise STTError(f"audio file not found: {audio}")

    backend = backend_name()
    if not backend:
        raise STTError(status())

    language = language or str(config.get("voice.language", "") or "")

    if backend == "faster-whisper":
        return _transcribe_faster_whisper(audio, language)
    if backend == "whisper.cpp":
        return _transcribe_whisper_cpp(audio, language)
    if backend == "vosk":
        return _transcribe_vosk(audio)
    if backend == "gemini":
        return _transcribe_gemini(audio)
    raise STTError(f"unknown speech backend '{backend}'")


def _transcribe_faster_whisper(audio: Path, language: str) -> str:
    model = _load_faster_whisper()
    try:
        segments, info = model.transcribe(
            str(audio),
            language=language or None,
            beam_size=1,              # greedy: much faster, fine for commands
            vad_filter=True,          # drop leading/trailing silence
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:
        raise STTError(f"transcription failed: {exc}") from exc

    audit.record(
        "stt.transcribed",
        backend="faster-whisper",
        language=getattr(info, "language", language),
        chars=len(text),
    )
    return text


def _transcribe_whisper_cpp(audio: Path, language: str) -> str:
    binary = _whisper_cpp_binary()
    if not binary:
        raise STTError("whisper.cpp binary not found")

    model_path = str(config.get("voice.whisper_cpp_model", "") or "")
    args = [binary, "-f", str(audio), "--no-timestamps", "--output-txt"]
    if model_path:
        args += ["-m", model_path]
    if language:
        args += ["-l", language]

    result = shell.run(args, timeout=180)
    if not result.ok:
        raise STTError(f"whisper.cpp failed: {result.output}")

    # It writes <input>.txt next to the audio.
    produced = audio.with_suffix(audio.suffix + ".txt")
    if produced.exists():
        try:
            text = produced.read_text(encoding="utf-8", errors="replace").strip()
        finally:
            produced.unlink(missing_ok=True)
        return text

    return result.stdout.strip()


def _transcribe_vosk(audio: Path) -> str:
    try:
        import json
        import wave

        from vosk import KaldiRecognizer, Model
    except ImportError as exc:
        raise STTError("vosk is not installed") from exc

    model_path = str(config.get("voice.vosk_model", "") or "")
    if not model_path or not Path(model_path).exists():
        raise STTError(
            "vosk needs a model directory. Set voice.vosk_model in settings.json"
        )

    with wave.open(str(audio), "rb") as handle:
        recogniser = KaldiRecognizer(Model(model_path), handle.getframerate())
        recogniser.SetWords(False)
        pieces: list[str] = []
        while True:
            data = handle.readframes(4000)
            if not data:
                break
            if recogniser.AcceptWaveform(data):
                pieces.append(json.loads(recogniser.Result()).get("text", ""))
        pieces.append(json.loads(recogniser.FinalResult()).get("text", ""))

    return " ".join(p for p in pieces if p).strip()


def _transcribe_gemini(audio: Path) -> str:
    if not config.gemini_api_key():
        raise STTError("no Gemini API key configured")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise STTError("google-genai is not installed") from exc

    client = genai.Client(api_key=config.gemini_api_key())
    response = client.models.generate_content(
        model=config.get("llm.gemini_model", "gemini-2.5-flash"),
        contents=[
            types.Part.from_bytes(data=audio.read_bytes(), mime_type="audio/wav"),
            "Transcribe this audio exactly. Reply with only the transcription.",
        ],
    )
    return (getattr(response, "text", "") or "").strip()
