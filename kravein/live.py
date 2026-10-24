"""Gemini Live voice session — the original Kravein pipeline.

This is a direct port of ``KraveinLive`` from the Windows ``main.py``: one
bidirectional websocket carrying microphone audio up and native speech down,
with the assistant's tools attached so the model can act mid-conversation.

Only the audio plumbing changed. The Windows build drove WASAPI through
sounddevice; here the same sounddevice calls land on PipeWire's ALSA
compatibility layer, which needs no code change beyond not assuming WASAPI
loopback exists.

Requires a Gemini key with native-audio access. Without one, use the default
local voice mode in :mod:`kravein.voice`.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import queue
import threading
import time
import traceback
from typing import Any, Callable, Protocol

from . import audit, config, registry
from .agent import SYSTEM_PROMPT
from .platform import detect
from .safety import Confirmer, confirmer_scope

# Matches the original's constants exactly.
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"

# The original's model is a dated preview, so keep replacements ready. All of
# these advertise bidiGenerateContent (checked against the live models list).
LIVE_FALLBACKS = [
    LIVE_MODEL,
    "models/gemini-2.5-flash-native-audio-latest",
    "models/gemini-3.1-flash-live-preview",
]
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_SIZE = 1024
VOICE_NAME = "Charon"

# Recognition language. Empty means "let the model guess", which mis-detected
# English badly enough to render it in Malayalam script.
DEFAULT_LANGUAGE = "en-US"

# A tool call blocks the model's turn. Without a ceiling the whole session can
# sit silent with no way to tell a slow command from a dead one, and no way out
# except tearing the session down by hand.
TOOL_TIMEOUT = 90.0

Observer = Callable[[str, str], None]


class Control(enum.Enum):
    """Non-audio items :meth:`NetworkAudio.pull_output` can return."""

    CLEAR = "clear"    # barge-in: the client must drop audio it has buffered
    CLOSED = "closed"  # the session ended; nothing more will arrive


class AudioSource(Protocol):
    async def capture(
        self, put: Callable[[bytes], None], astop: asyncio.Event, on_ready: Callable[[], None]
    ) -> None:
        """Feed PCM16 mono 16 kHz chunks to ``put`` until ``astop`` is set.

        Returning early ends the whole session (that is how a network client
        disconnecting is noticed).
        """


class AudioSink(Protocol):
    async def play(self, audio_queue: asyncio.Queue, stop: threading.Event) -> None:
        """Play PCM16 mono 24 kHz chunks taken from ``audio_queue``."""

    def interrupt(self) -> None:
        """Barge-in: discard audio that was queued but not yet played."""


def _drain(q: asyncio.Queue | None) -> None:
    while q is not None:
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            return


class SoundDeviceSource:
    """The laptop microphone."""

    async def capture(self, put, astop, on_ready) -> None:
        import sounddevice as sd

        def callback(indata, _frames, _time, status) -> None:
            if not status:
                put(bytes(indata))

        with sd.RawInputStream(
            samplerate=SEND_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
            callback=callback,
        ):
            on_ready()
            # Await the stop event rather than polling: this wakes the instant
            # stop() is called instead of up to a tick later.
            if astop is not None:
                await astop.wait()


class SoundDeviceSink:
    """The laptop speaker."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue | None = None

    def interrupt(self) -> None:
        _drain(self._queue)

    async def play(self, audio_queue, stop) -> None:
        import sounddevice as sd

        self._queue = audio_queue
        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while not stop.is_set():
                chunk = await audio_queue.get()
                if chunk is None:
                    continue
                # `stream.write` blocks until the buffer drains. Running it on
                # the event loop froze everything for the length of the reply —
                # which is exactly why Stop appeared to hang while the phase
                # was "Speaking". Off-thread, the loop stays responsive and the
                # stop watcher can still fire.
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
        finally:
            # abort(), not stop(): drop whatever is still queued in PortAudio
            # so the assistant goes quiet at once instead of finishing the
            # sentence after the user asked it to stop.
            with contextlib.suppress(Exception):
                stream.abort()
            with contextlib.suppress(Exception):
                stream.close()


class NetworkAudio:
    """Mic and speaker living on another machine (phone, browser).

    Pass the same object as ``source`` and ``sink`` of a :class:`LiveSession`.
    Everything here is thread-safe; the caller's transport threads never touch
    the session's event loop directly.

    * Input: :meth:`push_input` takes PCM16 mono little-endian at
      ``SEND_SAMPLE_RATE`` (16 kHz), any chunk size.
    * Output: :meth:`pull_output` returns PCM16 mono at ``RECEIVE_SAMPLE_RATE``
      (24 kHz), or :attr:`Control.CLEAR` (drop buffered playback — barge-in),
      or :attr:`Control.CLOSED` (session over), or ``None`` on timeout.
    * :meth:`close` is how the client disconnecting ends the session; the
      session ending closes it too, so a reader blocked in ``pull_output``
      always wakes.
    """

    MAX_INPUT_CHUNKS = 100   # several seconds; older audio is dropped
    MAX_OUTPUT_ITEMS = 400

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._inq: asyncio.Queue | None = None
        self._pending: list[bytes] = []   # pushed before the session is up
        self._carry = b""                 # odd trailing byte of a chunk
        self._out: queue.Queue = queue.Queue()
        self._audio_queue: asyncio.Queue | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    # -- caller side (any thread) ---------------------------------------

    def push_input(self, chunk: bytes) -> None:
        with self._lock:
            if self._closed or not chunk:
                return
            data = self._carry + bytes(chunk)
            # A split sample would be a click; keep the odd byte for next time.
            self._carry = data[-1:] if len(data) % 2 else b""
            data = data[: len(data) - len(self._carry)]
            if not data:
                return
            loop, inq = self._loop, self._inq
            if loop is None or inq is None:
                self._pending = (self._pending + [data])[-self.MAX_INPUT_CHUNKS:]
                return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._enqueue, inq, data)

    def pull_output(self, timeout: float | None = None) -> "bytes | Control | None":
        try:
            return self._out.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, inq = self._loop, self._inq
        # Audio already queued is still delivered first, then the reader sees
        # the end.
        self._out.put(Control.CLOSED)
        if loop is not None and inq is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._enqueue, inq, None)

    # -- session side (event loop) --------------------------------------

    def _enqueue(self, inq: asyncio.Queue, item: bytes | None) -> None:
        # Oldest audio goes first when full; the end marker must always fit.
        while inq.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                inq.get_nowait()
        inq.put_nowait(item)

    async def capture(self, put, astop, on_ready) -> None:
        inq: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_INPUT_CHUNKS)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._inq = inq
            pending, self._pending = self._pending, []
            closed = self._closed
        if closed:
            return
        for data in pending:
            self._enqueue(inq, data)
        on_ready()
        # Return on disconnect (None) as well as on stop: the session's
        # first-completed watcher turns either into a clean shutdown.
        stopper = asyncio.create_task(astop.wait()) if astop is not None else None
        try:
            while True:
                getter = asyncio.create_task(inq.get())
                waits = {getter} | ({stopper} if stopper else set())
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                if not getter.done():
                    getter.cancel()
                    return
                data = getter.result()
                if data is None:
                    return
                put(data)
        finally:
            if stopper:
                stopper.cancel()
            self.close()

    def _emit_out(self, item: "bytes | Control") -> None:
        if self._out.qsize() >= self.MAX_OUTPUT_ITEMS:
            # Reader is not keeping up (stalled socket). Drop the oldest audio
            # rather than growing without bound.
            with contextlib.suppress(queue.Empty):
                self._out.get_nowait()
        self._out.put(item)

    def interrupt(self) -> None:
        _drain(self._audio_queue)
        if self._closed:
            return  # the CLOSED marker must reach the reader
        # Drop what the reader has not collected yet, then tell it to flush
        # whatever it already handed to its own speaker.
        while True:
            try:
                self._out.get_nowait()
            except queue.Empty:
                break
        self._out.put(Control.CLEAR)

    async def play(self, audio_queue, stop) -> None:
        self._audio_queue = audio_queue
        try:
            while not stop.is_set() and not self._closed:
                chunk = await audio_queue.get()
                if chunk:
                    self._emit_out(chunk)
        finally:
            self.close()


class LiveSession:
    """A Gemini Live conversation with tool access."""

    def __init__(
        self,
        *,
        observer: Observer | None = None,
        confirmer: Confirmer | None = None,
        model: str = "",
        source: "AudioSource | None" = None,
        sink: "AudioSink | None" = None,
    ):
        # Default is the laptop's own mic and speaker; a phone or browser
        # passes a NetworkAudio for both (see its docstring).
        self._source = source or SoundDeviceSource()
        self._sink = sink or SoundDeviceSink()
        self.observer = observer
        self.confirmer = confirmer
        self.model = model or LIVE_MODEL
        self.turns = 0
        self._stop = threading.Event()
        self._session = None

        # Stop has to reach coroutines that are parked on a socket read or a
        # queue, and a threading.Event can only be polled between awaits — so
        # it is mirrored onto an asyncio.Event that a watcher task awaits. See
        # stop() and _main().
        self._loop: asyncio.AbstractEventLoop | None = None
        self._astop: asyncio.Event | None = None

        # Gemini streams transcription as deltas — one syllable or word per
        # message. Emitting each as it arrives produced a column of fragments
        # instead of a sentence, so they are accumulated and flushed at the
        # turn boundary.
        self._heard: list[str] = []
        self._said: list[str] = []

    # -- helpers ---------------------------------------------------------

    def _emit(self, kind: str, text: str) -> None:
        if self.observer:
            try:
                self.observer(kind, text)
            except Exception:
                pass

    def stop(self) -> None:
        """Ask the session to end. Safe to call from any thread."""
        self._stop.set()
        loop, event = self._loop, self._astop
        if loop is not None and event is not None and not loop.is_closed():
            # The event belongs to the session's loop; setting it from this
            # thread directly would not wake anything waiting on it.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(event.set)

    def _flush_heard(self) -> None:
        text = "".join(self._heard).strip()
        self._heard.clear()
        if text:
            self._emit("heard", text)

    def _flush_said(self) -> None:
        text = "".join(self._said).strip()
        self._said.clear()
        if text:
            # `answer` is the kind the local pipeline uses for a finished
            # reply. Live only ever emitted `speaking`, which carries phase
            # rather than content, so the assistant's own words never reached
            # the transcript at all.
            self._emit("answer", text)

    def _system_prompt(self) -> str:
        from .skills.memory import context_for_prompt

        prompt = SYSTEM_PROMPT.format(
            name=config.get("assistant_name", "Kravein"),
            environment=detect.environment().summary(),
            home=config.HOME,
        )
        memory = context_for_prompt()
        if memory:
            prompt += "\n\n" + memory

        # The original added a wake-word instruction; keep that behaviour.
        wake = config.get("voice.wake_words", []) or []
        if wake:
            prompt += (
                "\n\nWake-word mode: listen for "
                + ", ".join(f"'{w}'" for w in wake)
                + ". When you hear one, stay friendly and concise and wait for "
                "the user's next command."
            )
        prompt += (
            "\n\nYou are speaking out loud. Keep replies to a couple of short "
            "sentences unless the user asks for detail."
        )
        return prompt

    def _language(self) -> str:
        """BCP-47 code for speech recognition and the reply voice."""
        raw = str(config.get("voice.language", "") or "").strip()
        if not raw:
            return DEFAULT_LANGUAGE
        # Accept "en" or "en_US" as well as "en-US".
        code = raw.replace("_", "-")
        return code if "-" in code else f"{code}-{code.upper()}"

    def _config(self) -> Any:
        from google.genai import types

        from .llm import _sanitize_tools

        # Transcription defaults to auto-detect, and auto-detect was guessing
        # badly — English speech came back transcribed into Malayalam and
        # Hindi script. The audio still reached the model intact, so replies
        # were correct while the transcript was gibberish. Pinning the language
        # fixes the transcript; `voice.language` in settings overrides it.
        language = self._language()
        # `language_auto` is a nested config object, not a flag — supplying
        # language_codes is what turns auto-detection off.
        transcription = types.AudioTranscriptionConfig(language_codes=[language])

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=transcription,
            input_audio_transcription=transcription,
            system_instruction=self._system_prompt(),
            tools=[{"function_declarations": _sanitize_tools(registry.tool_declarations())}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                language_code=language,
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=config.get("voice.live_voice", VOICE_NAME)
                    )
                )
            ),
        )

    # -- audio -----------------------------------------------------------

    async def _send_microphone(self, out_queue: asyncio.Queue) -> None:
        loop = asyncio.get_running_loop()

        def put(data: bytes) -> None:
            # Called from whatever thread the source captures on. A full queue
            # drops the chunk: stale audio is worse than a gap.
            with contextlib.suppress(asyncio.QueueFull, RuntimeError):
                loop.call_soon_threadsafe(
                    out_queue.put_nowait, {"data": data, "mime_type": "audio/pcm"}
                )

        await self._source.capture(put, self._astop, lambda: self._emit("listening", ""))

    async def _drain_queue(self, out_queue: asyncio.Queue) -> None:
        while not self._stop.is_set():
            message = await out_queue.get()
            if self._session is None:
                continue
            try:
                await self._session.send_realtime_input(media=message)
            except Exception as exc:
                self._emit("error", f"send failed: {exc}")
                return

    async def _play_audio(self, audio_queue: asyncio.Queue) -> None:
        await self._sink.play(audio_queue, self._stop)

    # -- tools -----------------------------------------------------------

    async def _call_tool(self, name: str, args: dict) -> str:
        """Run one tool with a ceiling, always returning something to the model.

        Anything that raises or overruns comes back as an error string rather
        than as silence, so the conversation continues instead of stalling.
        """
        started = time.monotonic()
        audit.record("voice.live_tool_start", tool=name)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._run_tool, name, args), timeout=TOOL_TIMEOUT
            )
        except asyncio.TimeoutError:
            result = (
                f"{name} did not finish within {TOOL_TIMEOUT:.0f}s and was left "
                "running. Tell the user it timed out; do not retry it silently."
            )
            self._emit("error", f"{name} timed out after {TOOL_TIMEOUT:.0f}s")
            audit.record("voice.live_tool_timeout", tool=name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = f"{name} failed: {type(exc).__name__}: {exc}"
            self._emit("error", result)
            audit.record("voice.live_tool_failed", tool=name, error=str(exc))
        else:
            audit.record(
                "voice.live_tool_end", tool=name,
                seconds=round(time.monotonic() - started, 2),
            )
        self._emit("tool_result", result)
        return result

    def _run_tool(self, name: str, args: dict) -> str:
        self._emit("tool", f"{name}({', '.join(f'{k}={v!r}' for k, v in list(args.items())[:3])})")
        with confirmer_scope(self.confirmer):
            return registry.dispatch(name, args, source="voice-live")

    async def _receive(self, audio_queue: asyncio.Queue) -> None:
        from google.genai import types

        while not self._stop.is_set():
            turn = self._session.receive()
            async for response in turn:
                if self._stop.is_set():
                    return

                data = getattr(response, "data", None)
                if data:
                    audio_queue.put_nowait(data)

                server = getattr(response, "server_content", None)
                if server is not None:
                    inp = getattr(server, "input_transcription", None)
                    if inp is not None and getattr(inp, "text", ""):
                        self._heard.append(inp.text)
                        # Show it building, as one line that grows.
                        self._emit("heard_partial", "".join(self._heard).strip())

                    out = getattr(server, "output_transcription", None)
                    if out is not None and getattr(out, "text", ""):
                        # Deliberately NOT flushing the user's words here.
                        # Input transcription lags the audio, so deltas keep
                        # arriving after the model has begun answering —
                        # flushing on first output cut utterances in half
                        # ("running in for" instead of "what's running in
                        # workspace four"). The client shows the partial live,
                        # so nothing is hidden by waiting for turn_complete.
                        if not self._said:
                            self._emit("speaking", "")
                        self._said.append(out.text)
                        self._emit("answer_partial", "".join(self._said).strip())

                    # Barge-in: the user cut the model off. Keep what was
                    # actually said rather than dropping it.
                    if getattr(server, "interrupted", False):
                        # Audio already queued is the cut-off answer; playing
                        # it would talk over the user.
                        self._sink.interrupt()
                        self._flush_heard()
                        self._flush_said()

                    if getattr(server, "turn_complete", False) or getattr(
                        server, "generation_complete", False
                    ):
                        self._flush_heard()
                        self._flush_said()
                        self._emit("listening", "")

                tool_call = getattr(response, "tool_call", None)
                if tool_call is not None:
                    responses = []
                    for call in getattr(tool_call, "function_calls", []) or []:
                        # Every call must produce a FunctionResponse. A missing
                        # one is an indefinite wait by construction: the model
                        # holds the turn open for a result that never comes,
                        # and the session looks dead while still listening.
                        result = await self._call_tool(call.name, dict(call.args or {}))
                        self.turns += 1
                        responses.append(types.FunctionResponse(
                            id=getattr(call, "id", None),
                            name=call.name,
                            response={"result": result},
                        ))
                    if responses:
                        try:
                            await self._session.send_tool_response(
                                function_responses=responses
                            )
                        except Exception as exc:
                            # If the results cannot be delivered the turn will
                            # never complete. Say so rather than going quiet.
                            self._emit("error", f"could not return tool results: {exc}")
                            audit.record("voice.live_tool_send_failed", error=str(exc))
                            return

    # -- entry point -----------------------------------------------------

    async def _main(self) -> None:
        from google import genai

        client = genai.Client(
            api_key=config.gemini_api_key(),
            http_options={"api_version": "v1beta"},
        )

        audit.record("voice.live_start", model=self.model)
        self._emit("info", f"connecting to {self.model}")

        # Published before connecting so a stop() arriving during the handshake
        # is still seen.
        self._loop = asyncio.get_running_loop()
        self._astop = asyncio.Event()
        if self._stop.is_set():
            self._astop.set()

        async with client.aio.live.connect(model=self.model, config=self._config()) as session:
            self._session = session
            self._emit("info", "connected — start speaking")

            out_queue: asyncio.Queue = asyncio.Queue(maxsize=60)
            audio_queue: asyncio.Queue = asyncio.Queue()

            tasks = [
                asyncio.create_task(self._send_microphone(out_queue)),
                asyncio.create_task(self._drain_queue(out_queue)),
                asyncio.create_task(self._play_audio(audio_queue)),
                asyncio.create_task(self._receive(audio_queue)),
            ]
            # A watcher, so shutdown does not depend on any worker noticing the
            # flag. `gather` waits for *all* tasks; _receive parks on the
            # socket and _play_audio on its queue, so neither returned and the
            # cancelling `finally` was unreachable. Waiting for the first
            # completion instead means stop() ends the session immediately.
            watcher = asyncio.create_task(self._astop.wait())
            everything = [*tasks, watcher]
            try:
                await asyncio.wait(everything, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in everything:
                    task.cancel()
                await asyncio.gather(*everything, return_exceptions=True)
                self._flush_heard()
                self._flush_said()
                self._session = None

    def run(self) -> int:
        """Run the session until stopped. Returns the number of tool calls.

        A retired or overloaded audio model is retried against the fallback
        list before giving up, since the original's model is a dated preview.
        """
        candidates = (
            [self.model] if self.model not in LIVE_FALLBACKS
            else LIVE_FALLBACKS[LIVE_FALLBACKS.index(self.model):]
        )
        configured = config.get("voice.live_model", "")
        if configured:
            candidates = [configured]

        last: Exception | None = None
        for candidate in candidates:
            self.model = candidate
            try:
                asyncio.run(self._main())
                return self.turns
            except (KeyboardInterrupt, asyncio.CancelledError):
                self._emit("info", "interrupted")
                return self.turns
            except Exception as exc:
                if self._stop.is_set():
                    # Asked to stop mid-connect; trying the next model would
                    # reopen the microphone the user just closed.
                    return self.turns
                last = exc
                text = str(exc)
                audit.record(
                    "voice.live_error", model=candidate, error=text,
                    trace=traceback.format_exc()[:1200],
                )
                if any(t in text for t in ("404", "NOT_FOUND", "503", "UNAVAILABLE")):
                    self._emit("info", f"{candidate} unavailable, trying the next model")
                    continue
                self._emit("error", f"{type(exc).__name__}: {exc}")
                break

        if last is not None and self.turns == 0:
            self._emit("error", f"Gemini Live could not start: {last}")
        audit.record("voice.live_stop", tool_calls=self.turns)
        return self.turns
