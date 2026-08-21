"""Microphone capture with voice activity detection.

The Windows build streamed raw audio straight into the Gemini Live websocket.
Here capture is decoupled from recognition: this module just produces a WAV
file containing one utterance, and :mod:`brahma.platform.stt` turns it into
text. That keeps the voice loop working with a local recogniser and no
streaming API.

Capture goes through PipeWire's own tools rather than PortAudio, so it needs
no extra Python dependency and picks up the user's default source.
"""

from __future__ import annotations

import array
import math
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from .. import audit
from . import shell

SAMPLE_RATE = 16000       # what every Whisper-family model expects
CHANNELS = 1
SAMPLE_WIDTH = 2          # 16-bit signed
CHUNK_MS = 30
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_FRAMES * SAMPLE_WIDTH * CHANNELS


@dataclass
class ListenResult:
    """One captured utterance."""

    path: Path | None
    seconds: float
    aborted: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None and not self.aborted


class MicError(RuntimeError):
    """No usable capture path."""


def backend_name() -> str:
    """Which recorder will be used, or '' if none."""
    for tool in ("pw-record", "parec", "ffmpeg", "arecord"):
        if shell.have(tool):
            return tool
    return ""


def status() -> str:
    tool = backend_name()
    if not tool:
        return (
            "No microphone capture tool found.\n"
            "Install one with:  sudo pacman -S pipewire-audio  (or alsa-utils)"
        )

    sources = shell.run(["pactl", "list", "short", "sources"], timeout=8)
    inputs = [
        line.split("\t")[1]
        for line in sources.stdout.splitlines()
        if "\t" in line and ".monitor" not in line
    ] if sources.ok else []

    detail = f"Microphone capture ready via {tool}."
    if inputs:
        detail += f" Input(s): {', '.join(inputs[:3])}"
    else:
        detail += " No input source detected — check your microphone."
    return detail


def _record_command(tool: str) -> list[str]:
    """Build a command that writes raw 16-bit mono PCM to stdout."""
    if tool == "pw-record":
        return [
            "pw-record", "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS),
            "--format", "s16", "-",
        ]
    if tool == "parec":
        return [
            "parec", "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS),
            "--format", "s16le",
        ]
    if tool == "arecord":
        return [
            "arecord", "-q", "-r", str(SAMPLE_RATE), "-c", str(CHANNELS),
            "-f", "S16_LE", "-t", "raw",
        ]
    return [
        "ffmpeg", "-loglevel", "quiet", "-f", "pulse", "-i", "default",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-f", "s16le", "-",
    ]


def _rms(chunk: bytes) -> float:
    """Root-mean-square level of a PCM chunk, 0.0 to ~1.0."""
    if len(chunk) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) // 2 * 2])
    if not samples:
        return 0.0
    total = sum(float(s) * float(s) for s in samples)
    return math.sqrt(total / len(samples)) / 32768.0


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)


# A speech threshold above this is unreachable in practice — normal speech on
# a sane input peaks around 0.1-0.4 RMS. If calibration wants more than this,
# the input itself is the problem and we clamp rather than go deaf.
MAX_AUTO_THRESHOLD = 0.30
MIN_AUTO_THRESHOLD = 0.012

# A noise floor above this means a hot or faulty input, not a noisy room.
NOISY_FLOOR = 0.05


@dataclass
class MicHealth:
    """What a short recording says about the input device."""

    floor: float           # median RMS with nobody speaking
    peak: float
    clipped_percent: float
    dc_offset: float
    threshold: float       # what VAD will use
    clamped: bool

    @property
    def healthy(self) -> bool:
        return self.floor < NOISY_FLOOR and self.clipped_percent < 1.0

    def report(self) -> str:
        lines = [
            f"Noise floor    : {self.floor:.4f} RMS"
            + ("  (HIGH)" if self.floor >= NOISY_FLOOR else "  (ok)"),
            f"Peak           : {self.peak:.4f}",
            f"Clipped samples: {self.clipped_percent:.1f}%"
            + ("  (HIGH)" if self.clipped_percent >= 1.0 else ""),
            f"DC offset      : {self.dc_offset:+.0f}"
            + ("  (should be near 0)" if abs(self.dc_offset) > 500 else ""),
            f"Speech threshold: {self.threshold:.4f}"
            + ("  (clamped — floor too high to scale from)" if self.clamped else ""),
        ]
        if not self.healthy:
            lines += [
                "",
                "This input is hot or noisy, so speech may not be detected reliably.",
                "Try, in order:",
                "  1. Lower the mic gain:  wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 15%",
                "  2. Check the right device is default:  wpctl status",
                "  3. Set a threshold by hand in settings.json:",
                '       {"voice": {"silence_threshold": 0.35}}',
            ]
        return "\n".join(lines)


def health(seconds: float = 3.0) -> MicHealth:
    """Record briefly with nobody speaking and characterise the input."""
    tool = backend_name()
    if not tool:
        raise MicError(status())

    proc = subprocess.Popen(
        _record_command(tool),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    raw = b""
    deadline = time.monotonic() + seconds
    try:
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            chunk = proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            raw += chunk
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not raw:
        raise MicError("the microphone produced no audio at all")

    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])

    levels = sorted(
        _rms(raw[i:i + CHUNK_BYTES]) for i in range(0, len(raw) - CHUNK_BYTES, CHUNK_BYTES)
    ) or [0.0]
    floor = levels[len(levels) // 2]
    peak = levels[-1]
    clipped = sum(1 for s in samples if abs(s) > 32000)
    dc = sum(samples) / len(samples) if samples else 0.0

    wanted = floor * 3.0
    threshold = min(max(wanted, MIN_AUTO_THRESHOLD), MAX_AUTO_THRESHOLD)

    return MicHealth(
        floor=floor, peak=peak,
        clipped_percent=100.0 * clipped / len(samples) if samples else 0.0,
        dc_offset=dc, threshold=threshold,
        clamped=wanted > MAX_AUTO_THRESHOLD,
    )


def calibrate(seconds: float = 1.0) -> float:
    """Measure the room's noise floor so the threshold suits the environment."""
    tool = backend_name()
    if not tool:
        raise MicError(status())

    proc = subprocess.Popen(
        _record_command(tool),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    levels: list[float] = []
    deadline = time.monotonic() + seconds
    try:
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            chunk = proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            levels.append(_rms(chunk))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not levels:
        return 0.01
    levels.sort()
    # Median is robust against a stray bump while calibrating.
    return levels[len(levels) // 2]


def listen(
    *,
    silence_threshold: float = 0.0,
    silence_seconds: float = 1.2,
    max_seconds: float = 30.0,
    start_timeout: float = 15.0,
    min_speech_seconds: float = 0.35,
    stop_event: threading.Event | None = None,
    on_state: "callable | None" = None,
) -> ListenResult:
    """Record one utterance, stopping when the speaker goes quiet.

    Args:
        silence_threshold: RMS below which audio counts as silence. 0 means
            calibrate against the room first.
        silence_seconds: How long a pause ends the utterance.
        max_seconds: Hard cap on a single utterance.
        start_timeout: Give up if nobody speaks within this long.
        min_speech_seconds: Ignore anything shorter (a cough, a key click).
        stop_event: Set this to abort recording early.
        on_state: Called with 'waiting', 'recording' or 'done'.
    """
    tool = backend_name()
    if not tool:
        raise MicError(status())

    if silence_threshold <= 0:
        floor = calibrate(0.6)
        # Speech normally sits well above the noise floor. The lower bound
        # stops a silent room producing a hair-trigger; the upper bound stops
        # a hot or faulty input producing a threshold speech cannot reach,
        # which would make the assistant permanently deaf.
        silence_threshold = min(
            max(floor * 3.0, MIN_AUTO_THRESHOLD), MAX_AUTO_THRESHOLD
        )
        if floor >= NOISY_FLOOR:
            audit.record(
                "mic.noisy_input", floor=round(floor, 4),
                threshold=round(silence_threshold, 4),
            )

    def notify(state: str) -> None:
        if on_state:
            try:
                on_state(state)
            except Exception:
                pass

    proc = subprocess.Popen(
        _record_command(tool),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )

    frames: list[bytes] = []
    speaking = False
    silence_run = 0.0
    speech_time = 0.0
    started = time.monotonic()
    chunk_seconds = CHUNK_MS / 1000.0

    notify("waiting")
    audit.record("mic.listen_start", threshold=round(silence_threshold, 4))

    try:
        assert proc.stdout is not None
        while True:
            if stop_event is not None and stop_event.is_set():
                return ListenResult(None, 0.0, aborted=True, reason="stopped")

            chunk = proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                break

            elapsed = time.monotonic() - started
            level = _rms(chunk)
            loud = level >= silence_threshold

            if not speaking:
                if loud:
                    speaking = True
                    notify("recording")
                    frames.append(chunk)
                    speech_time += chunk_seconds
                elif elapsed > start_timeout:
                    return ListenResult(
                        None, 0.0, aborted=True, reason="nobody spoke"
                    )
                else:
                    # Keep a little pre-roll so the first syllable survives.
                    frames.append(chunk)
                    if len(frames) > int(0.3 / chunk_seconds):
                        frames.pop(0)
                continue

            frames.append(chunk)
            if loud:
                silence_run = 0.0
                speech_time += chunk_seconds
            else:
                silence_run += chunk_seconds
                if silence_run >= silence_seconds:
                    break

            if elapsed >= max_seconds:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    notify("done")

    if not speaking or speech_time < min_speech_seconds:
        audit.record("mic.listen_empty", speech_seconds=round(speech_time, 2))
        return ListenResult(None, speech_time, aborted=True, reason="nothing said")

    pcm = b"".join(frames)
    duration = len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)

    path = Path(tempfile.gettempdir()) / f"brahma-utterance-{int(time.time()*1000)}.wav"
    _write_wav(path, pcm)

    audit.record("mic.listen_ok", seconds=round(duration, 2), path=str(path))
    return ListenResult(path, duration)
