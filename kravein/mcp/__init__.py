"""Kravein as an MCP client (#243, v3 phase 1b).

An MCP server the owner approved gets its tools registered into the same
registry as built-in skills, so every call goes through
``registry.dispatch -> safety.enforce -> audit``. See ``kb/modules/engine.md``.

- ``config``: ``~/.config/kravein/mcp.json``, written only by owner actions
  (``kravein mcp ...``); no skill or agent module may import its writers.
- ``pins``: tool definitions are hashed at approval; a change disables a tool.
- ``transport``: the stdio transport over a process spawned by
  ``platform/shell.spawn_pipe`` (the SDK's own spawner is never used).
- ``manager``: one event-loop thread holding every connection.
- ``bridge``: approved, pin-matching tools become ``mcp__<server>__<tool>``.
"""
