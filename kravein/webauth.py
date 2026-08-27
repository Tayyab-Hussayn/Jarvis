"""Access control for the local web server.

Binding to ``127.0.0.1`` is **not** on its own a security boundary. Any page
open in the user's browser can issue requests to a loopback port, and DNS
rebinding defeats a naive host check. Three controls, all required:

1. **Bearer token** — generated at start, stored ``0600`` in the runtime dir,
   compared with :func:`secrets.compare_digest`.
2. **Origin allowlist** — a random website must not be able to script the
   local server through the user's browser.
3. **Host check** — the actual DNS-rebinding defence. A rebound name resolves
   to 127.0.0.1 but still carries the *attacker's* hostname in ``Host``.

Same-origin browser requests may legitimately omit ``Origin`` (a plain page
load is not a cross-origin fetch), so a missing Origin is allowed; a present
one must match. The token is what actually gates access.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from . import config

TOKEN_FILE = config.RUNTIME_DIR / "web-token"
TOKEN_BYTES = 32

_ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def token_path() -> Path:
    return TOKEN_FILE


def issue_token(*, force: bool = False) -> str:
    """Return this server's token, generating and persisting one if needed.

    Reuses an existing token so a browser tab survives a server restart.
    """
    config.ensure_dirs()
    if not force:
        existing = read_token()
        if existing:
            return existing

    token = secrets.token_urlsafe(TOKEN_BYTES)
    # Create with 0600 from the outset — writing then chmod'ing leaves a window
    # where another local user could read it.
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    return token


def read_token() -> str:
    """Read the persisted token, or "" if there isn't a usable one."""
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return token if token else ""


def clear_token() -> None:
    """Remove the token file. Used on shutdown and by the tests."""
    try:
        TOKEN_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def verify_token(supplied: str | None, expected: str) -> bool:
    """Constant-time token comparison. Empty never matches."""
    if not supplied or not expected:
        return False
    return secrets.compare_digest(str(supplied), str(expected))


def token_from_request(
    header: str | None, query_token: str | None = None
) -> str | None:
    """Pull a token out of an ``Authorization`` header or a query parameter.

    The query parameter exists because the browser WebSocket API cannot set
    request headers. It is only consulted for the handshake.
    """
    if header:
        prefix = "bearer "
        if header.lower().startswith(prefix):
            return header[len(prefix):].strip()
        return header.strip()
    return query_token


def _hostname_of(value: str) -> str:
    """Extract the hostname from a Host header or an Origin URL."""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" in value:
        return (urlsplit(value).hostname or "").lower()
    # Bare Host header: "127.0.0.1:8787" or "[::1]:8787".
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[1:end].lower()
    return value.rsplit(":", 1)[0].lower() if ":" in value else value.lower()


def host_allowed(host_header: str | None) -> bool:
    """Reject a Host that is not loopback — the DNS-rebinding defence."""
    if not host_header:
        # HTTP/1.1 requires Host. Absent means a non-browser client; the token
        # still gates it, and refusing here would break simple curl testing.
        return True
    return _hostname_of(host_header) in _ALLOWED_HOSTNAMES


def origin_allowed(origin: str | None, extra: tuple[str, ...] = ()) -> bool:
    """Reject a cross-site Origin. A missing Origin is allowed; see module docs."""
    if not origin:
        return True
    if origin in extra:
        return True
    return _hostname_of(origin) in _ALLOWED_HOSTNAMES
