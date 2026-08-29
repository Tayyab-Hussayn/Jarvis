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

**LAN exposure.** The server can be bound to a private address so a phone on
the same network can reach it (``kravein serve --lan``). The three controls
above are unchanged and are exactly what makes that defensible — but two
things do change and neither is hidden:

* the allowlist grows by the literal IPs this machine holds, never a hostname
  and never a wildcard, so rebinding still fails; and
* the traffic is plain HTTP, so the token and everything else crosses the
  network in the clear. That is acceptable on a WPA-protected home network and
  is **not** acceptable on open or shared wifi. `serve()` says so out loud
  rather than leaving it to be discovered.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import socket
from pathlib import Path
from urllib.parse import urlsplit

from . import config

TOKEN_FILE = config.RUNTIME_DIR / "web-token"
TOKEN_BYTES = 32

_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})

# Extra hostnames accepted when the server is deliberately bound to the LAN.
# Populated by `allow_lan_host()` at startup and *only* ever with IP literals
# this machine actually owns.
#
# Why literals and never a hostname or a wildcard: the Host check exists to
# defeat DNS rebinding, where an attacker's name is made to resolve to our
# address. The request still arrives carrying the attacker's *hostname*, so an
# allowlist of literal IPs rejects it. Accepting a name — or accepting
# anything — would throw away the only defence that mechanism has.
_lan_hostnames: set[str] = set()


def is_private_address(value: str) -> bool:
    """Is this a loopback or RFC1918/link-local address?

    Binding the assistant to a public address would put a machine with 144
    skills and a shell on the open internet, so the answer to "which addresses
    may we serve on" is a whitelist, not a warning.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # `0.0.0.0` and `::` are *wildcards*, not addresses — and `ipaddress` calls
    # them private, since 0.0.0.0/8 is "this network". Treating them as
    # bindable would mean "every interface", which is the exact thing this
    # function exists to prevent, so they are rejected here rather than being
    # caught by a string comparison somewhere up the call chain.
    if address.is_unspecified:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local)


def local_addresses() -> list[str]:
    """Private IPv4 addresses this machine actually holds."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidate = info[4][0]
            if is_private_address(candidate) and not candidate.startswith("127."):
                found.add(candidate)
    except OSError:
        pass

    if not found:
        # getaddrinfo can return only loopback when /etc/hosts maps the
        # hostname there, which is common. Ask the routing table instead.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            with probe:
                probe.connect(("192.0.2.1", 80))     # TEST-NET-1, never routed
                candidate = probe.getsockname()[0]
            if is_private_address(candidate) and not candidate.startswith("127."):
                found.add(candidate)
        except OSError:
            pass
    return sorted(found)


def allow_lan_host(address: str) -> None:
    """Accept `address` in Host and Origin checks. IP literals only."""
    if not is_private_address(address):
        raise ValueError(f"refusing to allow non-private address {address!r}")
    _lan_hostnames.add(address.lower())


def clear_lan_hosts() -> None:
    _lan_hostnames.clear()


def allowed_hostnames() -> frozenset[str]:
    return frozenset(_LOOPBACK_HOSTNAMES | _lan_hostnames)


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
    """Reject a Host we are not serving — the DNS-rebinding defence.

    On LAN this is the control doing the real work. The token gates access,
    but a token can end up in a screenshot or a shell history; the Host check
    is what stops a page on some other site from scripting this server through
    the user's browser after rebinding a name onto our address.
    """
    if not host_header:
        # HTTP/1.1 requires Host. Absent means a non-browser client; the token
        # still gates it, and refusing here would break simple curl testing.
        return True
    return _hostname_of(host_header) in allowed_hostnames()


def origin_allowed(origin: str | None, extra: tuple[str, ...] = ()) -> bool:
    """Reject a cross-site Origin. A missing Origin is allowed; see module docs."""
    if not origin:
        return True
    if origin in extra:
        return True
    return _hostname_of(origin) in allowed_hostnames()
