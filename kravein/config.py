"""Configuration and filesystem layout.

The Windows build kept everything next to the script and leaned on
%LOCALAPPDATA%. Here we follow the XDG Base Directory spec instead, while
still reading the original `config/*.json` files so existing setups keep
working.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

APP_NAME = "kravein"

BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY_CONFIG_DIR = BASE_DIR / "config"


def _xdg(var: str, default: Path) -> Path:
    raw = os.environ.get(var, "").strip()
    return Path(raw) if raw else default


HOME = Path.home()
XDG_CONFIG_HOME = _xdg("XDG_CONFIG_HOME", HOME / ".config")
XDG_DATA_HOME = _xdg("XDG_DATA_HOME", HOME / ".local" / "share")
XDG_STATE_HOME = _xdg("XDG_STATE_HOME", HOME / ".local" / "state")
XDG_CACHE_HOME = _xdg("XDG_CACHE_HOME", HOME / ".cache")
XDG_RUNTIME_DIR = _xdg("XDG_RUNTIME_DIR", Path(f"/run/user/{os.getuid()}"))

# The assistant was called Brahma-Echo until 2026-08-24 and its XDG
# directories were named after it. The rename moved them, but a directory that
# was missed — or one restored from an old backup, or written by a stale
# checkout — would otherwise mean silently starting from scratch: no API keys,
# no audit history, a browser profile with no logins. So the old location is
# adopted when the new one does not exist yet.
#
# This is transitional. See the rename note in CLAUDE.md: legacy paths are to
# be removed once nothing is falling back to them.
LEGACY_APP_NAME = "brahma-echo"


def _app_dir(base: Path) -> Path:
    current = base / APP_NAME
    legacy = base / LEGACY_APP_NAME
    if not current.exists() and legacy.exists():
        return legacy
    return current


CONFIG_DIR = _app_dir(XDG_CONFIG_HOME)
DATA_DIR = _app_dir(XDG_DATA_HOME)
STATE_DIR = _app_dir(XDG_STATE_HOME)
CACHE_DIR = _app_dir(XDG_CACHE_HOME)
RUNTIME_DIR = XDG_RUNTIME_DIR / APP_NAME

AUDIT_LOG = STATE_DIR / "audit.jsonl"
SESSION_LOG = STATE_DIR / "session.log"
MEMORY_FILE = DATA_DIR / "memory.json"
SOCKET_PATH = RUNTIME_DIR / "kravein.sock"

# Directories the assistant may write to without extra scrutiny.
WORKSPACE_DIR = DATA_DIR / "workspace"


def ensure_dirs() -> None:
    """Create every directory the assistant writes to."""
    for d in (CONFIG_DIR, DATA_DIR, STATE_DIR, CACHE_DIR, RUNTIME_DIR, WORKSPACE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    # The socket lives in a runtime dir that must not be world-readable.
    try:
        RUNTIME_DIR.chmod(0o700)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "assistant_name": "Kravein",
    "user_title": "sir",
    "voice": {
        # Announce notifications aloud. Voice *mode* is started with /voice
        # and does not depend on this.
        "enabled": False,
        # Which pipeline a voice session uses, everywhere — the REPL, the CLI
        # and the web UI all resolve it through voice.resolve_mode().
        #
        #   auto  = live when a Gemini key is present, otherwise local
        #   local = mic -> whisper -> agent -> edge-tts (no API key needed)
        #   live  = the original Gemini Live websocket (needs a Gemini key)
        #
        # Naming one explicitly means it is a requirement: if it cannot run,
        # the session refuses rather than silently starting the other one.
        "mode": "auto",
        # The exact voice the Windows build used.
        "tts_voice": "en-US-GuyNeural",
        # Gemini Live's voice, as in the original.
        "live_voice": "Charon",
        "speak_replies": True,
        # auto | faster-whisper | whisper.cpp | vosk | gemini
        "stt_backend": "auto",
        # tiny.en is fastest; small.en is more accurate. base.en is the balance.
        "stt_model": "base.en",
        "language": "",
        # How long a pause ends your sentence.
        "silence_seconds": 1.2,
        "max_utterance_seconds": 30.0,
        "start_timeout": 300.0,
        # Set this to require a wake word before acting, e.g. "kravein".
        "wake_word": "",
        "wake_words": ["kravein", "hey kravein"],
        # Destructive actions are confirmed out loud in voice mode.
        "confirm_by_voice": True,
        "max_reply_chars": 700,
    },
    "llm": {
        # cline | gemini | openrouter.
        #
        # Cline's direct HTTP API is the primary. The original Windows build
        # used Gemini, but measured on this account gemini-3.6-flash is not
        # dependable enough to lead: it returned 503/504 or blew a 20s deadline
        # on nearly every live request over an extended test, while
        # deepseek-v3.2 answered in a consistent 2.5-3.1s. A primary model you
        # have to build a circuit breaker around is the wrong primary.
        #
        # Gemini is still used for voice (Live native-audio) and vision, where
        # it is the only option — see voice.live_model below.
        "provider": "cline",
        # Cline leads; NovaRouter is the backup. A single-entry chain meant one
        # provider outage took the whole assistant down with nowhere to fail
        # over, which matters much more now that the UI is reachable from a
        # phone and nobody is sitting at the machine to notice why.
        #
        # NovaRouter stays inert until a key exists — `available()` is false
        # without one, so the breaker skips it and nothing changes.
        "provider_order": ["cline", "novarouter"],
        # Kept configured for vision; not in the agent's text chain.
        "gemini_model": "gemini-3.6-flash",
        # A stalled Gemini is as unusable as a down one, so cap the wait and
        # fail over. Normal is 2-6s; Google sometimes takes 20-30s.
        # The API rejects anything under 10s.
        "gemini_timeout_seconds": 20,
        # Circuit breaker: after this many consecutive failures a provider is
        # skipped entirely for the cooldown, so an outage costs the timeout
        # once instead of on every single request.
        "breaker_failures": 2,
        "breaker_cooldown_seconds": 180,
        # Answers over Cline's direct HTTP API (no CLI, no ~5s process boot).
        # deepseek-v4-flash is gated to official Cline surfaces, so choosing it
        # forces the slow CLI path.
        # kimi-k2 was chosen by measurement on this workload: 15/15 on the
        # agentic eval, never reached a destructive tool unprompted on vague
        # requests, and $0.000150/request against minimax-m2.5's $0.002966.
        # See kravein/cline.py for the full comparison table.
        "cline_model": "moonshotai/kimi-k2",
        # Second best on restraint and judgment. Both verified for tool calling.
        "cline_fallback_model": "minimax/minimax-m2.5",
        # Set true to force every Cline call through the CLI.
        "cline_force_cli": False,
        # Cap the primary the same way Gemini is capped: a slow provider is as
        # unusable as a down one, and cancellation cannot interrupt a request
        # already in flight. Was effectively 180s, during which Stop could do
        # nothing. Only safe to cap because NovaRouter is now behind it.
        "cline_timeout_seconds": 45,
        # NovaRouter (https://novarouter.site) — an OpenAI-compatible gateway.
        # Verified against the live API on 2026-08-24: `/v1/models` returns an
        # OpenAI model list and `/v1/chat/completions` answers 401
        # `authentication_error` without a key. kimi-k3 is served from
        # nvidia-nim with a 262k context and text/vision/reasoning modalities.
        #
        # Base URL is configurable so a move does not need a code change.
        "novarouter_base_url": "https://novarouter.site/v1",
        "novarouter_model": "kimi-k3",
        "temperature": 0.4,
        "max_tool_iterations": 8,
        # "off" sends every skill with every request; "auto" sends only the
        # categories the request needs (see toolselect.py). Stays "off" until
        # tools/eval_models.py shows the narrowed set completes as many tasks.
        "tool_routing": "off",
    },
    "safety": {
        # When false, destructive skills refuse outright instead of prompting.
        "allow_destructive": True,
        # Paths the assistant must never modify, even with confirmation.
        "protected_paths": [
            "/boot", "/efi", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
            "/var/lib/pacman", "/proc", "/sys", "/dev",
        ],
        # Extra paths the user marks read-only. The WhatsApp session is here by
        # default because that file is a full device login — losing it means
        # re-pairing, and copying it means somebody else is that device.
        "readonly_paths": ["~/.local/share/kravein/whatsapp"],
        "confirm_timeout_seconds": 120,
    },
    "whatsapp": {
        # Who Kravein sends reminders to. Empty means the linked account
        # itself, which always works and cannot reach anyone else by accident.
        "owner": "",
        # **The inbound allowlist.** Only this number's direct messages are
        # read, and only against a fixed command vocabulary — everything else
        # is dropped without a reply, because answering confirms to a stranger
        # that something automated is listening. Digits only, country code
        # included, no "+" or spaces. Empty disables inbound entirely, which is
        # the safe default: a channel nobody configured should not be open.
        "owner_jid": "",
        # Ignore anything older than this when it arrives. Reconnecting
        # replays undelivered messages, and a replayed "done" must not silently
        # complete today's step.
        "inbound_max_age_seconds": 300,
        # Commands accepted per minute from the owner. Cheap guard against a
        # stuck client hammering the routine store.
        "inbound_rate_limit": 20,
    },
    "daemon": {
        "socket": str(SOCKET_PATH),
        # Daemon has no TTY, so it can never prompt. Destructive calls are
        # rejected unless the request explicitly pre-confirms them.
        "auto_confirm": False,
    },
    "integrations": {
        "discord": False,
        "kravein_connect": False,
        "dashboard": False,
    },
}

_settings_lock = threading.RLock()
_settings_cache: dict[str, Any] | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def settings_path() -> Path:
    return CONFIG_DIR / "settings.json"


def load_settings(reload: bool = False) -> dict[str, Any]:
    """Load settings, merging user overrides over the defaults."""
    global _settings_cache
    with _settings_lock:
        if _settings_cache is not None and not reload:
            return _settings_cache

        user: dict[str, Any] = {}
        path = settings_path()
        if path.exists():
            try:
                user = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[config] ignoring malformed {path}: {exc}")

        # Fall back to the original Windows-era settings file if present.
        if not user:
            legacy = LEGACY_CONFIG_DIR / "app_settings.json"
            if legacy.exists():
                try:
                    user = json.loads(legacy.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass

        _settings_cache = _deep_merge(DEFAULT_SETTINGS, user)
        return _settings_cache


def save_settings(data: dict[str, Any]) -> None:
    global _settings_cache
    with _settings_lock:
        ensure_dirs()
        path = settings_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        _settings_cache = _deep_merge(DEFAULT_SETTINGS, data)


def get(dotted: str, default: Any = None) -> Any:
    """Read a setting by dotted path, e.g. get('llm.temperature')."""
    node: Any = load_settings()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def api_keys() -> dict[str, str]:
    """Resolve API keys from env, then XDG config, then the legacy file.

    Environment wins so secrets can be injected by systemd without landing
    on disk.
    """
    keys = _read_json(LEGACY_CONFIG_DIR / "api_keys.json")
    keys = _deep_merge(keys, _read_json(CONFIG_DIR / "api_keys.json"))

    env_map = {
        "gemini_api_key": ("KRAVEIN_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "openrouter_api_key": ("KRAVEIN_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
        "novarouter_api_key": ("KRAVEIN_NOVAROUTER_API_KEY", "NOVAROUTER_API_KEY"),
    }
    for key, env_names in env_map.items():
        for name in env_names:
            value = os.environ.get(name, "").strip()
            if value:
                keys[key] = value
                break

    return {k: str(v).strip() for k, v in keys.items() if isinstance(v, (str, int))}


def gemini_api_key() -> str:
    return api_keys().get("gemini_api_key", "")


def openrouter_api_key() -> str:
    return api_keys().get("openrouter_api_key", "")


def novarouter_api_key() -> str:
    return api_keys().get("novarouter_api_key", "")


# --------------------------------------------------------------------------
# User directories (XDG user-dirs, with sane fallbacks)
# --------------------------------------------------------------------------

_USER_DIR_DEFAULTS = {
    "desktop": HOME / "Desktop",
    "downloads": HOME / "Downloads",
    "documents": HOME / "Documents",
    "pictures": HOME / "Pictures",
    "music": HOME / "Music",
    "videos": HOME / "Videos",
    "home": HOME,
}

_USER_DIR_XDG_KEYS = {
    "desktop": "XDG_DESKTOP_DIR",
    "downloads": "XDG_DOWNLOAD_DIR",
    "documents": "XDG_DOCUMENTS_DIR",
    "pictures": "XDG_PICTURES_DIR",
    "music": "XDG_MUSIC_DIR",
    "videos": "XDG_VIDEOS_DIR",
}

_user_dirs_cache: dict[str, Path] | None = None


def user_dirs() -> dict[str, Path]:
    """Resolve XDG user directories from `user-dirs.dirs`, with fallbacks.

    Unlike the Windows build (which read the Desktop path out of the
    registry) Linux stores these in a shell-style config file.
    """
    global _user_dirs_cache
    if _user_dirs_cache is not None:
        return _user_dirs_cache

    resolved = dict(_USER_DIR_DEFAULTS)
    conf = XDG_CONFIG_HOME / "user-dirs.dirs"
    parsed: dict[str, str] = {}
    if conf.exists():
        try:
            for line in conf.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                parsed[name.strip()] = value.strip().strip('"')
        except OSError:
            pass

    for key, xdg_key in _USER_DIR_XDG_KEYS.items():
        raw = os.environ.get(xdg_key) or parsed.get(xdg_key)
        if not raw:
            continue
        expanded = os.path.expandvars(raw.replace("$HOME", str(HOME)))
        candidate = Path(expanded).expanduser()
        if candidate.is_absolute():
            resolved[key] = candidate

    _user_dirs_cache = resolved
    return resolved


def resolve_user_path(raw: str) -> Path:
    """Turn 'desktop', '~/x', or an absolute path into a real Path."""
    text = (raw or "").strip()
    if not text:
        return HOME
    key = text.lower()
    dirs = user_dirs()
    if key in dirs:
        return dirs[key]
    return Path(os.path.expandvars(text)).expanduser()
