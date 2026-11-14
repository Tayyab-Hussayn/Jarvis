"""MCP connections (#243): one asyncio loop thread, one task per server.

The agent loop is synchronous on worker threads; the SDK is async. Each
server's ``mcp.Client`` is entered and exited inside its own long-lived task
on a private event loop (anyio cancel scopes must close in the task that
opened them); a skill call is submitted to that loop and waited on with a
timeout and the turn's stop flag.

What the server is allowed to do is deliberately small:
- no sampling, elicitation or roots callbacks: a server cannot make Kravein
  call a model or put a question to anyone;
- calls go through ``session.call_tool`` directly, never ``Client.call_tool``,
  which silently re-lists and resends after ``HEADER_MISMATCH``. That error
  means the tool definition changed mid-session, so the call fails, the
  connection is dropped and the next connect re-checks the pins;
- tools are registered only when approved and pin-identical (``bridge``).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import threading
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from .. import audit
from .. import config as kconfig
from . import config, pins

_RECONNECT_GAP = 10.0          # seconds between automatic reconnects of one server
_LIST_PAGE_CAP = 20


class MCPUnavailable(RuntimeError):
    """The server is not connected (disabled, failed, or never approved)."""


class MCPCallFailed(RuntimeError):
    """The call did not complete: timeout, stop, a changed definition, a dead
    connection. The message names which, never the server's own text."""


def reason(exc: BaseException) -> str:
    return type(exc).__name__


class _Conn:
    def __init__(self, name: str, spec: dict[str, Any]) -> None:
        self.name = name
        self.spec = spec
        self.key = json.dumps(spec, sort_keys=True, default=str)
        self.status = "connecting"
        self.error = ""
        self.client: Any = None
        self.proc: Any = None
        self.closing: asyncio.Event | None = None
        self.done = threading.Event()          # the connect attempt is over (ready or failed)
        self.finished = threading.Event()      # the task has ended and its process is killed
        self.listed: dict[str, dict[str, Any]] = {}
        self.report: dict[str, list[str]] = {}
        self.registered: list[str] = []
        self.rejected: dict[str, str] = {}
        self.last_connect = time.monotonic()

    @property
    def timeout(self) -> float:
        try:
            return max(1.0, min(float(self.spec.get("timeout") or config.DEFAULT_TIMEOUT), 600.0))
        except (TypeError, ValueError):
            return config.DEFAULT_TIMEOUT


class Manager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._conns: dict[str, _Conn] = {}
        self._mtime: float | None = None

    # -- lifecycle --------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._thread is None or not self._thread.is_alive():
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, name="kravein-mcp", daemon=True)
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    def ensure_started(self, wait: float | None = None) -> None:
        """Connect every enabled server (if the config changed) and wait a
        bounded time for the first connects, so the turn sees the tools."""
        if not config.path().exists():
            return
        self._ensure_loop()
        self.sync(wait)

    def sync(self, wait: float | None = None) -> None:
        mtime = config.mtime()
        with self._lock:
            unchanged = mtime == self._mtime
            self._mtime = mtime
            if unchanged:
                self._revive()
                pending = []
            else:
                servers = config.servers()
                for name in list(self._conns):
                    spec = servers.get(name)
                    if spec is None or not spec.get("enabled") or \
                            json.dumps(spec, sort_keys=True, default=str) != self._conns[name].key:
                        self._close(name)
                pending = [self._open(name, spec) for name, spec in servers.items()
                           if spec.get("enabled") and name not in self._conns]
        limit = float(kconfig.get("mcp.connect_wait_seconds", 10)) if wait is None else wait
        deadline = time.monotonic() + limit
        for conn in pending:
            conn.done.wait(max(0.0, deadline - time.monotonic()))

    def _revive(self) -> None:
        """Reconnect a server whose connection died, at most every few seconds."""
        servers = config.servers()
        for name, conn in list(self._conns.items()):
            if conn.status in ("error", "closed") and time.monotonic() - conn.last_connect > _RECONNECT_GAP:
                self._conns.pop(name, None)
                spec = servers.get(name)
                if spec and spec.get("enabled"):
                    self._open(name, spec)

    def _open(self, name: str, spec: dict[str, Any]) -> _Conn:
        conn = _Conn(name, spec)
        self._conns[name] = conn
        asyncio.run_coroutine_threadsafe(self._serve(conn), self._ensure_loop())
        return conn

    def _close(self, name: str) -> None:
        conn = self._conns.pop(name, None)
        if conn is None:
            return
        conn.status = "closed"
        # Unregister now, on the caller's thread: a disabled or removed
        # server's tools must be gone when sync() returns, not whenever its
        # task finishes tearing down.
        from . import bridge

        bridge.unregister(conn)
        if conn.closing is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(conn.closing.set)

    def connect_once(self, name: str, wait: float = 30.0) -> _Conn:
        """Connect one server now (``kravein mcp approve``) and return it."""
        spec = config.servers().get(name)
        if spec is None:
            raise ValueError(f"no server '{name}'")
        self._ensure_loop()
        with self._lock:
            self._close(name)
            conn = self._open(name, spec)
        conn.done.wait(wait)
        return conn

    def shutdown(self, wait: float = 5.0) -> None:
        with self._lock:
            conns = list(self._conns.values())
            for name in list(self._conns):
                self._close(name)
        # Wait for each task to END (its exit stack kills the server's process
        # group), not merely to have connected: stopping the loop earlier
        # leaves the servers running as orphans.
        for conn in conns:
            if conn.closing is not None:
                conn.finished.wait(wait)
        with self._lock:
            for conn in conns:
                if not conn.finished.is_set() and conn.proc is not None:
                    from ..platform import shell

                    shell.kill_group(conn.proc, 0.5)
        with self._lock:
            loop, self._loop, self._thread, self._mtime = self._loop, None, None, None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)

    # -- one connection ---------------------------------------------------

    async def _transport(self, conn: _Conn, stack: AsyncExitStack) -> Any:
        spec = conn.spec
        if spec.get("transport") == "http":
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            headers = {}
            if spec.get("auth_key"):
                token = kconfig.api_key(spec["auth_key"])
                if not token:
                    raise MCPUnavailable(f"api key '{spec['auth_key']}' is not set")
                headers["Authorization"] = f"Bearer {token}"
            http = await stack.enter_async_context(
                httpx2.AsyncClient(headers=headers, timeout=conn.timeout))
            return streamable_http_client(spec["url"], http_client=http)

        from ..platform import shell
        from .transport import pipe_transport

        argv = [str(a) for a in spec.get("command") or []]
        if not argv:
            raise MCPUnavailable("no command configured")
        if not os.path.isabs(argv[0]):
            found = shell.which(argv[0])
            if not found:
                raise MCPUnavailable(f"'{argv[0]}' is not on PATH")
            argv[0] = found
        env = build_env(spec.get("env") or {})
        log_dir = Path(kconfig.STATE_DIR) / "mcp"
        log_dir.mkdir(parents=True, exist_ok=True)
        conn.proc = shell.spawn_pipe(argv, env=env, stderr_path=log_dir / f"{conn.name}.stderr.log",
                                     sandbox=bool(spec.get("sandbox")))
        stack.callback(shell.kill_group, conn.proc, 1.0)
        return pipe_transport(conn.proc)

    async def _serve(self, conn: _Conn) -> None:
        from mcp import Client
        from mcp_types import Implementation

        from . import bridge

        conn.closing = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                transport = await self._transport(conn, stack)
                client = Client(transport, mode="auto", read_timeout_seconds=conn.timeout,
                                cache=None, client_info=Implementation(name="kravein", version="3"))
                await asyncio.wait_for(stack.enter_async_context(client), conn.timeout)
                conn.client = client
                conn.listed = await asyncio.wait_for(self._list(client), conn.timeout)
                approved = conn.spec.get("tools") or {}
                conn.report = pins.compare(approved, conn.listed)
                conn.registered, conn.rejected = bridge.register(conn, self)
                conn.status = "ready"
                audit.record("mcp.connected", server=conn.name, registered=conn.registered,
                             new=conn.report["new"], changed=conn.report["changed"],
                             removed=conn.report["removed"], rejected=sorted(conn.rejected))
                conn.done.set()
                await conn.closing.wait()
        except BaseException as exc:                         # never let a server take us down
            if conn.status != "closed":
                conn.status, conn.error = "error", reason(exc)
                audit.record("mcp.connect_failed", server=conn.name, error=conn.error)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            from . import bridge as _bridge

            _bridge.unregister(conn)
            conn.client = None
            conn.done.set()
            conn.finished.set()

    async def _list(self, client: Any) -> dict[str, dict[str, Any]]:
        tools: dict[str, dict[str, Any]] = {}
        cursor = None
        for _ in range(_LIST_PAGE_CAP):
            page = await client.list_tools(cursor=cursor, cache_mode="refresh")
            for tool in page.tools:
                defn = pins.definition(tool)
                defn["_hints"] = _hints(tool)          # display only, never pinned
                tools[defn["name"]] = defn
            cursor = page.next_cursor
            if not cursor:
                break
        return tools

    # -- calls ------------------------------------------------------------

    def call(self, server: str, tool: str, args: dict[str, Any], *, stop: Any = None) -> Any:
        """Run one tool. Raises ``MCPUnavailable`` / ``MCPCallFailed``."""
        with self._lock:
            conn = self._conns.get(server)
        if conn is None or conn.status != "ready" or conn.client is None or self._loop is None:
            raise MCPUnavailable(f"MCP server '{server}' is not connected")
        future = asyncio.run_coroutine_threadsafe(self._call(conn, tool, args), self._loop)
        deadline = time.monotonic() + conn.timeout + 2
        while True:
            try:
                return future.result(0.2)
            except concurrent.futures.TimeoutError:
                if stop is not None and stop.is_set():
                    future.cancel()
                    audit.record("mcp.call_stopped", server=server, tool=tool)
                    raise MCPCallFailed("stopped at your request") from None
                if time.monotonic() > deadline:
                    future.cancel()
                    raise MCPCallFailed(f"timed out after {conn.timeout:.0f}s") from None
            except concurrent.futures.CancelledError:
                raise MCPCallFailed("cancelled") from None

    async def _call(self, conn: _Conn, tool: str, args: dict[str, Any]) -> Any:
        from mcp import MCPError
        from mcp_types import HEADER_MISMATCH

        try:
            return await asyncio.wait_for(
                conn.client.session.call_tool(tool, args, read_timeout_seconds=conn.timeout),
                conn.timeout)
        except asyncio.TimeoutError:
            raise MCPCallFailed(f"timed out after {conn.timeout:.0f}s") from None
        except MCPError as exc:
            if exc.error.code == HEADER_MISMATCH:
                audit.record("mcp.definition_changed", server=conn.name, tool=tool)
                self._drop(conn)
                raise MCPCallFailed("the server changed this tool's definition; it is "
                                    "disabled until the owner re-approves it") from None
            raise MCPCallFailed(f"the server returned an error ({exc.error.code})") from None
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as exc:
            # A broken pipe, a dead process, a closed stream: reconnect later.
            audit.record("mcp.call_failed", server=conn.name, tool=tool, error=reason(exc))
            self._drop(conn)
            raise MCPCallFailed(f"the connection failed ({reason(exc)})") from None

    def _drop(self, conn: _Conn) -> None:
        conn.status, conn.error = "error", conn.error or "connection dropped"
        conn.last_connect = time.monotonic()
        if conn.closing is not None:
            conn.closing.set()

    # -- status -----------------------------------------------------------

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            conns = dict(self._conns)
        out = []
        for name, spec in sorted(config.servers().items()):
            conn = conns.get(name)
            out.append({
                "name": name, "transport": spec.get("transport"), "enabled": bool(spec.get("enabled")),
                "approved": sorted((spec.get("tools") or {})), "status": conn.status if conn else "idle",
                "error": conn.error if conn else "", "registered": list(conn.registered) if conn else [],
                "report": dict(conn.report) if conn else {},
                "rejected": dict(conn.rejected) if conn else {},
            })
        return out

    def connection(self, name: str) -> _Conn | None:
        with self._lock:
            return self._conns.get(name)


def build_env(spec_env: dict[str, str]) -> dict[str, str]:
    """Exactly the variables the server config names; nothing inherited by
    default. ``inherit`` copies Kravein's value, ``key:<name>`` reads
    ``api_keys.json``, anything else is a literal (never a secret: those go
    through ``key:``)."""
    env: dict[str, str] = {}
    for name, value in spec_env.items():
        value = str(value)
        if value == "inherit":
            if name in os.environ:
                env[name] = os.environ[name]
        elif value.startswith("key:"):
            secret = kconfig.api_key(value[4:])
            if not secret:
                raise MCPUnavailable(f"api key '{value[4:]}' for {name} is not set")
            env[name] = secret
        else:
            env[name] = value
    return env


def _hints(tool: Any) -> dict[str, Any]:
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return {}
    return {k: v for k, v in (("read_only", getattr(ann, "read_only_hint", None)),
                              ("destructive", getattr(ann, "destructive_hint", None)),
                              ("open_world", getattr(ann, "open_world_hint", None))) if v is not None}


_manager: Manager | None = None
_manager_lock = threading.Lock()


def get() -> Manager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = Manager()
        return _manager


def ensure_started(wait: float | None = None) -> None:
    get().ensure_started(wait)
