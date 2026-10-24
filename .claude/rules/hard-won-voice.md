---
paths:
  - "kravein/voice.py"
  - "kravein/live.py"
  - "kravein/platform/mic.py"
  - "kravein/platform/stt.py"
  - "kravein/platform/tts.py"
  - "kravein/platform/audio.py"
---

# Hard-won knowledge: Voice

Moved verbatim from `CLAUDE.md`. Each paragraph is a bug that already happened; do not rediscover it.

**TTS runs mpv with `--no-config`.** A user `mpv.conf` with `keep-open=yes` hangs playback forever otherwise.

**A tool call must never hold a voice session open indefinitely.** The model keeps its turn open
until every call it made returns a `FunctionResponse`; one that never comes leaves the session
listening but unable to answer, recoverable only by stopping it by hand. `live.py` bounds each
call at `TOOL_TIMEOUT` and always returns *something*, and records
`voice.live_tool_start`/`_end`/`_timeout` so a real stall leaves evidence.

**Gemini Live transcription auto-detects the language, and gets it wrong.** English speech came
back rendered in Malayalam and Hindi script. The audio itself reached the model intact — replies
were correct while the transcript was gibberish, which is a confusing way to fail. Pass
`AudioTranscriptionConfig(language_codes=[...])`; `language_auto` is a nested config object, not a
flag, so supplying codes is what disables detection. **That is not enough for Hindustani-accented
speech (2026-10-24, #100):** with `en-US` pinned, the owner's English still came back in Devanagari,
and letter-by-letter transliteration turned it into junk ("he opan flaish laait"). The display/log
transcript now comes from local faster-whisper `task="translate"` on the user's own turn audio
(#101); Gemini's text is only a fallback with foreign script stripped. Never transliterate.

**Live input transcription lags the audio.** Deltas keep arriving *after* the model has begun
answering, so flushing the user's words on the first output chunk truncates them — "running in
for" instead of "what's running in workspace four". Flush at `turn_complete`, and stream the
partial into a line that is rewritten in place, or the finished sentence lands underneath the
reply it prompted.

**One blip made the microphone useless.** A single 30ms chunk crossing the VAD threshold — a fan
surge, a key click — set `speaking`, and 1.2s of quiet then closed the "utterance". The 30ms was
below `min_speech_seconds` so the whole listen was discarded as **"nothing said"**, and
`start_timeout` was never consulted. Every listen died in ~1.3s, so local voice gave up with
"heard nothing usable 8 times in a row" in about five seconds while `pw-record` was capturing
perfectly. A false start now resets to waiting instead of ending the listen. Related: EOF on the
capture pipe is reported as *the recorder stopped*, not as silence — conflating a dead recorder
with a quiet room sends you debugging the wrong end.

**`voice.mode` used to be read by nothing.** It was displayed in `kravein voice status` and
consumed nowhere: the CLI hardcoded local, and the browser guessed a pipeline from a readiness
snapshot and sent it as a decision. That snapshot only covered the *local* halves, so a machine
with a Gemini key but no local speech model had voice disabled in the UI while `kravein voice live`
worked fine. `voice.resolve_mode()` is now the single resolver for every surface; the browser
sends a preference and the server answers with the pipeline it actually started. An explicit
`live` or `local` that cannot run is refused rather than downgraded — silently switching to the
metered pipeline, or away from it, is not a decision to make on someone's behalf.

**A tool that fails is not a dead voice session.** `live.py` emits tool timeouts and session
failures through the same `error` phase, so treating every one as fatal put the whole toggle into
an error state because one tool took too long. It is a turn error when a turn is open, a session
error only when there is none.
