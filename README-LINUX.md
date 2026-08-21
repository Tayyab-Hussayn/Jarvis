<div align="center">

# Brahma Echo — Linux

**A JARVIS-style system assistant for Linux.**
Modular skills · real machine control · confirmation before anything destructive

</div>

---

## What this is

The original Brahma Echo was a Windows desktop assistant. This is the Linux
port: same purpose, rebuilt on native Linux mechanisms, restructured around a
modular skills system with a safety layer and a full audit trail.

It runs two ways — as an interactive terminal assistant, or as a background
systemd user service — off the same core.

**118 skills** across system control, files, processes, applications, windows,
settings, input synthesis, screen capture, notifications, documents, memory,
games and the web.

---

## Quick start

```bash
git clone <your-fork> Brahma-Echo && cd Brahma-Echo
./install-linux.sh
.venv/bin/python -m brahma            # interactive assistant
```

### The LLM backend

| Provider | Model | Used for |
|---|---|---|
| **`cline`** (default) | `deepseek/deepseek-v3.2` → `z-ai/glm-4.6` | everything text/agent |
| `gemini` | `gemini-2.5-flash-native-audio` / `gemini-3.6-flash` | **voice (Live) and vision only** |
| `openrouter` | free-tier chain | optional, off by default |

### Why Gemini does not lead

The original Windows build used Gemini, and this port did too — until it was
measured over an extended run on a real account:

| | Latency | Behaviour |
|---|---|---|
| `gemini-3.6-flash` | 1.8s – 48s | 503 / 504 / blew a 20s deadline on nearly every request |
| `deepseek/deepseek-v3.2` | **2.5 – 3.1s** | consistent, and cheapest per token |

A primary you have to build a circuit breaker around is the wrong primary.
Gemini stays configured because it is the only option for the Live voice
pipeline and for vision — but it is **out of the agent's text chain entirely**.

`provider_order` is **exclusive**: a provider left out of it is left out on
purpose, not merely deprioritised.

To put Gemini back in the chain:

```json
{ "llm": { "provider": "gemini", "provider_order": ["gemini", "cline"] } }
```

Keys live in `~/.config/brahma-echo/api_keys.json` (mode 0600), or set
`BRAHMA_GEMINI_API_KEY` — the environment wins, so systemd can inject it
without it landing on disk.

> **Model note.** `gemini-2.5-flash` — the original's model — now returns
> `404 … no longer available to new users` for freshly created keys.

### Failover

```
cline: deepseek/deepseek-v3.2  ──(failure)──▶  cline: z-ai/glm-4.6
```

One same-provider hop, both verified for native tool calling. There is no
Gemini in this path.

If you do re-enable Gemini as primary, two safeguards apply automatically:

- **A deadline.** A slow model is as unusable as a down one, so requests are
  capped at 20s (`llm.gemini_timeout_seconds`). Google rejects any deadline
  under 10s, so the value is clamped.
- **A circuit breaker.** After 2 consecutive failures a provider is skipped
  for 3 minutes. Without it an outage costs the full timeout on *every*
  request; with it, once. State lives in
  `~/.local/state/brahma-echo/llm-breaker.json`, because each `brahma ask` is
  a fresh process and would otherwise forget.

Measured during a real Gemini slowdown, with the breaker doing its job:

| Request | Time |
|---|---|
| 1st (pays the timeout, trips the breaker) | 31.5s |
| 2nd | 5.5s |
| 3rd | 5.8s |

A success clears the cooldown immediately. To retry the primary at once:

```bash
.venv/bin/python -m brahma run reset_llm_failover
```

**Cline** needs no key of ours — it reuses the CLI's own session:

```bash
cline auth          # once, if you have not already
```

**Brahma decides nothing on its own authority**: every action goes through
Brahma's safety layer and audit trail, whichever backend answers.

Switch backend or model at any time:

```bash
.venv/bin/python -m brahma run llm_status
.venv/bin/python -m brahma run set_llm_model model=z-ai/glm-4.6
.venv/bin/python -m brahma doctor     # check the whole setup
```

### Commands

| Command | What it does |
|---|---|
| `python -m brahma` | Interactive assistant (default) |
| `python -m brahma ask "..."` | One request, then exit |
| `python -m brahma run <skill> k=v` | Run one skill directly, no model involved |
| `python -m brahma skills [category]` | List skills |
| `python -m brahma env` | Environment and capability report |
| `python -m brahma doctor` | Diagnose setup problems |
| `python -m brahma audit -n 50` | Read the audit trail |
| `python -m brahma daemon` | Run as a background service |
| `python -m brahma send "..."` | Talk to a running daemon |

Inside the REPL: `/voice`, `/say`, `/help`, `/skills`, `/skill <name>`,
`/run <name> k=v`, `/env`, `/audit`, `/reset`, `/quit`.

---

## Voice

Start hands-free mode from inside the assistant:

```
› /voice
```

Speak when you see `● listening`. It transcribes, acts, and answers out loud.
Say **"stop listening"** or press **Ctrl-C** to go back to typing. Text mode is
untouched — voice is a mode you enter and leave.

```bash
.venv/bin/python -m brahma voice           # straight into voice
.venv/bin/python -m brahma voice status    # check mic, recogniser, speech
.venv/bin/python -m brahma voice --no-speak  # listen and act, but stay silent
```

### The two pipelines

| Mode | Path | Needs |
|---|---|---|
| **`local`** (default) | mic → faster-whisper → your LLM → edge-tts | nothing |
| `live` | one Gemini Live websocket doing speech-in, reasoning and speech-out | a Gemini key |

`local` works with any backend, including Cline, and needs no API key at all.

`live` is the **original Windows pipeline**, ported intact —
`gemini-2.5-flash-native-audio-preview-12-2025`, 16 kHz in / 24 kHz out, voice
`Charon`, tools attached, with a fallback to
`native-audio-latest` / `gemini-3.1-flash-live-preview` if that dated preview
goes away. It needs a Gemini key with native-audio access:

```
› /voice live
```

### Speech output

Text-to-speech is the original's own engine: **edge-tts**, voice
**`en-US-GuyNeural`** — unchanged from the Windows build. Only playback moved
from `winmm` to `mpv`.

> If you keep `keep-open=yes` in `~/.config/mpv/mpv.conf`, that would normally
> hang playback forever. Brahma runs mpv with `--no-config`, so your player
> settings can't break speech.

### Confirmation by voice

In voice mode there is no prompt to type into, so destructive actions are
confirmed **out loud**:

> *"Move to trash notes.txt. That is a destructive action. Say yes to continue, or no to cancel."*

Say **yes** or **no**. Silence counts as **no**. The hard blocks (`/etc`,
killing the compositor, `rm -rf /`) still apply and cannot be talked past.

### Tuning

In `~/.config/brahma-echo/settings.json`:

```json
{
  "voice": {
    "stt_model": "base.en",
    "silence_seconds": 1.2,
    "wake_word": "",
    "confirm_by_voice": true
  }
}
```

- `stt_model` — `tiny.en` (fastest), `base.en` (default), `small.en` (most accurate)
- `silence_seconds` — how long a pause ends your sentence
- `wake_word` — set to e.g. `"brahma"` to require it before acting

Expect roughly **2s** to transcribe, **5s** to think, **2s** to speak.

---

## Safety

Every action is classified into one of four levels before it runs.

| Level | Examples | Behaviour |
|---|---|---|
| **safe** | status, listing, search, reading | Runs immediately |
| **moderate** | launch app, set volume, write a new file | Runs immediately |
| **destructive** | delete, kill, overwrite, close a window | **Asks you first** |
| **critical** | shutdown, reboot, install packages, system services | **Asks you first** |

Some things are refused outright and **no confirmation can override them**:

- Writes or deletes inside `/etc`, `/usr`, `/boot`, `/bin`, `/lib`, `/sys`, `/proc`, …
- `rm -rf /`, fork bombs, `mkfs`, raw writes to block devices, `curl … | sh`
- Killing PID 1, the compositor, PipeWire, logind, sshd, the assistant itself
- `$HOME` or `/` as a bulk delete target

Deletes go to the **XDG trash**, not `unlink()`, unless you explicitly pass
`permanent=true`.

The confirmation prompt is supplied by the front-end. The terminal provides
one; **the daemon does not**, so a background request can never approve its
own destructive action — it must carry `"confirm": true` explicitly.

Configure in `~/.config/brahma-echo/settings.json`:

```json
{
  "safety": {
    "allow_destructive": true,
    "readonly_paths": ["~/important-project"]
  }
}
```

Setting `allow_destructive: false` makes destructive skills refuse rather than prompt.

---

## Audit trail

Every skill call, shell command, confirmation decision and LLM turn is written
to `~/.local/state/brahma-echo/audit.jsonl`, one JSON object per line.
API keys and anything key-like are redacted before writing.

```bash
python -m brahma audit -n 50
python -m brahma audit -e safety          # just the safety decisions
python -m brahma audit -e shell | jq .    # just the commands run
```

---

## Architecture

```
brahma/
├── config.py        XDG paths, settings, credentials
├── audit.py         append-only JSONL audit log
├── safety.py        risk classification + confirmation broker
├── registry.py      skill registry; derives LLM schemas from signatures
├── llm.py           provider failover + tool calling
├── cline.py         Cline CLI as an LLM provider (default)
├── voice.py         hands-free voice loop (local pipeline)
├── live.py          Gemini Live websocket (the original pipeline)
├── agent.py         the tool-calling loop
├── cli.py           interactive REPL and argument parsing
├── daemon.py        Unix-socket background service
├── platform/        one module per OS concern (see table below)
└── skills/          one module per capability group
```

### Adding a skill

Drop a function into any module under `brahma/skills/`. No core file changes:

```python
from ..registry import skill
from ..safety import Danger

@skill(category="system", danger=Danger.SAFE, requires=("sensors",))
def cpu_temperature(unit: str = "celsius") -> str:
    """Report the CPU temperature.

    Args:
        unit: 'celsius' or 'fahrenheit'.
    """
    ...
```

The decorator reads the signature, type hints and docstring to build the tool
schema the model sees. `requires` names external binaries; if one is missing
the skill reports that instead of failing mysteriously.

Mark anything that destroys state `Danger.DESTRUCTIVE` and call
`safety.enforce(...)` inside it — the registry will not do it for you, because
only the skill knows what its arguments actually mean.

---

## What replaced what

| Windows mechanism | Linux replacement |
|---|---|
| `pyautogui` | `ydotool` (Wayland) / `xdotool` (X11) |
| `pygetwindow`, `pywinauto` | `hyprctl` / `swaymsg` / `wmctrl` |
| `pycaw` + `comtypes` | `wpctl` (WirePlumber) / `pactl` |
| `mss`, `pyautogui.screenshot()` | `grim` + `slurp` (Wayland) / `maim` (X11) |
| `pyperclip` | `wl-clipboard` / `xclip` |
| `win10toast` | `libnotify` (`notify-send`) |
| `winmm.mciSendStringW` (TTS playback) | `mpv` / `ffplay` |
| `wpndatabase.db` (toast snooping) | `org.freedesktop.Notifications` on D-Bus |
| `winreg` (startup entry) | systemd user service |
| `winreg` (Desktop path) | XDG user-dirs |
| `winreg` (default browser) | `xdg-settings` |
| `winreg` (Steam/Epic paths) | `libraryfolders.vdf` + `appmanifest_*.acf` |
| `winreg` (dark mode) | `gsettings` colour-scheme |
| PowerShell WMI brightness | `brightnessctl` |
| PowerShell Wi-Fi adapter | `nmcli` |
| `shutdown.exe`, Task Scheduler | `systemctl`, `loginctl`, `systemd-run` |
| `tasklist` / `taskkill` | `psutil` + `/proc` |
| `os.startfile` | `xdg-open` / `gio open` |
| `winsound.Beep` | `libnotify` + `mpv` |
| Start-menu keystroke launching | freedesktop `.desktop` entry index |
| `cv2.CAP_DSHOW` | V4L2 (`cv2.CAP_V4L2`) |

---

## How the Cline backend works

Cline is itself a coding agent, so it is reduced to a pure reasoning engine:

```
cline --json --auto-approve false --compaction off \
      -m deepseek/deepseek-v4-flash \
      -s "<Brahma's system prompt>" \
      -c ~/.cache/brahma-echo/cline-workspace \
      "<conversation + tool catalogue>"
```

- **`--auto-approve false`** — Cline may not run any of its own tools.
- **`-s`** — replaces its coding-agent prompt, so it reasons for Brahma instead.
- **`-c`** — an empty scratch directory, so it never reads your repository.
- Tool calling is structured JSON: Cline returns
  `{"tool_calls":[…]}` or `{"reply":"…"}`, Brahma parses it and executes.

**The CLI is only the fallback.** `api.cline.bot` exposes an OpenAI-compatible
`/chat/completions` endpoint that accepts your account's own OAuth token, so
most models are reached over **one HTTPS request with native tool calling** —
no CLI, no process boot. Brahma refreshes the token itself using the refresh
token from Cline's store, and caches the short-lived access token in its own
state dir; **Cline's config file is only ever read, never written.**

A few models — `deepseek-v4-flash` among them — are gated server-side and
return `403: only available via Cline product surfaces`. Those fall back to
driving the real CLI, which *is* such a surface, rather than forging headers to
impersonate a client we are not.

**The difference is large.** Same request, 118 tools:

| Path | Time |
|---|---|
| Direct HTTP (`deepseek-v3.2`) | **2.65s** median |
| CLI fallback (`deepseek-v4-flash`) | 11–42s |
| `cline --version` alone, no inference | ~5.0s |
| `node -e ""` | 0.07s |

That ~5s CLI floor is it parsing ~174 MB of bundled JavaScript on every
invocation, and prompt caching never engages (`cacheReadTokens=0`) because each
subprocess is a fresh conversation.

Model latency also flips completely once a large tool schema is involved —
benchmark with your real tools, not a "say OK" prompt:

| Model | bare | with 118 tools |
|---|---|---|
| `deepseek/deepseek-v3.2` | 6.0s | **2.65s** (also cheapest) |
| `z-ai/glm-4.6` | 4.0s | 3.80s |
| `moonshotai/kimi-k2` | 2.1s | 7.05s |
| `anthropic/claude-haiku-4.5` | 1.8s | 22.2s (15–53s spread) |

Force the CLI for everything with `{"llm": {"cline_force_cli": true}}`.

Health check:

```bash
.venv/bin/python -m brahma run llm_status
```

---

## Hyprland 0.56+ note

Hyprland 0.56 replaced its shell-style dispatcher with a Lua API. The old form
no longer parses:

```
$ hyprctl dispatch closewindow address:0x55f2ee251f50
error: ')' expected near 'address'
```

Brahma probes once at startup and speaks whichever dialect your compositor
understands, so both old and new Hyprland work.

**There is a sharp edge worth knowing about.** In the Lua API,
`window.close()`, `window.kill()`, `window.float()` and `window.move()` act on
the **focused** window and *silently ignore* an `"address:…"` argument. So this
looks like it targets one window and actually destroys whatever is focused:

```lua
hl.dispatch(hl.dsp.window.kill("address:0xTARGET"))  -- kills the FOCUSED window
```

Brahma therefore focuses the target, confirms the focus landed, and only then
acts — and refuses outright if focus fails. `close_app` and `close_window` also
verify the window actually went away instead of trusting the request, and
report honestly when an app is showing a save prompt. Pass `force=true` to kill
a client that ignores a polite close.

---

## Requirements

**Python 3.11+.** Arch packages the assistant drives:

```bash
sudo pacman -S --needed \
  grim slurp wl-clipboard ydotool brightnessctl playerctl \
  libnotify wireplumber networkmanager mpv xdg-utils \
  pacman-contrib dbus
```

Typing and clicking need the ydotool daemon running:

```bash
systemctl --user enable --now ydotool.service
# some builds ship it as a system unit instead:
# sudo systemctl enable --now ydotoold.service
```

If it fails on `/dev/uinput`: `sudo usermod -aG input $USER`, then re-login.

`python -m brahma doctor` tells you exactly what is missing and the command to
fix it.

---

## Running as a service

```bash
systemctl --user enable --now brahma-echo
journalctl --user -u brahma-echo -f
python -m brahma send "what is using my disk?"
```

It is a **user** service, not a system one — it needs your session bus,
compositor socket, audio and file permissions. Put credentials in
`~/.config/brahma-echo/env` so they never enter the unit file or the journal.

---

## Known gaps

| Feature | Status |
|---|---|
| PyQt6 desktop GUI (`ui.py`, 12k lines) | **Not ported.** Terminal + daemon first; the core is UI-agnostic so a shell can attach later. |
| Gemini Live voice | **Ported intact** as `/voice live`; needs a Gemini key. The default `/voice` uses a local recogniser instead, so voice works without one. |
| Hand-gesture tracking | **Blocked.** `mediapipe` has no Python 3.14 wheels. Needs a 3.12/3.13 venv. |
| Camera capture (`screen_processor.py`) | **Needs `cv2.CAP_V4L2`**; the original hardcodes the Windows-only `CAP_DSHOW`. |
| Epic Games Launcher | **No Linux client exists.** Uses Legendary/Heroic when installed. |
| Windows UI automation (`pywinauto` clicking) | **Replaced,** not reproduced — `click_on_screen` uses vision + ydotool instead. |

The original modules are all still in the tree; nothing was deleted.
