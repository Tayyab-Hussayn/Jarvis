<div align="center">
  <img src="assets/Kravein_Lite_Logo.png" alt="Kravein" width="260" />

  <h1>Kravein</h1>
  <p><strong>A Linux desktop AI assistant — voice, text, web UI and a phone app</strong></p>
</div>

Kravein controls the desktop it runs on (Hyprland, niri, Sway, X11): windows and workspaces, apps,
files, settings, media, schedules and routines — through text, live voice, a web UI, and the
Kravein Connect Android app. Every state-changing action goes through a safety layer that asks
before anything destructive.

## Getting started

```bash
./install-linux.sh        # venv, native tool check, the `kravein` command
kravein start             # web UI + phone channel on https://<lan-ip>:8787
kravein pair              # pair the phone app (QR in the terminal)
kravein status | stop | restart | open | logs
```

Other entry points: `.venv/bin/python -m kravein` (REPL), `kravein ask "…"`, `kravein voice test`,
`kravein doctor`. Details: [README-LINUX.md](README-LINUX.md).

## Layout

| Path | What |
|---|---|
| `kravein/` | The assistant (agent loop, skills, safety, platform backends, server). |
| `web/` | The web UI (Next.js), built into `kravein/web-dist/`. |
| `android/` | Kravein Connect, the phone app (Kotlin/Compose). |

## Tests

```bash
.venv/bin/python -m pytest tests -q
```
