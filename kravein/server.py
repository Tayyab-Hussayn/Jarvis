"""Local web server for the Kravein frontend.

Opt-in only: started by ``kravein serve``, never by the REPL or the systemd
daemon. Binds loopback and gates every request on a token — see
:mod:`kravein.webauth` for why loopback alone is not enough.

Shape of the thing:

  browser ──ws──> event loop ──worker thread──> Agent.ask() ──> skills
          <──frames──          <──confirm──

The agent loop is blocking, so it runs on a worker thread; frames come back
through :class:`events.Session`, which also carries confirmations the other
way. That bridge, not the HTTP surface, is the interesting part.

Unlike :mod:`kravein.daemon` — which installs a confirmer that auto-denies
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
import collections
import contextlib
import ipaddress
import json
import threading
from pathlib import Path
from typing import Any, Optional

from . import audit, config, discovery, events, phone, registry, safety, webauth
from .agent import Agent
from .platform import detect
from .safety import confirmer_scope

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

# Where `next build` drops the static export. Absent during backend-only
# development, which is not an error — the API still serves.
WEB_DIST = Path(__file__).resolve().parent / "web-dist"

# The Next dev server, allowed as an Origin so `npm run dev` can talk to a
# locally running backend without disabling the check outright.
DEV_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def _is_phone_path(path: str) -> bool:
    return path == "/phone" or path.startswith("/phone/")


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")


_POLICY_KEYS = frozenset({"confirmation_mode", "allow_destructive"})


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
    @contextlib.asynccontextmanager
    async def lifespan(_app):
        # Reminders fire in the tick process; this thread hands them to phones.
        stop = threading.Event()
        threading.Thread(
            target=phone.OutboxRelay().run, args=(stop,), name="phone-outbox", daemon=True
        ).start()
        # Sweeps once now (startup), then hourly: expired phones are removed
        # even if they never reconnect.
        threading.Thread(
            target=phone.run_expiry_sweeper, args=(stop,), name="phone-expiry", daemon=True
        ).start()
        try:
            yield
        finally:
            stop.set()

    app = FastAPI(title="Kravein", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.token = expected
    # The address a phone should dial, set by `serve()`. Unset under tests and
    # behind the dev server, where the request's own Host is used instead.
    app.state.public_host = None
    app.state.public_port = None
    # SHA-256 of the served certificate; set by `serve()` under TLS only.
    app.state.cert_sha256 = None

    # -- guards ----------------------------------------------------------

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        if not webauth.host_allowed(request.headers.get("host")):
            return JSONResponse({"detail": "Bad Host"}, status_code=400)
        if not webauth.origin_allowed(request.headers.get("origin"), DEV_ORIGINS):
            return JSONResponse({"detail": "Bad Origin"}, status_code=403)
        if _is_phone_path(request.url.path) and request.headers.get("origin"):
            # Phones never send Origin; browsers always do on cross-site POSTs
            # and on every websocket handshake. Refusing any Origin here means
            # no web page, not even our own, can drive the phone endpoints.
            return JSONResponse({"detail": "Phone endpoints refuse browsers"}, status_code=403)
        return await call_next(request)

    def require_token(request: Request) -> None:
        supplied = webauth.token_from_request(
            request.headers.get("authorization"),
            request.query_params.get("token"),
        )
        if not webauth.token_accepted(supplied, app.state.token):
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

    # Skills return prose formatted for a model to read, not structured data.
    # Panels that need real records read the platform layer directly rather
    # than regex-parsing that prose — parsing it would break the moment a
    # message is reworded, and the wording exists to serve the model.
    @app.get("/api/windows", dependencies=auth)
    def api_windows() -> dict[str, Any]:
        return _windows_snapshot()

    @app.get("/api/apps", dependencies=auth)
    def api_apps() -> dict[str, Any]:
        return _apps_snapshot()

    @app.get("/api/processes", dependencies=auth)
    def api_processes(sort: str = "cpu", limit: int = 40) -> dict[str, Any]:
        return _processes_snapshot(sort=sort, limit=max(1, min(int(limit), 200)))

    @app.get("/api/voice", dependencies=auth)
    def api_voice(probe: bool = False) -> dict[str, Any]:
        # `probe` records from the microphone, so it is opt-in rather than
        # something a page refresh triggers.
        return _voice_snapshot(probe=probe)

    @app.get("/api/jobs", dependencies=auth)
    def api_jobs(history: int = 30) -> dict[str, Any]:
        return _jobs_snapshot(history=max(0, min(int(history), 200)))

    # Posted by the scheduled-jobs tick, which runs in its own process and has
    # no other way to reach a browser. Token-gated like everything else; the
    # Host/Origin middleware applies too, and the tick sends a loopback Host.
    @app.post("/api/job-event", dependencies=auth)
    async def api_job_event(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise _unauthorized("Expected a JSON object")
        reached = events.broadcast(
            "job_event",
            id=str(payload.get("id", "")),
            title=str(payload.get("title", "")),
            text=str(payload.get("text", "")),
            failed=bool(payload.get("failed", False)),
        )
        audit.record("web.job_event", id=payload.get("id", ""), clients=reached)
        return {"delivered": reached}

    @app.get("/api/habits", dependencies=auth)
    def api_habits(history: int = 30) -> dict[str, Any]:
        return _habits_snapshot(history=max(0, min(int(history), 200)))

    # Posted by the routine pass inside the same tick, for the same reason as
    # `/api/job-event`: a short-lived process with no way to reach a browser.
    @app.post("/api/routine-event", dependencies=auth)
    async def api_routine_event(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise _unauthorized("Expected a JSON object")
        reached = events.broadcast(
            "routine_event",
            routine=str(payload.get("routine", "")),
            step=str(payload.get("step", "")),
            label=str(payload.get("label", "")),
            text=str(payload.get("text", "")),
            nag=int(payload.get("nag", 0) or 0),
        )
        audit.record("web.routine_event", routine=payload.get("routine", ""),
                     step=payload.get("step", ""), clients=reached)
        return {"delivered": reached}

    @app.get("/api/whatsapp", dependencies=auth)
    def api_whatsapp() -> dict[str, Any]:
        return _whatsapp_state()

    @app.post("/api/whatsapp/link", dependencies=auth)
    def api_whatsapp_link() -> dict[str, Any]:
        """Ask the resident service to begin pairing.

        Only ever starts the flow. The QR itself is read back through
        `/api/whatsapp`, so a code never travels as the answer to a POST that
        something might log.
        """
        from .platform import whatsapp

        result = whatsapp.begin_link()
        audit.record("web.whatsapp_link", ok=bool(result.get("ok")))
        return result

    @app.get("/api/settings", dependencies=auth)
    def api_settings() -> dict[str, Any]:
        return {"settings": _redacted_settings()}

    @app.post("/api/settings", dependencies=auth)
    async def api_settings_patch(request: Request) -> dict[str, Any]:
        patch = await request.json()
        if not isinstance(patch, dict):
            raise _unauthorized("Expected a JSON object")
        # The confirmation policy has exactly one write path
        # (/api/safety/policy), which goes through safety.set_confirmation_mode.
        # A generic write here would skip that, and a non-object `safety` would
        # replace the whole section and silently drop a "decline".
        if "safety" in patch:
            section = patch["safety"]
            if not isinstance(section, dict) or _POLICY_KEYS & set(section):
                return JSONResponse(
                    {"detail": "The confirmation policy is changed with /api/safety/policy"},
                    status_code=400,
                )
        current = config.load_settings()
        merged = _deep_merge(current, patch)
        config.save_settings(merged)
        audit.record("web.settings_updated", keys=sorted(patch.keys()))
        return {"settings": _redacted_settings()}

    # -- confirmation policy (ask / decline / auto-approve) -----------------

    @app.get("/api/safety/policy", dependencies=auth)
    def api_policy() -> dict[str, Any]:
        return safety.confirmation_policy()

    @app.post("/api/safety/policy", dependencies=auth)
    async def api_policy_set(request: Request) -> Any:
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"detail": "Expected a JSON object"}, status_code=400)
        seconds = body.get("seconds")
        # bool is an int in Python; "seconds": true must not mean one second.
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int)):
            return JSONResponse({"detail": "seconds must be a whole number"}, status_code=400)
        try:
            return safety.set_confirmation_mode(body.get("mode"), seconds, by="web")
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.post("/api/safety/auto/off", dependencies=auth)
    def api_policy_auto_off() -> dict[str, Any]:
        return safety.disable_auto_approve(by="web")

    # -- phone channel: owner-side management (browser token) -------------

    @app.post("/api/phone/offer", dependencies=auth)
    def api_phone_offer(request: Request) -> dict[str, Any]:
        host = app.state.public_host or (request.url.hostname or "127.0.0.1")
        port = app.state.public_port or (request.url.port or DEFAULT_PORT)
        fingerprint = app.state.cert_sha256
        offer = phone.create_offer(
            host, port,
            scheme="https" if fingerprint else "http",
            cert_sha256=fingerprint,
        )
        # A phone cannot reach a loopback address. Say so instead of handing
        # out a QR that can never work.
        return {"offer": offer, "reachable_from_lan": not _is_loopback(host)}

    @app.get("/api/phones", dependencies=auth)
    def api_phones() -> dict[str, Any]:
        return {"devices": phone.list_devices()}

    @app.delete("/api/phones/{device_id}", dependencies=auth)
    def api_phone_revoke(device_id: str) -> dict[str, Any]:
        if not phone.revoke(device_id):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Unknown device")
        return {"revoked": device_id}

    # -- phone channel: the phone's side (device credential, never the token)

    @app.post("/phone/pair")
    async def phone_pair(request: Request) -> Any:
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"detail": "Expected a JSON object"}, status_code=400)
        try:
            credential = phone.redeem_offer(body.get("code"), body.get("device_name"))
        except phone.PhoneError as exc:
            return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=403)
        return credential

    @app.post("/phone/ui-session")
    async def phone_ui_session(request: Request) -> Any:
        # Origin is already refused for every /phone/* by the middleware.
        device = phone.authenticate(request.headers.get("authorization"))
        if device is None:
            return JSONResponse({"detail": "Invalid device credential"}, status_code=401)
        token, expires, ttl = webauth.issue_ui_token(device["device_id"])
        # The device id, never the token: the audit log must not hold a bearer.
        audit.record("phone.ui_session", device_id=device["device_id"], ttl=ttl)
        return {"token": token, "expires_at": int(expires), "ttl_seconds": int(ttl)}

    @app.websocket("/phone/ws")
    async def phone_ws(socket: WebSocket) -> None:
        headers = socket.headers
        # The HTTP middleware does not see websocket handshakes, so the same
        # checks are repeated here, all before accept().
        if not webauth.host_allowed(headers.get("host")) or headers.get("origin"):
            await socket.close(code=1008)
            return
        device = phone.authenticate(headers.get("authorization"))
        if device is None:
            await socket.close(code=1008)
            return

        await socket.accept()
        link = phone.PhoneLink(device, asyncio.get_running_loop())
        phone.attach(link)

        async def pump() -> None:
            while True:
                payload = await link.next_frame()
                if payload is None:
                    # Revoked, replaced, protocol mismatch or oversized frame.
                    with contextlib.suppress(Exception):
                        await socket.close(code=1008)
                    return
                await socket.send_text(json.dumps(payload))

        pump_task = asyncio.create_task(pump())
        reason = "disconnected"
        try:
            while not link.closed:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                text = message.get("text")
                if text is None:
                    text = (message.get("bytes") or b"").decode("utf-8", "replace")
                link.handle_text(text)
        except Exception as exc:  # a broken socket must still fail pending calls
            reason = f"socket error: {type(exc).__name__}"
        finally:
            phone.detach(link, reason)
            # Let the pump deliver the final error frame and close, then stop it.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(pump_task, timeout=1.0)
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump_task

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
        if not webauth.token_accepted(supplied, app.state.token):
            # Close before accepting: never hand an unauthenticated peer an
            # open socket, not even briefly.
            await socket.close(code=1008)
            return

        await socket.accept()
        loop = asyncio.get_running_loop()
        ui_watch: asyncio.Task | None = None
        if not webauth.verify_token(supplied, app.state.token):
            # Opened with a phone's UI token: end the socket when that token
            # is revoked or expires, so a revoked phone loses a live session
            # and not just future ones.
            gone = asyncio.Event()
            webauth.on_ui_token_revoked(
                supplied, lambda: loop.call_soon_threadsafe(gone.set)
            )
            expiry = webauth.ui_token_expiry(supplied) or 0.0

            async def watch_ui_token() -> None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(gone.wait(), max(0.0, expiry - webauth._now()))
                with contextlib.suppress(Exception):
                    await socket.close(code=1008)

            ui_watch = asyncio.create_task(watch_ui_token())
        session = events.Session(loop)
        events.register(session)
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
        peer = socket.client.host if socket.client else ""
        # Whether this page is on the laptop itself. The phone app also loads
        # 127.0.0.1 (a loopback pipe on the phone), so the page cannot tell;
        # the server sees the phone's LAN address and can.
        session.emit(
            "hello", **_hello_payload(), local_client=_is_loopback_peer(peer)
        )

        # The JSON pump and the audio sender both write to this socket.
        send_lock = asyncio.Lock()

        async def pump() -> None:
            """Drain the outbound queue onto the socket."""
            while True:
                payload = await session.next_frame()
                async with send_lock:
                    await socket.send_text(json.dumps(payload, default=str))

        pump_task = asyncio.create_task(pump())

        audio_out = _AudioOut()

        async def audio_sender() -> None:
            """Write device-voice audio to the socket, in order. A slow client
            costs dropped audio (see `_AudioOut`), never a stalled loop."""
            while True:
                item = await audio_out.get()
                async with send_lock:
                    if item is _CLEAR:
                        await socket.send_text(json.dumps({"t": "audio_clear"}))
                    else:
                        await socket.send_bytes(item)

        audio_task = asyncio.create_task(audio_sender())

        # One stop flag per turn. The agent loop runs on a worker thread, so
        # cancelling the asyncio task around it does not reach the loop — the
        # thread carries on through every remaining tool round. This is the
        # signal it actually checks.
        stop_turn = threading.Event()

        async def run_turn(turn_id: str, text: str) -> None:
            session.turn_id = turn_id
            stop_turn.clear()
            session.emit("turn_start", id=turn_id)
            try:
                turn = await asyncio.to_thread(agent.ask, text, stop_turn)
                session.emit(
                    "turn_end", id=turn_id,
                    iterations=turn.iterations,
                    tool_calls=turn.tool_calls,
                    error=turn.error,
                    cancelled=turn.cancelled,
                )
            except Exception as exc:  # never let one bad turn kill the socket
                audit.record("web.turn_failed", error=f"{type(exc).__name__}: {exc}")
                session.emit("turn_end", id=turn_id, iterations=0, error=str(exc))
            finally:
                busy.clear()

        def _dispatch(skill: str, args: dict) -> str:
            """Run one skill on the worker thread, with confirmations wired.

            The UI panels drive skills directly — no model, no tokens. The
            confirmer must still be installed, or `safety.enforce()` falls back
            to deny and every destructive panel button silently fails. Using
            the same `session.confirm` as the agent loop means a panel-issued
            `kill_process` raises the identical confirmation card, and the
            per-turn denial memory applies here too.
            """
            with confirmer_scope(session.confirm):
                return registry.dispatch(skill, args, source="web-panel")

        async def run_skill(turn_id: str, skill: str, args: dict) -> None:
            session.turn_id = turn_id
            session.emit("turn_start", id=turn_id, skill=skill)
            call_id = f"{turn_id}-call"
            session.emit(
                "tool_call", id=turn_id, call_id=call_id, name=skill,
                args=args, display=skill,
            )
            try:
                result = await asyncio.to_thread(_dispatch, skill, args)
                if skill == "open_app":
                    await asyncio.to_thread(
                        _record_launch, str(args.get("app_name") or "")
                    )
                session.emit(
                    "tool_result", id=turn_id, call_id=call_id,
                    name=skill, result=result,
                )
                session.emit("turn_end", id=turn_id, iterations=1, tool_calls=[skill])
            except Exception as exc:
                audit.record(
                    "web.skill_failed", skill=skill,
                    error=f"{type(exc).__name__}: {exc}",
                )
                session.emit(
                    "tool_result", id=turn_id, call_id=call_id,
                    name=skill, result=str(exc),
                )
                session.emit("turn_end", id=turn_id, iterations=1, error=str(exc))
            finally:
                busy.clear()

        # -- voice ---------------------------------------------------------
        #
        # The voice pipeline records from *this process's* microphone and speaks
        # through its speakers. Since the server binds loopback only, that is
        # the same machine the browser is on — so the browser is the control
        # surface and live transcript, and the whole tested `VoiceSession` loop
        # is reused rather than reimplemented over the socket. Capturing audio
        # in the browser and shipping it here would duplicate a working
        # pipeline to reach the same devices.
        #
        # (This is exactly the assumption that breaks for remote access, which
        # is why phone support stays a separate piece of work.)
        voice_box: dict[str, Any] = {"session": None, "thread": None, "net": None}

        def voice_observer(kind: str, text: str) -> None:
            # Voice turns deliberately never enter `messages` — the text
            # console keeps its own history and must not be rewritten by a
            # parallel spoken conversation.
            session.emit("voice_state", phase=kind, text=text)

        voice_turn = {"n": 0}

        def voice_confirmer(action: str, detail: str, danger: Any) -> bool:
            """Confirm a destructive voice action in the browser, not out loud.

            `VoiceSession` would otherwise ask by speaking a summary and
            listening for "yes". A card wins here: it shows the exact target,
            which a spoken summary cannot, and it avoids running two
            independent confirmers that could both try to answer. A short
            spoken nudge tells a hands-free user to look at the screen.
            """
            from .platform import tts as _tts

            # Voice frames carried whatever turn id was last used by a typed
            # request — usually none at all — so the confirmation card matched
            # no message on the client and was dropped without a trace. The
            # worker then blocked until it timed out and denied, which looked
            # like the whole session freezing. A distinct id routes it to the
            # global overlay instead.
            voice_turn["n"] += 1
            session.turn_id = f"v{voice_turn['n']}"

            # And say it in the transcript, not only out loud — the panel
            # otherwise showed the tool call and then nothing.
            session.emit(
                "voice_state", phase="info",
                text=f"Waiting for your confirmation: {action}",
            )
            # The laptop speaker is the wrong place to say it when the owner is
            # on the phone; the card (also relayed to phones) is the prompt.
            if voice_box.get("net") is None:
                with contextlib.suppress(Exception):
                    _tts.speak("I need your confirmation.", blocking=True)
            return session.confirm(action, detail, danger)

        def audio_reader(net: Any) -> None:
            """Move the session's output audio onto the loop until it ends.

            Its own thread because `pull_output` blocks; it exits when the
            session closes `net` (CLOSED), so nothing is left behind.
            """
            from .live import Control

            while True:
                item = net.pull_output(0.5)
                if item is Control.CLOSED:
                    return
                if item is None:
                    continue
                try:
                    if item is Control.CLEAR:
                        loop.call_soon_threadsafe(audio_out.clear)
                    else:
                        loop.call_soon_threadsafe(audio_out.put, item)
                except RuntimeError:  # the loop is gone, and the socket with it
                    return

        def voice_worker(mode: str, net: Any = None) -> None:
            from . import voice as voice_mod

            reader: threading.Thread | None = None
            try:
                if mode == "live":
                    ok, why = voice_mod.live_available()
                    if not ok:
                        session.emit("voice_state", phase="error", text=why)
                        return
                    # LiveSession lives in live.py, not voice.py.
                    from .live import LiveSession

                    if net is not None:
                        live = LiveSession(
                            observer=voice_observer, confirmer=voice_confirmer,
                            source=net, sink=net,
                        )
                        reader = threading.Thread(
                            target=audio_reader, args=(net,), daemon=True,
                            name="kravein-web-voice-out",
                        )
                        reader.start()
                    else:
                        live = LiveSession(
                            observer=voice_observer, confirmer=voice_confirmer
                        )
                    voice_box["session"] = live
                    session.emit(
                        "voice_state", phase="started", text="live",
                        audio="device" if net is not None else "laptop",
                    )
                    live.run()
                else:
                    convo = voice_mod.VoiceSession(
                        observer=voice_observer,
                        confirmer=voice_confirmer,
                    )
                    voice_box["session"] = convo
                    session.emit("voice_state", phase="started", text="local")
                    convo.run()
            except Exception as exc:
                audit.record(
                    "web.voice_failed", error=f"{type(exc).__name__}: {exc}"
                )
                session.emit("voice_state", phase="error", text=str(exc))
            finally:
                # Closing `net` wakes the reader (CLOSED) and ends the input
                # side even if `run()` raised before it ever started.
                if net is not None:
                    net.close()
                if reader is not None:
                    reader.join(timeout=2.0)
                voice_box["session"] = None
                voice_box["thread"] = None
                voice_box["net"] = None
                if net is not None:
                    # Playback the client still holds is stale once we stop.
                    with contextlib.suppress(RuntimeError):
                        loop.call_soon_threadsafe(audio_out.clear)
                session.emit("voice_state", phase="stopped", text="")

        def stop_voice() -> bool:
            """Ask the voice loop to end. Returns whether one was running."""
            active = voice_box.get("session")
            running = active is not None or voice_box.get("thread") is not None
            if active is not None:
                with contextlib.suppress(Exception):
                    active.stop()
            net = voice_box.get("net")
            if net is not None:
                # Also covers a stop that lands before the session exists.
                with contextlib.suppress(Exception):
                    net.close()
            with contextlib.suppress(Exception):
                from .platform import tts as _tts

                _tts.stop()
            return running

        turn_task: asyncio.Task | None = None
        try:
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                chunk = message.get("bytes")
                if chunk is not None:
                    # Device-voice microphone audio. A stream, so no reply per
                    # frame; with no device session (stopped, or a late frame)
                    # it is dropped. `push_input` never blocks and drops the
                    # oldest audio when the session falls behind.
                    net = voice_box.get("net")
                    if net is not None and len(chunk) <= MAX_AUDIO_FRAME:
                        net.push_input(chunk)
                    continue
                raw = message.get("text") or ""
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

                elif kind == "run":
                    skill = str(msg.get("skill") or "").strip()
                    args = msg.get("args")
                    turn_id = str(msg.get("id") or "")
                    if not skill:
                        continue
                    if not isinstance(args, dict):
                        args = {}
                    if busy.is_set():
                        session.emit(
                            "error", id=turn_id,
                            message="A request is already running.",
                        )
                        continue
                    busy.set()
                    turn_task = asyncio.create_task(run_skill(turn_id, skill, args))

                elif kind == "voice_start":
                    from . import voice as voice_mod

                    if voice_box["thread"] is not None:
                        session.emit(
                            "voice_state", phase="error",
                            text="A voice session is already running.",
                        )
                        continue
                    # The browser asks for a preference, not a pipeline. Only
                    # the server knows what is actually installed and keyed, so
                    # it decides — and the `started` frame reports which one
                    # really ran. The client used to pick from a readiness
                    # snapshot and then assume it got what it asked for, which
                    # both disabled voice on live-only machines and left the UI
                    # labelling the session with a mode it might not be in.
                    use_device = msg.get("audio") == "device"
                    requested = str(msg.get("mode") or "")
                    if use_device and requested == "local":
                        session.emit(
                            "voice_state", phase="error",
                            text="This device's mic and speaker need Live voice.",
                        )
                        continue
                    # Device audio is Live-only (`NetworkAudio` feeds a
                    # LiveSession). Asking for it forces `live`, so a missing
                    # key is a refusal, not a quiet fall back to the laptop mic.
                    mode, problem = voice_mod.resolve_mode(
                        "live" if use_device else requested
                    )
                    if not mode:
                        session.emit("voice_state", phase="error", text=problem)
                        continue
                    audit.record(
                        "web.voice_start", mode=mode,
                        audio="device" if use_device else "laptop",
                    )
                    net = None
                    if use_device:
                        from .live import NetworkAudio

                        net = NetworkAudio()
                        voice_box["net"] = net
                    worker = threading.Thread(
                        target=voice_worker, args=(mode, net), daemon=True,
                        name="kravein-web-voice",
                    )
                    voice_box["thread"] = worker
                    worker.start()

                elif kind == "voice_stop":
                    audit.record("web.voice_stop")
                    # Always answer. When a session is running its worker emits
                    # `stopped` from its finally; when none is, nothing would
                    # have replied at all, and a client that shows "Stopping…"
                    # until confirmation stayed stuck there for good.
                    if not stop_voice():
                        session.emit("voice_state", phase="stopped", text="")

                elif kind == "confirm_reply":
                    ok = session.resolve_confirm(
                        str(msg.get("confirm_id") or ""), bool(msg.get("approve"))
                    )
                    if not ok:
                        session.emit(
                            "error",
                            message="That confirmation already expired.",
                        )

                elif kind == "cancel":
                    # Always answer, even with nothing running. A client that
                    # shows "Stopping…" until the server confirms would wait
                    # for ever otherwise — the same failure the voice toggle
                    # had.
                    if not busy.is_set():
                        session.emit("cancelled", id="", pending_confirms=0)
                        continue
                    stop_turn.set()
                    # A turn parked on a confirmation is not looping; it is
                    # blocked. Answering the card is what lets it reach the
                    # next stop check at all.
                    released = session.deny_pending()
                    audit.record("web.cancel", released_confirms=released)
                    session.emit(
                        "cancelled", id=session.turn_id or "",
                        pending_confirms=released,
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
            # A voice session holds the microphone and can be mid-utterance;
            # leaving it running after the browser closes would keep recording
            # with nothing listening.
            stop_voice()
            events.unregister(session)
            session.close()
            if ui_watch is not None:
                ui_watch.cancel()
            pump_task.cancel()
            audio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await audio_task
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
# Device voice (the browser's mic and speaker)
# ---------------------------------------------------------------------------

# One microphone frame is ~100 ms (3.2 KB); anything far larger is not ours.
MAX_AUDIO_FRAME = 64 * 1024

_CLEAR = object()   # barge-in marker, kept in order with the audio


class _AudioOut:
    """Bounded, drop-oldest hand-off from the reader thread to the sender task.

    Only touched on the event loop (the reader goes through
    `call_soon_threadsafe`), so a slow client costs old audio, never a stalled
    loop or unbounded memory.
    """

    MAX_ITEMS = 200

    def __init__(self) -> None:
        self._items: "collections.deque[Any]" = collections.deque(maxlen=self.MAX_ITEMS)
        self._ready = asyncio.Event()

    def put(self, item: bytes) -> None:
        self._items.append(item)
        self._ready.set()

    def clear(self) -> None:
        """Barge-in: audio not sent yet is stale. The marker tells the client
        to drop what it already buffered."""
        self._items.clear()
        self._items.append(_CLEAR)
        self._ready.set()

    async def get(self) -> Any:
        while not self._items:
            self._ready.clear()
            await self._ready.wait()
        return self._items.popleft()


def _is_loopback_peer(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
        "assistant_name": config.get("assistant_name", "Kravein"),
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
    import socket as _socket

    hostname = ""
    with contextlib.suppress(Exception):
        hostname = _socket.gethostname()

    return {
        "assistant_name": config.get("assistant_name", "Kravein"),
        "hostname": hostname,
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


def _windows_snapshot() -> dict[str, Any]:
    from .platform import desktop

    try:
        backend = desktop.backend()
        if not backend.available():
            return {"available": False, "backend": "", "windows": [], "workspaces": []}
        windows = backend.list_windows()
        return {
            "available": True,
            "backend": type(backend).__name__.replace("Backend", "").lower(),
            "workspaces": backend.list_workspaces(),
            "windows": [desktop.window_record(w) for w in windows],
        }
    except Exception as exc:
        return {"available": False, "error": str(exc), "windows": [], "workspaces": []}


# How often each app was launched from the panel, so the launcher can list the
# ones the owner actually uses first. Keyed by the desktop entry's exact name:
# a free-text `open_app` ("the browser") is not counted, otherwise a typo would
# create a "frequent app" that exists nowhere in the list.
_LAUNCH_LOCK = threading.Lock()
FREQUENT_APPS = 5


def _launch_file() -> Path:
    return config.DATA_DIR / "app_launches.json"


def _launch_counts() -> dict[str, int]:
    try:
        raw = json.loads(_launch_file().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): v for k, v in raw.items()
        if isinstance(v, int) and not isinstance(v, bool) and v > 0
    }


def _record_launch(app_name: str) -> None:
    """Count one launch of `app_name` if it is exactly an installed entry."""
    from .platform import apps

    with contextlib.suppress(Exception):
        names = {e.name for e in apps.index() if not e.no_display}
        if app_name not in names:
            return
        with _LAUNCH_LOCK:
            counts = _launch_counts()
            counts[app_name] = counts.get(app_name, 0) + 1
            path = _launch_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(counts))
            tmp.replace(path)


def _apps_snapshot() -> dict[str, Any]:
    from .platform import apps

    try:
        entries = apps.index()
    except Exception as exc:
        return {"available": False, "error": str(exc), "apps": []}
    counts = _launch_counts()
    shown = {e.name for e in entries if not e.no_display}
    return {
        "available": True,
        "frequent": [
            name for name, _ in sorted(
                counts.items(), key=lambda kv: (-kv[1], kv[0].lower())
            )
            if name in shown
        ][:FREQUENT_APPS],
        "apps": [
            {
                "name": e.name,
                "id": e.id,
                "icon": e.icon,
                "exec": e.exec_cmd,
                "launches": counts.get(e.name, 0),
                "comment": e.comment or e.generic_name,
                "categories": list(e.categories),
                "terminal": e.terminal,
                "running": False,   # probing every entry would be far too slow
            }
            for e in entries
            if not e.no_display
        ],
    }


# psutil's cpu_percent measures usage *since the previous call on the same
# object*, so the very first call always returns 0.0. Without priming, a
# freshly started server served a process table where every row was 0.0 —
# "sort by CPU" degenerated to PID order and the screen filled with idle
# kernel threads. Keeping the Process objects alive between requests is what
# makes the reading real; rebuilding them each time resets the measurement.
_PROC_CACHE: dict[int, Any] = {}
_PROC_LOCK = threading.Lock()


def prime_cpu_counters() -> None:
    """Take the throwaway first sample so the first real request has numbers."""
    try:
        import psutil
    except ImportError:
        return
    with contextlib.suppress(Exception):
        psutil.cpu_percent(interval=None)
    with _PROC_LOCK:
        for proc in psutil.process_iter():
            with contextlib.suppress(Exception):
                proc.cpu_percent(interval=None)
                _PROC_CACHE[proc.pid] = proc


def _processes_snapshot(*, sort: str = "cpu", limit: int = 40) -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return {"available": False, "processes": []}

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    with _PROC_LOCK:
        for proc in psutil.process_iter(["pid"]):
            pid = proc.info.get("pid")
            if pid is None:
                continue
            seen.add(pid)
            # Reuse the cached object so cpu_percent has a previous sample to
            # measure against. A brand-new process legitimately reads 0.0 once.
            cached = _PROC_CACHE.get(pid)
            if cached is None:
                cached = proc
                _PROC_CACHE[pid] = proc
            try:
                with cached.oneshot():
                    rows.append({
                        "pid": pid,
                        "name": cached.name(),
                        "user": cached.username(),
                        "cpu": round(cached.cpu_percent(interval=None), 1),
                        "memory": round(cached.memory_percent(), 1),
                        "status": cached.status(),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                _PROC_CACHE.pop(pid, None)
                continue

        # Drop cache entries for processes that have exited, or the dict grows
        # without bound on a long-running server.
        for dead in [pid for pid in _PROC_CACHE if pid not in seen]:
            _PROC_CACHE.pop(dead, None)

    key = "memory" if sort == "memory" else "cpu"
    rows.sort(key=lambda r: (r[key], r["cpu"]), reverse=True)
    return {"available": True, "total": len(rows), "processes": rows[:limit]}


def _jobs_snapshot(*, history: int = 30) -> dict[str, Any]:
    """Scheduled jobs as records, not prose.

    Same rule as the windows and process panels: a UI that needs real data
    reads the store directly rather than regex-parsing a skill's output. The
    skill's wording exists to be read by a model and is free to change.
    """
    import json as _json

    from . import jobs as jobs_mod

    snapshot: dict[str, Any] = {
        "timer_active": False,
        "timezone": jobs_mod.local_timezone(),
        "grace_seconds": int(
            config.get("jobs.grace_seconds", jobs_mod.DEFAULT_GRACE_SECONDS)
            or jobs_mod.DEFAULT_GRACE_SECONDS
        ),
        "now": jobs_mod.iso(jobs_mod.now_utc()),
        "jobs": [],
        "history": [],
    }

    # Whether the timer is running is the single most important fact here: a
    # job list that cannot fire looks identical to one that can.
    with contextlib.suppress(Exception):
        from .platform import shell

        if shell.have("systemctl"):
            result = shell.run(
                ["systemctl", "--user", "is-active", "kravein-jobs.timer"], timeout=8
            )
            snapshot["timer_active"] = result.stdout.strip() == "active"

    with contextlib.suppress(Exception):
        snapshot["jobs"] = jobs_mod.all_jobs()

    if history:
        with contextlib.suppress(Exception):
            lines = jobs_mod.HISTORY.read_text(encoding="utf-8").splitlines()
            for raw in lines[-history:]:
                with contextlib.suppress(ValueError):
                    snapshot["history"].append(_json.loads(raw))
            snapshot["history"].reverse()

    return snapshot


def _habits_snapshot(*, history: int = 30) -> dict[str, Any]:
    """Routines, today's instance and recent days — records, not prose.

    `routines.snapshot()` rolls the day in memory without saving, so a panel
    refresh always shows the current day even if no tick has run since the
    boundary, and cannot itself close a day.
    """
    from . import routines as routines_mod

    snapshot: dict[str, Any] = {
        "timer_active": False,
        "now": routines_mod.iso(routines_mod.now_utc()),
        "timezone": routines_mod.local_timezone(),
        "streak_threshold": routines_mod.STREAK_THRESHOLD,
        "routines": [],
        "history": [],
        "whatsapp": {"state": "unavailable"},
    }

    # The timer's state comes first for the same reason it does on the jobs
    # panel: a routine that cannot nag looks identical to one that can.
    with contextlib.suppress(Exception):
        from .platform import shell

        if shell.have("systemctl"):
            result = shell.run(
                ["systemctl", "--user", "is-active", "kravein-jobs.timer"], timeout=8
            )
            snapshot["timer_active"] = result.stdout.strip() == "active"

    with contextlib.suppress(Exception):
        snapshot.update(routines_mod.snapshot())

    if history:
        with contextlib.suppress(Exception):
            snapshot["history"] = routines_mod.history(limit=history)

    with contextlib.suppress(Exception):
        snapshot["whatsapp"] = _whatsapp_state()

    return snapshot


def _whatsapp_state() -> dict[str, Any]:
    """Connection state, with the pairing code already rendered to squares.

    The raw code stays out of the payload: the browser only ever needs
    something to draw, and a credential that is never serialised in a form
    anything else understands is one fewer place it can leak from.
    """
    from .platform import whatsapp

    state = dict(whatsapp.state())
    code = str(state.pop("qr", ""))
    state["qr_modules"] = whatsapp.qr_modules(code) if code else []
    return state


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
    tls: bool = True,
    follow: str | None = None,
) -> int:
    """Run the web server until interrupted.

    `follow` (`auto` or an address) makes a LAN server track the machine's
    address: when the network changes the listener is restarted on the new one.
    """
    try:
        import uvicorn
    except ImportError:
        print("The web server needs fastapi and uvicorn:")
        print("  .venv/bin/pip install fastapi uvicorn")
        return 1

    loopback = host in ("127.0.0.1", "localhost", "::1")
    reachable = host

    if not loopback:
        # LAN exposure is opt-in and narrow. `0.0.0.0` means "every interface",
        # which on a laptop can include a public one — that would put a machine
        # with 144 skills and a shell on the open internet. Bind one address
        # this machine actually holds, and only a private one.
        if host in ("0.0.0.0", "::"):
            print(
                "Refusing to bind every interface. Name the address to serve "
                "on, or use --lan to pick this machine's private one:"
            )
            for address in webauth.local_addresses():
                print(f"  --host {address}")
            return 1
        if not webauth.is_private_address(host):
            print(
                f"Refusing to bind {host}: only private addresses "
                "(loopback, 10.x, 172.16-31.x, 192.168.x, link-local) are allowed."
            )
            return 1
        if host not in webauth.local_addresses():
            print(f"Refusing to bind {host}: this machine does not hold that address.")
            return 1

        # Teach the Host and Origin checks about the address we are serving,
        # so they keep rejecting everything else. Adding the literal IP — not a
        # hostname, not a wildcard — is what preserves the rebinding defence.
        webauth.allow_lan_host(host)

    # TLS only when exposed on the LAN: loopback never leaves the machine, and
    # a browser warning on a self-signed cert there would buy nothing.
    use_tls = tls and not loopback
    ssl_args: dict[str, Any] = {}
    fingerprint = None
    if use_tls:
        cert_path, key_path, fingerprint, regenerated = webauth.ensure_tls_cert(host)
        ssl_args = {"ssl_certfile": str(cert_path), "ssl_keyfile": str(key_path)}
        if regenerated:
            print(f"Generated a new certificate for {host}.")
            print("  Its fingerprint changed: phones paired before must pair again.")

    token = webauth.issue_token()
    url = f"{'https' if use_tls else 'http'}://{reachable}:{port}/?token={token}"

    registry.load_skills()
    # Take the throwaway first CPU sample now rather than serving zeros to the
    # first request that asks.
    prime_cpu_counters()
    app = create_app(token)
    app.state.public_host = reachable
    app.state.public_port = port
    app.state.cert_sha256 = fingerprint

    print(f"Kravein web UI on {url}")
    if fingerprint:
        print(f"Certificate SHA-256: {fingerprint}")
    print(f"Token stored at {webauth.token_path()}")
    if not WEB_DIST.is_dir():
        print("(frontend not built yet — API only)")

    if not loopback:
        # Said out loud, every time. The token and everything after it cross
        # the network in the clear, which is fine on a WPA-protected home
        # network and is not fine on open or shared wifi. Someone opening this
        # on café wifi should not have to infer that from the docs.
        print()
        print("  LAN mode — reachable from other devices on this network.")
        print("  Anyone who has the URL above has full control of this machine.")
        if use_tls:
            print("  Traffic is encrypted with a self-signed certificate; paired phones")
            print("  pin its fingerprint. A browser will warn about it once.")
        else:
            print("  Traffic is plain HTTP: do not use this on open or shared wifi.")
        print(f"  Revoke by restarting without --lan, or delete {webauth.token_path()}")
        print()

    if open_browser and loopback:
        import webbrowser

        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    audit.record("web.server_started", host=host, port=port, tls=use_tls)
    if loopback:
        uvicorn.run(app, host=host, port=port, log_level="warning", **ssl_args)
        return 0
    return _serve_following(uvicorn, app, host, port, ssl_args, fingerprint, follow)


def _serve_following(uvicorn, app, host, port, ssl_args, fingerprint, follow) -> int:
    """Run the LAN listener, rebinding when the machine's address changes.

    The certificate and token stay the same (phones pin the fingerprint, not
    the address); only the bind address, the Host allowlist and the mDNS
    announcement move. Restarting uvicorn in-process, instead of exiting and
    leaning on systemd, keeps `kravein serve --lan` working the same by hand.
    """
    import time

    advertiser = discovery.Advertiser(port, fingerprint)
    try:
        while True:
            moved: dict[str, Any] = {}
            server = uvicorn.Server(
                uvicorn.Config(app, host=host, port=port, log_level="warning", **ssl_args)
            )

            def on_change(new: Optional[str], server=server, moved=moved) -> None:
                moved["to"] = new
                server.should_exit = True

            watcher = None
            if follow:
                watcher = discovery.AddressWatcher(follow, host, on_change)
                watcher.start()
            advertiser.advertise(host)
            try:
                server.run()
            finally:
                if watcher:
                    watcher.stop()
                advertiser.close()
            if "to" not in moved:
                return 0
            audit.record("web.address_changed", old=host, new=moved["to"])
            new = moved["to"]
            while new is None:                    # no network: wait for one
                print("Network address lost; waiting for one...")
                time.sleep(discovery.POLL_SECONDS)
                new = discovery.pick_address(follow)
            print(f"Network address changed: {host} -> {new}")
            host = new
            webauth.clear_lan_hosts()
            webauth.allow_lan_host(host)
            app.state.public_host = host
    except KeyboardInterrupt:
        return 0
