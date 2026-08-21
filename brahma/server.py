"""Local web server for the Brahma Echo frontend.

Opt-in only: started by ``brahma serve``, never by the REPL or the systemd
daemon. Binds loopback and gates every request on a token — see
:mod:`brahma.webauth` for why loopback alone is not enough.

Shape of the thing:

  browser ──ws──> event loop ──worker thread──> Agent.ask() ──> skills
          <──frames──          <──confirm──

The agent loop is blocking, so it runs on a worker thread; frames come back
through :class:`events.Session`, which also carries confirmations the other
way. That bridge, not the HTTP surface, is the interesting part.

Unlike :mod:`brahma.daemon` — which installs a confirmer that auto-denies
everything destructive, because a background service must not approve its own
dangerous actions — this server asks the human sitting in front of it. What it
does **not** do is weaken :func:`safety.enforce`: forbidden actions stay
forbidden no matter what the browser answers.
"""

# NOTE: deliberately *no* ``from __future__ import annotations`` here.
# FastAPI resolves a route's type hints to decide what is a request object and
# what is a query parameter. With postponed annotations those hints are strings
# resolved against module scope, where the lazily-imported ``Request`` and
# ``WebSocket`` names do not exist — so every handler's request argument gets
# misread as a required query parameter and the route 422s.

import asyncio
import contextlib
import json
import threading
from pathlib import Path
from typing import Any, Optional

from . import audit, config, events, registry, webauth
from .agent import Agent
from .platform import detect

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

# Where `next build` drops the static export. Absent during backend-only
# development, which is not an error — the API still serves.
WEB_DIST = Path(__file__).resolve().parent / "web-dist"

# The Next dev server, allowed as an Origin so `npm run dev` can talk to a
# locally running backend without disabling the check outright.
DEV_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def _unauthorized(detail: str = "Invalid or missing token"):
    from fastapi import HTTPException

    return HTTPException(status_code=401, detail=detail)


def create_app(token: str | None = None) -> Any:
    """Build the FastAPI application.

    Imports FastAPI lazily so the rest of the package still imports on a
    machine without the web extras installed.
    """
    from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    expected = token or webauth.issue_token()
    app = FastAPI(title="Brahma Echo", docs_url=None, redoc_url=None)
    app.state.token = expected

    # -- guards ----------------------------------------------------------

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        if not webauth.host_allowed(request.headers.get("host")):
            return JSONResponse({"detail": "Bad Host"}, status_code=400)
        if not webauth.origin_allowed(request.headers.get("origin"), DEV_ORIGINS):
            return JSONResponse({"detail": "Bad Origin"}, status_code=403)
        return await call_next(request)

    def require_token(request: Request) -> None:
        supplied = webauth.token_from_request(
            request.headers.get("authorization"),
            request.query_params.get("token"),
        )
        if not webauth.verify_token(supplied, app.state.token):
            raise _unauthorized()

    auth = [Depends(require_token)]

    # -- REST ------------------------------------------------------------

    @app.get("/api/state", dependencies=auth)
    def api_state() -> dict[str, Any]:
        return _state_snapshot()

    @app.get("/api/skills", dependencies=auth)
    def api_skills() -> dict[str, Any]:
        registry.load_skills()
        out = []
        for name, skill in sorted(registry.all_skills().items()):
            ok, why = skill.available()
            out.append({
                "name": name,
                "description": skill.description,
                "category": skill.category,
                "danger": skill.danger.name,
                "available": ok,
                "unavailable_reason": why,
                "parameters": skill.parameters,
                "aliases": list(skill.aliases),
            })
        return {"skills": out}

    @app.get("/api/system", dependencies=auth)
    def api_system() -> dict[str, Any]:
        return _system_snapshot()

    @app.get("/api/audit", dependencies=auth)
    def api_audit(n: int = 100, prefix: str = "") -> dict[str, Any]:
        limit = max(1, min(int(n), 1000))
        return {"entries": audit.tail(limit=limit, event_prefix=prefix)}

    @app.get("/api/voice", dependencies=auth)
    def api_voice(probe: bool = False) -> dict[str, Any]:
        # `probe` records from the microphone, so it is opt-in rather than
        # something a page refresh triggers.
        return _voice_snapshot(probe=probe)

    @app.get("/api/settings", dependencies=auth)
    def api_settings() -> dict[str, Any]:
        return {"settings": _redacted_settings()}

    @app.post("/api/settings", dependencies=auth)
    async def api_settings_patch(request: Request) -> dict[str, Any]:
        patch = await request.json()
        if not isinstance(patch, dict):
            raise _unauthorized("Expected a JSON object")
        current = config.load_settings()
        merged = _deep_merge(current, patch)
        config.save_settings(merged)
        audit.record("web.settings_updated", keys=sorted(patch.keys()))
        return {"settings": _redacted_settings()}

    # -- websocket -------------------------------------------------------

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        headers = socket.headers
        if not webauth.host_allowed(headers.get("host")):
            await socket.close(code=1008)
            return
        if not webauth.origin_allowed(headers.get("origin"), DEV_ORIGINS):
            await socket.close(code=1008)
            return
        supplied = webauth.token_from_request(
            headers.get("authorization"), socket.query_params.get("token")
        )
        if not webauth.verify_token(supplied, app.state.token):
            # Close before accepting: never hand an unauthenticated peer an
            # open socket, not even briefly.
            await socket.close(code=1008)
            return

        await socket.accept()
        loop = asyncio.get_running_loop()
        session = events.Session(loop)
        agent = Agent(
            source="web",
            observer=events.observer_for(session),
            confirmer=session.confirm,
        )
        # One turn at a time. A second `ask` mid-turn is rejected outright
        # rather than queued — queuing would let a user stack destructive
        # requests behind a confirmation they have not answered yet.
        busy = threading.Event()

        audit.record("web.connected")
        session.emit("hello", **_hello_payload())

        async def pump() -> None:
            """Drain the outbound queue onto the socket."""
            while True:
                payload = await session.next_frame()
                await socket.send_text(json.dumps(payload, default=str))

        pump_task = asyncio.create_task(pump())

        async def run_turn(turn_id: str, text: str) -> None:
            session.turn_id = turn_id
            session.emit("turn_start", id=turn_id)
            try:
                turn = await asyncio.to_thread(agent.ask, text)
                session.emit(
                    "turn_end", id=turn_id,
                    iterations=turn.iterations,
                    tool_calls=turn.tool_calls,
                    error=turn.error,
                )
            except Exception as exc:  # never let one bad turn kill the socket
                audit.record("web.turn_failed", error=f"{type(exc).__name__}: {exc}")
                session.emit("turn_end", id=turn_id, iterations=0, error=str(exc))
            finally:
                busy.clear()

        turn_task: asyncio.Task | None = None
        try:
            while True:
                raw = await socket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    session.emit("error", message="Malformed frame")
                    continue
                if not isinstance(msg, dict):
                    session.emit("error", message="Malformed frame")
                    continue

                kind = msg.get("t")
                if kind == "ping":
                    session.emit("pong")

                elif kind == "ask":
                    text = str(msg.get("text") or "").strip()
                    turn_id = str(msg.get("id") or "")
                    if not text:
                        continue
                    if busy.is_set():
                        session.emit(
                            "error", id=turn_id,
                            message="A request is already running.",
                        )
                        continue
                    busy.set()
                    turn_task = asyncio.create_task(run_turn(turn_id, text))

                elif kind == "confirm_reply":
                    ok = session.resolve_confirm(
                        str(msg.get("confirm_id") or ""), bool(msg.get("approve"))
                    )
                    if not ok:
                        session.emit(
                            "error",
                            message="That confirmation already expired.",
                        )

                elif kind == "reset":
                    if busy.is_set():
                        session.emit(
                            "error", message="Cannot reset mid-request."
                        )
                        continue
                    agent.reset()
                    session.emit("reset_ok")

                else:
                    session.emit("error", message=f"Unknown frame {kind!r}")

        except WebSocketDisconnect:
            pass
        finally:
            # Releasing the session first unblocks any worker parked on a
            # confirmation — it resolves to deny, and the turn unwinds.
            session.close()
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
            if turn_task is not None:
                turn_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await turn_task
            audit.record("web.disconnected")

    # -- static ----------------------------------------------------------

    if WEB_DIST.is_dir():
        index = WEB_DIST / "index.html"

        @app.get("/")
        def root() -> Any:
            return FileResponse(index)

        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/")
        def root_missing() -> dict[str, str]:
            return {
                "detail": "Frontend not built. Run `npm run build` in web/, "
                          "or use the Next dev server on :3000."
            }

    return app


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def _provider_available(provider: Any) -> bool:
    try:
        return bool(provider.available())
    except Exception:
        return False


def _hello_payload() -> dict[str, Any]:
    registry.load_skills()
    env = detect.environment()
    return {
        "assistant_name": config.get("assistant_name", "Brahma"),
        "skills": len(registry.all_skills()),
        "provider": config.get("llm.provider", ""),
        "model": config.get("llm.cline_model", ""),
        "environment": env.summary(),
    }


def _state_snapshot() -> dict[str, Any]:
    from .llm import client

    registry.load_skills()
    env = detect.environment()
    llm = client()
    providers = []
    for provider in llm._all():
        providers.append({
            "name": provider.name,
            "available": _provider_available(provider),
            "cooling": llm._is_cooling(provider.name),
        })
    return {
        "assistant_name": config.get("assistant_name", "Brahma"),
        "skills": len(registry.all_skills()),
        "provider": config.get("llm.provider", ""),
        "model": config.get("llm.cline_model", ""),
        "fallback_model": config.get("llm.cline_fallback_model", ""),
        "provider_order": config.get("llm.provider_order", []),
        "providers": providers,
        "environment": {
            "distro": env.distro,
            "kernel": env.kernel,
            "session_type": env.session_type,
            "desktop": env.desktop,
            "compositor": env.compositor,
            "audio": env.audio,
            "package_manager": env.package_manager,
            "summary": env.summary(),
        },
    }


def _system_snapshot() -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return {"available": False}

    snapshot: dict[str, Any] = {"available": True}
    # Each block is guarded separately: a container without battery or a
    # permission-restricted mount must not blank the whole dashboard.
    with contextlib.suppress(Exception):
        snapshot["cpu_percent"] = psutil.cpu_percent(interval=None)
        snapshot["cpu_count"] = psutil.cpu_count()
        load = psutil.getloadavg()
        snapshot["load"] = [round(v, 2) for v in load]
    with contextlib.suppress(Exception):
        mem = psutil.virtual_memory()
        snapshot["memory"] = {
            "total": mem.total, "used": mem.used, "percent": mem.percent,
        }
    with contextlib.suppress(Exception):
        swap = psutil.swap_memory()
        snapshot["swap"] = {
            "total": swap.total, "used": swap.used, "percent": swap.percent,
        }
    with contextlib.suppress(Exception):
        disk = psutil.disk_usage("/")
        snapshot["disk"] = {
            "total": disk.total, "used": disk.used, "percent": disk.percent,
        }
    with contextlib.suppress(Exception):
        battery = psutil.sensors_battery()
        if battery is not None:
            snapshot["battery"] = {
                "percent": round(battery.percent, 1),
                "plugged": bool(battery.power_plugged),
            }
    with contextlib.suppress(Exception):
        net = psutil.net_io_counters()
        snapshot["network"] = {"sent": net.bytes_sent, "received": net.bytes_recv}
    with contextlib.suppress(Exception):
        snapshot["uptime_seconds"] = round(
            __import__("time").time() - psutil.boot_time()
        )
    return snapshot


def _voice_snapshot(*, probe: bool = False) -> dict[str, Any]:
    """Voice chain status. Probing the mic actually records, so it is opt-in."""
    from .platform import stt, tts

    snapshot: dict[str, Any] = {
        "stt_backend": "",
        "stt_status": "",
        "stt_model": "",
        "tts_available": False,
        "live_configured": False,
        "mic": None,
    }
    with contextlib.suppress(Exception):
        snapshot["stt_backend"] = stt.backend_name()
        snapshot["stt_status"] = stt.status()
        snapshot["stt_model"] = stt.model_size()
    with contextlib.suppress(Exception):
        snapshot["tts_available"] = bool(tts.available())
    with contextlib.suppress(Exception):
        # api_keys() is keyed by the full setting name, not the provider.
        snapshot["live_configured"] = bool(config.api_keys().get("gemini_api_key"))

    if probe:
        with contextlib.suppress(Exception):
            from .platform import mic

            health = mic.health()
            snapshot["mic"] = {
                "floor": round(health.floor, 4),
                "peak": round(health.peak, 4),
                "clipped_percent": round(health.clipped_percent, 2),
                "dc_offset": round(health.dc_offset, 4),
                "threshold": round(health.threshold, 4),
                "clamped": health.clamped,
                "healthy": health.healthy,
            }
    return snapshot


# Anything whose key looks like a credential is replaced wholesale. This
# mirrors audit.py's redaction: the settings API is a read surface for a
# browser, and key material must never cross it.
_SECRET_HINTS = ("key", "token", "secret", "password", "credential")


def _redacted_settings() -> dict[str, Any]:
    return _redact(config.load_settings())


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            if any(hint in str(key).lower() for hint in _SECRET_HINTS):
                out[key] = "***" if inner else ""
            else:
                out[key] = _redact(inner)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a settings patch, dropping redaction placeholders.

    A client that GETs settings and PUTs them back would otherwise write the
    literal ``"***"`` over a real API key.
    """
    out = dict(base)
    for key, value in patch.items():
        if value == "***":
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = True,
) -> int:
    """Run the web server until interrupted."""
    try:
        import uvicorn
    except ImportError:
        print("The web server needs fastapi and uvicorn:")
        print("  .venv/bin/pip install fastapi uvicorn")
        return 1

    if host not in ("127.0.0.1", "localhost", "::1"):
        # Remote access is a v3 feature with its own pairing and device
        # tokens. Refuse rather than quietly exposing the machine.
        print(f"Refusing to bind {host}: only loopback is supported.")
        return 1

    token = webauth.issue_token()
    url = f"http://{host}:{port}/?token={token}"

    registry.load_skills()
    app = create_app(token)

    print(f"Brahma Echo web UI on {url}")
    print(f"Token stored at {webauth.token_path()}")
    if not WEB_DIST.is_dir():
        print("(frontend not built yet — API only)")

    if open_browser:
        import webbrowser

        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    audit.record("web.server_started", host=host, port=port)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
