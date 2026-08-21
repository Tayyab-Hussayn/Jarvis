"""Background service mode.

Listens on a Unix domain socket in ``$XDG_RUNTIME_DIR``, so only the owning
user can talk to it (the socket is created 0600 inside a 0700 directory).
This replaces the Windows build's autostart-via-registry approach; here it
runs as a systemd **user** service, which is the correct scope for something
that needs the user's session bus, audio and compositor.

The daemon has no terminal, so it cannot prompt for confirmation. Destructive
actions are therefore refused unless the request explicitly sets
``confirm: true`` — a deliberate choice, so a background process can never
quietly delete something.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import threading
from pathlib import Path
from typing import Any

from . import audit, config, registry
from .agent import Agent
from .safety import Danger

MAX_REQUEST_BYTES = 256 * 1024


class _Handler(socketserver.StreamRequestHandler):
    """One request per connection, newline-delimited JSON."""

    timeout = 300
    server: "_Server"

    def handle(self) -> None:
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES)
        except (OSError, socket.timeout):
            return

        if not raw:
            return

        try:
            request = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            self._reply({"ok": False, "error": f"malformed JSON: {exc}"})
            return

        if not isinstance(request, dict):
            self._reply({"ok": False, "error": "request must be a JSON object"})
            return

        try:
            response = self.server.dispatch(request)
        except Exception as exc:
            audit.record("daemon.handler_error", error=str(exc))
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        self._reply(response)

    def _reply(self, payload: dict[str, Any]) -> None:
        try:
            self.wfile.write((json.dumps(payload, default=str) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (OSError, BrokenPipeError):
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: Path):
        self.path = path
        super().__init__(str(path), _Handler)
        # Only the owner may talk to the assistant.
        os.chmod(path, 0o600)

        self._agents: dict[str, Agent] = {}
        self._agents_lock = threading.Lock()

    # -- request routing -------------------------------------------------

    def _agent_for(self, session: str) -> Agent:
        with self._agents_lock:
            if session not in self._agents:
                self._agents[session] = Agent(
                    source=f"daemon:{session}",
                    # No TTY, so no confirmer: destructive calls need an
                    # explicit confirm flag in the request instead.
                    confirmer=None,
                )
            return self._agents[session]

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("action", "ask")).lower()

        if action == "ping":
            return {"ok": True, "reply": "pong", "pid": os.getpid()}

        if action == "status":
            return {
                "ok": True,
                "pid": os.getpid(),
                "socket": str(self.path),
                "skills": len(registry.all_skills()),
                "sessions": len(self._agents),
            }

        if action == "skills":
            return {
                "ok": True,
                "skills": [
                    {
                        "name": s.name,
                        "category": s.category,
                        "danger": s.danger.name,
                        "description": s.description,
                        "available": s.available()[0],
                    }
                    for s in sorted(registry.all_skills().values(), key=lambda s: s.name)
                ],
            }

        if action == "reset":
            session = str(request.get("session", "default"))
            with self._agents_lock:
                self._agents.pop(session, None)
            return {"ok": True, "reply": f"session '{session}' cleared"}

        if action == "run":
            name = str(request.get("skill", ""))
            params = request.get("params") or {}
            if not isinstance(params, dict):
                return {"ok": False, "error": "'params' must be an object"}
            confirm = bool(request.get("confirm", False))
            result = registry.dispatch(
                name, params, source="daemon-run", confirm=confirm
            )
            return {"ok": True, "reply": result}

        if action == "ask":
            text = str(request.get("text", "")).strip()
            if not text:
                return {"ok": False, "error": "'text' is required"}

            session = str(request.get("session", "default"))
            agent = self._agent_for(session)

            # A caller can pre-approve destructive work for this one request.
            if request.get("confirm"):
                agent.confirmer = _preapproved
            else:
                agent.confirmer = None

            turn = agent.ask(text)
            return {
                "ok": turn.ok,
                "reply": turn.reply,
                "error": turn.error,
                "tools": turn.tool_calls,
                "iterations": turn.iterations,
            }

        return {"ok": False, "error": f"unknown action '{action}'"}


def _preapproved(action: str, detail: str, danger: Danger) -> bool:
    """Confirmer used when a client sets confirm:true on the request."""
    audit.record("daemon.preapproved", action=action, danger=danger.name)
    return True


def serve(socket_path: str | None = None) -> int:
    """Run the daemon until terminated."""
    config.ensure_dirs()
    path = _resolve_path(socket_path)

    # Clear a socket left behind by an unclean exit, but never clobber a live one.
    if path.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.5)
            probe.connect(str(path))
            probe.close()
            print(f"A daemon is already listening on {path}.")
            return 1
        except OSError:
            try:
                path.unlink()
            except OSError as exc:
                print(f"Could not remove the stale socket {path}: {exc}")
                return 1
        finally:
            probe.close()

    path.parent.mkdir(parents=True, exist_ok=True)

    server = _Server(path)
    skills = len(registry.all_skills())
    audit.record("daemon.start", socket=str(path), skills=skills)
    print(f"Brahma daemon listening on {path} ({skills} skills).")

    stopping = threading.Event()

    def _shutdown(signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print(f"\nReceived signal {signum}; shutting down.")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        audit.record("daemon.stop")
        print("Daemon stopped.")

    return 0


def _resolve_path(override: str | None) -> Path:
    if override:
        return Path(override).expanduser()
    configured = config.get("daemon.socket")
    return Path(configured) if configured else config.SOCKET_PATH


def send_request(
    text: str,
    *,
    socket_path: str | None = None,
    action: str = "ask",
    params: dict[str, Any] | None = None,
    session: str = "default",
    confirm: bool = False,
    timeout: float = 300,
) -> str:
    """Send one request to a running daemon and return its reply."""
    path = _resolve_path(socket_path)

    if not path.exists():
        return (
            f"No daemon socket at {path}.\n"
            "Start it with:  systemctl --user start brahma-echo"
        )

    payload: dict[str, Any] = {"action": action, "session": session, "confirm": confirm}
    if action == "ask":
        payload["text"] = text
    elif action == "run":
        payload["skill"] = text
        payload["params"] = params or {}

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))

        chunks: list[bytes] = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if chunk.endswith(b"\n"):
                break
    except socket.timeout:
        return f"The daemon did not reply within {timeout}s."
    except OSError as exc:
        return f"Could not reach the daemon at {path}: {exc}"
    finally:
        client.close()

    raw = b"".join(chunks).decode("utf-8", "replace").strip()
    if not raw:
        return "The daemon closed the connection without replying."

    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if response.get("reply"):
        return str(response["reply"])
    if response.get("error"):
        return f"Error: {response['error']}"
    return json.dumps(response, indent=2)
