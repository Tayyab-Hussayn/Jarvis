"""Gemini Live voice session — the original Brahma Echo pipeline.

This is a direct port of ``BrahmaLive`` from the Windows ``main.py``: one
bidirectional websocket carrying microphone audio up and native speech down,
with the assistant's tools attached so the model can act mid-conversation.

Only the audio plumbing changed. The Windows build drove WASAPI through
sounddevice; here the same sounddevice calls land on PipeWire's ALSA
compatibility layer, which needs no code change beyond not assuming WASAPI
loopback exists.

Requires a Gemini key with native-audio access. Without one, use the default
local voice mode in :mod:`brahma.voice`.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import traceback
from typing import Any, Callable

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

Observer = Callable[[str, str], None]


class LiveSession:
    """A Gemini Live conversation with tool access."""

    def __init__(
        self,
        *,
        observer: Observer | None = None,
        confirmer: Confirmer | None = None,
        model: str = "",
    ):
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
            name=config.get("assistant_name", "Brahma"),
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

    def _config(self) -> Any:
        from google.genai import types

        from .llm import _sanitize_tools

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=self._system_prompt(),
            tools=[{"function_declarations": _sanitize_tools(registry.tool_declarations())}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=config.get("voice.live_voice", VOICE_NAME)
                    )
                )
            ),
        )

    # -- audio -----------------------------------------------------------

    async def _send_microphone(self, out_queue: asyncio.Queue) -> None:
        import sounddevice as sd

        loop = asyncio.get_running_loop()

        def callback(indata, _frames, _time, status) -> None:
            if status:
                return
            try:
                loop.call_soon_threadsafe(
                    out_queue.put_nowait,
                    {"data": bytes(indata), "mime_type": "audio/pcm"},
                )
            except (asyncio.QueueFull, RuntimeError):
                pass

        with sd.RawInputStream(
            samplerate=SEND_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
            callback=callback,
        ):
            self._emit("listening", "")
            # Await the stop event rather than polling: this wakes the instant
            # stop() is called instead of up to a tick later.
            if self._astop is not None:
                await self._astop.wait()

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
        import sounddevice as sd

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while not self._stop.is_set():
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

    # -- tools -----------------------------------------------------------

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
                        # The model answering means the user's utterance is
                        # over — a cleaner boundary than turn_complete, which
                        # would hold their words back until after the reply.
                        self._flush_heard()
                        if not self._said:
                            self._emit("speaking", "")
                        self._said.append(out.text)
                        self._emit("answer_partial", "".join(self._said).strip())

                    # Barge-in: the user cut the model off. Keep what was
                    # actually said rather than dropping it.
                    if getattr(server, "interrupted", False):
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
                        result = await asyncio.to_thread(
                            self._run_tool, call.name, dict(call.args or {})
                        )
                        self.turns += 1
                        responses.append(types.FunctionResponse(
                            id=getattr(call, "id", None),
                            name=call.name,
                            response={"result": result},
                        ))
                    if responses:
                        await self._session.send_tool_response(function_responses=responses)

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
