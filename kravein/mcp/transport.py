"""stdio transport over a process Kravein spawned itself (#243).

The SDK's ``stdio_client`` starts the server with ``anyio.open_process``. That
would be a subprocess outside ``platform/shell.py``, which is the one place
that may spawn (so the audit trail is complete and the environment is
controlled). So the process comes from ``shell.spawn_pipe`` and this module
only frames JSON-RPC on its pipes, the way the SDK does: one message per
line, parsed with ``mcp_types.jsonrpc_message_adapter``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import anyio
import anyio.to_thread
import mcp_types as types
from mcp.shared.message import SessionMessage

MAX_LINE = 4 * 1024 * 1024      # one JSON-RPC message; a longer line is refused


def _parse(line: str) -> SessionMessage | Exception:
    try:
        return SessionMessage(types.jsonrpc_message_adapter.validate_json(line, by_name=False))
    except ValueError as exc:
        return exc


@asynccontextmanager
async def pipe_transport(proc: Any) -> AsyncIterator[tuple]:
    """Yield ``(read_stream, write_stream)`` for ``mcp.Client``; ``proc`` comes
    from ``platform.shell.spawn_pipe``."""
    read_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)

    def write(data: bytes) -> None:
        proc.stdin.write(data)
        proc.stdin.flush()

    async def reader() -> None:
        async with read_writer:
            while True:
                # readline with a size cap: a server cannot make us buffer an
                # endless line.
                raw = await anyio.to_thread.run_sync(proc.stdout.readline, MAX_LINE + 1,
                                                     abandon_on_cancel=True)
                if not raw:
                    return                                     # server exited
                if len(raw) > MAX_LINE:
                    await read_writer.send(ValueError("MCP message larger than the limit"))
                    return
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    await read_writer.send(_parse(line))

    async def writer() -> None:
        async with write_reader:
            async for message in write_reader:
                data = (message.message.model_dump_json(by_alias=True, exclude_unset=True)
                        + "\n").encode("utf-8")
                try:
                    await anyio.to_thread.run_sync(write, data, abandon_on_cancel=True)
                except (BrokenPipeError, OSError, ValueError):
                    return

    async with anyio.create_task_group() as tg:
        tg.start_soon(reader)
        tg.start_soon(writer)
        try:
            yield read_stream, write_stream
        finally:
            tg.cancel_scope.cancel()
