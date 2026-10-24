---
type: module
owner: engine
updated: 2026-10-24
---

# engine

## Code map
- `agent.py` loop, cancel, tool routing hooks · `llm.py`/`cline.py` providers, failover, breaker · `registry.py` `@skill` + `dispatch` · `safety.py` enforce, denial memory · `audit.py` · `config.py` · `toolselect.py`
- `jobs.py`/`routines.py` scheduler (tick via `python -m kravein.jobs tick`) · `voice.py`/`live.py` (audio source/sink: `SoundDevice*`, `NetworkAudio`) · `whatsapp_service.py` · `cli.py`/`daemon.py` · `service.py` (`kravein start|stop|restart|status|pair|phones|open|logs`: `systemctl --user` on `kravein-web.service` + the server's HTTP API with the owner token, TLS pinned to the on-disk cert)

## Verify
```bash
.venv/bin/python -m compileall -q kravein/
.venv/bin/python -m pytest tests/engine -q          # 248: safety, registry, llm, agent loop, cline, novarouter, voice, jobs, routines, whatsapp, audit, routing
```

## Public interfaces
- Confirmation policy (`safety.py`, #35): `confirmation_policy() -> {"mode": "ask"|"decline"|"auto", "seconds_left", "default_seconds", "max_seconds"}`; `set_confirmation_mode(mode, seconds=None, *, by="")` (`auto` in memory only, default 1 h, capped 4 h, raises `ValueError` on bad input; `ask`/`decline` persist as `safety.confirmation_mode`); `disable_auto_approve(*, by="")`. `auto` covers DESTRUCTIVE from `safety.AUTO_SOURCES` (web, web-panel, voice, voice-live, cli, cli-ask) only, with a real confirmer; CRITICAL, hard refusals and the denial memory are unchanged. `allow_destructive: false` = `decline`. Audit: `safety.auto_approved`, `safety.mode_changed`, `safety.auto_expired`. `check_path` refuses writes to `settings.json`. Skills must never call the setters (pinned by `tests/engine/test_confirmation_policy.py`).
- Live voice audio I/O (`live.py`, #83): `LiveSession(observer=, confirmer=, model=, source=None, sink=None)`; default = `SoundDeviceSource`/`SoundDeviceSink` (laptop mic/speaker, unchanged). Network client: `net = NetworkAudio(); s = LiveSession(source=net, sink=net, observer=..., confirmer=...)`, run `s.run()` on a worker thread (blocking, returns tool-call count).
  - In: `net.push_input(bytes)` from any thread — PCM16 mono little-endian **16 kHz** (`SEND_SAMPLE_RATE`), any chunk size (odd trailing byte is carried; bounded 100 chunks, oldest dropped; audio pushed before connect is kept).
  - Out: `net.pull_output(timeout) -> bytes | Control.CLEAR | Control.CLOSED | None` — PCM16 mono **24 kHz** (`RECEIVE_SAMPLE_RATE`). `CLEAR` = barge-in: the client must drop audio it already buffered (uncollected audio is dropped server-side). `CLOSED` = session over. `None` = timeout. Output backlog bounded (400 chunks, oldest dropped).
  - Stop: client disconnect → `net.close()` (ends the session cleanly); `s.stop()` also closes `net`, so a reader blocked in `pull_output` always gets `CLOSED`. Run the reader as a loop until `CLOSED`; no other thread is left behind.
  - Custom backends implement `AudioSource.capture(put, astop, on_ready)` / `AudioSink.play(audio_queue, stop)` + `interrupt()`. Pinned by `tests/engine/test_live_audio.py` (fake genai client).

- Live turn hygiene (`live.py`, #97): transcripts and replies go through `clean_transcript` (strips `<ctrlNN>`/`<unusedNN>`/turn tokens, removes Devanagari/Arabic script — never displayed, never transliterated: letter-by-letter Latin turned "turn off this flashlight" into "tarn of dis flaishalaait"). A turn with no audio, no usable text and no tool call, after the model began or the user spoke, emits `answer` = `EMPTY_REPLY_TEXT` and audits `live.empty_reply` (text only, not spoken). `voice.language`: `""` or `en+ur-roman` = English + Roman Urdu (recognition `en-US`, base language English: replies in English); any other value is a BCP-47 code as before. Config default stays `""` because `platform/stt.py` passes `voice.language` to whisper. Pinned by `tests/engine/test_live_turns.py`.
- English user transcript (`live.py` + `platform/stt.py`, #100): Gemini input transcription cannot be pinned to English (language_codes is ignored; Hindustani/accented English returns Devanagari), so each turn's mic audio (`_mic`, last 30 s) is run through faster-whisper `task="translate"` (`stt.translate_to_english`, multilingual `voice.translate_model`, default `base`, preloaded in a daemon thread at session start; `voice.live_translate` false disables). `heard` carries the English text; the reply never waits (Whisper runs in a task), only the `listening` marker is held back (<= `TRANSLATE_TIMEOUT` 45 s) so the client's user bubble is not split. Until the model is loaded (`stt.translate_ready()`), or on failure, the fallback is Gemini's text with foreign script stripped; `heard_partial` shows `…` while Devanagari-only. Tests set `stt.translate_available` false in the `backend` fixture so no model is loaded.

## Gotchas
- `service.py` (#76): the unit is never enabled (owner: manual on/off). Host comes from `web.env` `KRAVEIN_LAN_HOST`, port 8787. `phones remove` refuses ambiguous names; `open` never prints the token. Pinned by `tests/engine/test_service_cli.py` (systemctl/HTTP stubbed).
- Tests stub all three providers; breaker tests monkeypatch `config.STATE_DIR`. See `CLAUDE.md` "Testing conventions".
- Tool routing is pinned by `tests/engine/test_tool_routing.py` (default off, only `auto` enables, fail-open, `more_tools` edge cases, byte-stable tool list between rounds).
- `serve --lan` address choice is `cli._pick_lan_host`: explicit `--host` must be a held private address; several candidates and no `--host` → refuse and list (never `addresses[0]`). Pinned by `tests/engine/test_serve_lan_host.py`.
- `classify_command` hard-blocks shell writes to Kravein's `settings.json` (redirects, sed -i, cp/mv/tee/dd/ln, editors, interpreters); reads stay allowed. Text matching only: a path assembled at run time is not caught, and a bare `settings.json` counts as ours (cwd unknown). Pinned in `tests/engine/test_confirmation_policy.py` (#40). Globs matching the file and any write-class command naming its directory (`cp -r x dir/`, `cd dir && sed -i *`) are blocked too; bare globs without a directory part and the parent `~/.config` are not.
