"""Cline as an LLM provider.

Two paths, picked per model.

**Direct HTTP (default).** ``api.cline.bot`` exposes an OpenAI-compatible
``/chat/completions`` endpoint that accepts the account's own OAuth token.
Most models answer over it with native tool calling, so there is no CLI in the
loop: one HTTPS request per turn instead of ~5s of Node boot. We refresh the
token ourselves using the refresh token from Cline's store, and cache the
short-lived access token in Kravein's own state directory — Cline's config file
is only ever read, never written.

**The CLI (fallback).** A few models — ``deepseek-v4-flash`` among them — are
gated server-side to "Cline product surfaces" and return HTTP 403. For those we
drive the real CLI, which *is* such a surface, rather than forging headers to
impersonate a client we are not. Cline is itself an agent, so two flags matter:

  ``--auto-approve false``  it may not execute any of its own tools
  ``-s <prompt>``           replaces its coding-agent system prompt with ours

That reduces it to a reasoning engine, with tool calling done as structured
JSON since the CLI exposes no function-calling interface.

Either way Kravein decides what runs, applies its own safety layer, and keeps
the audit trail. Tokens are never logged.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import audit, config

CLI_NAME = "cline"
DEFAULT_TIMEOUT = 240

API_BASE = "https://api.cline.bot/api/v1"
REFRESH_URL = f"{API_BASE}/auth/refresh"
COMPLETIONS_URL = f"{API_BASE}/chat/completions"

CREDENTIALS_PATH = Path.home() / ".cline" / "data" / "settings" / "providers.json"

# Most models answer over plain HTTP with the account's own token. A few —
# deepseek-v4-flash among them — are gated server-side to "Cline product
# surfaces" and return 403; those fall back to driving the CLI, which is a
# legitimate surface but costs ~5s of process boot per turn.
#
# kimi-k2 is the default. It was picked by measurement on THIS workload, not
# reputation — the deciding tests were behaviour on vague requests (what a
# voice assistant mostly receives) and a 5-case agentic eval.
#
# Vague request ("My system feels messy, clean it up"), 5-6 runs each:
#
#                    answered   reached destructive   tool calls (median)
#   kimi-k2            5/5             0/5                   4
#   minimax-m2.5       6/6             2/6                   5
#   glm-4.6            3/5             5/5                  12
#   deepseek-v3.2      3/6             4/6                  20  (max 33)
#
# Agentic eval (chain / structured args / judgment / honesty / precision):
#
#   kimi-k2        15/15    $0.000150/req    5,746 prompt tokens
#   minimax-m2.5   12/15    $0.002966/req    9,651 prompt tokens
#   deepseek-v3.2  13/15    $0.000303/req    9,297 prompt tokens
#
# kimi-k2 wins on every axis: perfect scores, never reached a destructive tool
# unprompted, and 20x cheaper than m2.5 — it tokenises the same 118-tool
# schema in ~40% fewer prompt tokens, which is where nearly all the cost is.
#
# deepseek runs away on open-ended input: it looped on directory_size and
# list_files, burned the iteration budget, and the longer it flailed the more
# often it reached run_command. Prompt hardening did NOT fix this — that is a
# property of the model, so it was demoted rather than patched around.
DEFAULT_MODEL = "moonshotai/kimi-k2"

# Verified over direct HTTP with native tool calling and the full 118-tool
# catalogue. Ordered by measured median latency over 3 runs each — note this
# differs sharply from a bare "say OK" benchmark, because a large tool schema
# dominates. claude-haiku was fastest with no tools (1.8s) and by far the
# slowest with them (22s median, 15-53s spread).
#
#   deepseek/deepseek-v3.2   2.65s   also the cheapest per token
#   z-ai/glm-4.6             3.80s
#   moonshotai/kimi-k2       7.05s
#   anthropic/claude-haiku   22.2s   very high variance
#
# Note single-call latency is NOT the metric that matters for an agent —
# minimax-m2.5 is slightly slower per call but finishes vague tasks in ~5
# tool calls where deepseek takes ~20, so it is faster end to end.
DIRECT_MODELS = (
    "moonshotai/kimi-k2",
    "minimax/minimax-m2.5",
    "deepseek/deepseek-v3.2",
    "z-ai/glm-4.6",
    "qwen/qwen3-coder",
    "anthropic/claude-haiku-4.5",
    "google/gemini-2.5-flash",
    "deepseek/deepseek-chat",
)

# Known to be gated; go straight to the CLI instead of burning a 403.
CLI_ONLY_MODELS = ("deepseek/deepseek-v4-flash",)

# Why the direct path exists, measured on this machine:
#
#   bare `node -e ""`         0.07s   Node itself is not the problem
#   `cline --version`         ~5.0s   no inference at all — the CLI's floor
#   CLI turn (118 tools)     11-42s   boot + queue latency
#   DIRECT turn (118 tools)   2.65s   median, deepseek-v3.2
#
# The CLI's ~5s floor is it parsing ~174 MB of bundled JavaScript on every
# invocation; prompt caching never engages either (cacheReadTokens=0 on
# repeated identical prompts) because each subprocess is a fresh conversation.
STARTUP_OVERHEAD_NOTE = (
    "direct HTTP ~2.7s/turn; the CLI fallback adds ~5s boot (11-42s/turn)"
)


@dataclass
class ClineAuth:
    """What the credential file says about the current session."""

    present: bool
    email: str = ""
    account_id: str = ""
    model: str = ""
    expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() >= self.expires_at

    def describe(self) -> str:
        if not self.present:
            return f"No Cline credentials found at {CREDENTIALS_PATH}. Run:  cline auth"
        # This reports the CLI's own stored token. Kravein keeps a separate
        # short-lived one and refreshes it on demand, so an expired value here
        # is not a problem for the direct path.
        state = "stale (Kravein refreshes its own copy on demand)" if self.expired else "valid"
        return (
            f"Cline account : {self.email or 'unknown'}\n"
            f"  CLI's model : {self.model or DEFAULT_MODEL}  (only used on the CLI path)\n"
            f"  CLI token   : {state}"
        )


def read_auth() -> ClineAuth:
    """Read credential *metadata*. Tokens are never returned or logged."""
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ClineAuth(present=False)

    settings = ((data.get("providers") or {}).get("cline") or {}).get("settings") or {}
    auth = settings.get("auth") or {}
    if not auth.get("accessToken"):
        return ClineAuth(present=False)

    user = (auth.get("metadata") or {}).get("userInfo") or {}
    expires = auth.get("expiresAt") or 0
    # Stored as epoch milliseconds.
    expires_seconds = float(expires) / 1000.0 if expires else 0.0

    return ClineAuth(
        present=True,
        email=user.get("email", ""),
        account_id=auth.get("accountId", ""),
        model=settings.get("model", ""),
        expires_at=expires_seconds,
    )


class ClineAuthError(RuntimeError):
    """No usable Cline credentials."""


def _refresh_token() -> str:
    """Read the long-lived refresh token from Cline's own store."""
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    settings = ((data.get("providers") or {}).get("cline") or {}).get("settings") or {}
    return (settings.get("auth") or {}).get("refreshToken", "")


_token_lock = threading.Lock()
_token_cache: dict[str, Any] = {"access": "", "expires": 0.0}


def _token_cache_path() -> Path:
    return config.STATE_DIR / "cline-token.json"


def _load_cached_token() -> None:
    """Reload the cached access token from disk (survives restarts)."""
    try:
        blob = json.loads(_token_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(blob, dict) and blob.get("access"):
        _token_cache.update(
            {"access": blob["access"], "expires": float(blob.get("expires", 0))}
        )


def _store_cached_token() -> None:
    """Persist our own copy. Cline's file is never written to."""
    try:
        config.ensure_dirs()
        path = _token_cache_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_token_cache), encoding="utf-8")
        tmp.replace(path)
        path.chmod(0o600)
    except OSError:
        pass


def access_token(force: bool = False) -> str:
    """Get a valid access token, refreshing when it is close to expiry.

    Reads Cline's refresh token but keeps the short-lived access token in
    Kravein's own state directory, so Cline's config is never modified.
    """
    import requests

    with _token_lock:
        if not _token_cache["access"]:
            _load_cached_token()

        # Refresh a minute early rather than racing the expiry.
        if not force and _token_cache["access"] and time.time() < _token_cache["expires"] - 60:
            return str(_token_cache["access"])

        refresh = _refresh_token()
        if not refresh:
            raise ClineAuthError(
                f"No Cline refresh token in {CREDENTIALS_PATH}. Run:  cline auth"
            )

        try:
            response = requests.post(
                REFRESH_URL,
                json={"refreshToken": refresh, "grantType": "refresh_token"},
                timeout=40,
            )
        except Exception as exc:
            raise ClineAuthError(f"could not reach Cline to refresh the token: {exc}") from exc

        if response.status_code != 200:
            raise ClineAuthError(
                f"token refresh failed ({response.status_code}). Run:  cline auth"
            )

        payload = (response.json() or {}).get("data") or {}
        token = payload.get("accessToken", "")
        if not token:
            raise ClineAuthError("token refresh returned no access token")

        # expiresAt is an ISO timestamp here (it is epoch ms in Cline's file).
        expires = time.time() + 3000
        raw = payload.get("expiresAt")
        if isinstance(raw, str) and raw:
            try:
                from datetime import datetime

                expires = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass

        _token_cache.update({"access": token, "expires": expires})
        _store_cached_token()
        audit.record("llm.cline.token_refreshed", expires_in=round(expires - time.time()))
        return token


def configured_model() -> str:
    """Which model to use.

    Kravein's own setting wins, so the assistant's model can differ from
    whatever the user last selected for interactive Cline sessions.
    """
    override = str(config.get("llm.cline_model", "") or "").strip()
    if override:
        return override
    return read_auth().model or DEFAULT_MODEL


def _workspace() -> Path:
    """An empty directory to run the CLI in.

    Cline inspects its working directory for project context. Pointing it at
    a scratch dir keeps it from reading the user's repository and stops any
    stray file operation from landing somewhere that matters.
    """
    path = config.CACHE_DIR / "cline-workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

# The CLI wraps us in its own coding-agent context, which advertises its own
# tools and then reports them as unavailable (we pass --auto-approve false).
# Left unaddressed the model reasons about *that* situation and tells the user
# it has no way to act. This states plainly which world it is operating in.
_GROUNDING = """\
# Important: which tools are real

Disregard any other tool list, permission notice or "non-interactive session"
warning in your surrounding context. Those belong to the program that is
running you and are NOT your situation.

The tools listed below are the real ones. They are executed on a live Arch
Linux desktop by the host process that sent you this message, and they work.
When you name one, it runs for real.

Never tell the user you lack access to a tool that is listed below. Never
suggest they run a shell command themselves as a substitute for a tool you
have. If a tool genuinely fails, the host will send you its error and you can
report that.
"""

_PROTOCOL = """\

# How to reply

You decide; the host executes. You do not run anything yourself.

Reply with exactly ONE JSON object and nothing else — no markdown fences, no
commentary before or after.

To run one or more tools:
{"tool_calls": [{"name": "tool_name", "arguments": {"arg": "value"}}]}

To answer the user directly:
{"reply": "your answer here"}

Rules:
- Use the exact tool names listed above. Never invent one.
- Include every required argument.
- Prefer answering directly once you have enough information from tool results.
- Never set a "confirm" argument yourself. The host asks the user, and a
  refusal is the user's decision — report it, do not work around it.
- If a tool reports it failed, say what failed. Do not conclude the tool
  does not exist.
"""


def _render_tool(tool: dict[str, Any]) -> str:
    """Render one tool as a compact signature line.

    Full JSON Schema for 100+ skills would waste most of the prompt, so this
    emits a signature the model can read at a glance.
    """
    params = tool.get("parameters") or {}
    properties = params.get("properties") or {}
    required = set(params.get("required") or [])

    rendered: list[str] = []
    for name, schema in properties.items():
        kind = schema.get("type", "string")
        if schema.get("enum"):
            kind = "|".join(str(v) for v in schema["enum"])
        rendered.append(f"{name}: {kind}" if name in required else f"{name}?: {kind}")

    signature = f"{tool['name']}({', '.join(rendered)})"
    description = " ".join((tool.get("description") or "").split())
    if len(description) > 150:
        description = description[:150].rstrip() + "…"
    return f"- {signature}\n    {description}"


def build_prompt(
    messages: list[dict[str, Any]],
    tools: list[dict] | None,
) -> str:
    """Flatten the conversation and tool catalogue into a single prompt."""
    sections: list[str] = []

    if tools:
        sections.append(_GROUNDING)
        lines = [_render_tool(tool) for tool in tools]
        sections.append(
            f"# Available tools ({len(tools)} of them, all live)\n\n" + "\n".join(lines)
        )
        sections.append(_PROTOCOL)

    transcript: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = (message.get("content") or "").strip()

        if role == "user":
            transcript.append(f"USER: {content}")

        elif role == "assistant":
            calls = message.get("tool_calls") or []
            if calls:
                rendered = ", ".join(
                    f"{c['name']}({json.dumps(c.get('arguments', {}), default=str)})"
                    for c in calls
                )
                transcript.append(f"YOU CALLED: {rendered}")
            if content:
                transcript.append(f"YOU: {content}")

        elif role == "tool":
            name = message.get("name", "tool")
            # Tool output can be huge (process lists, file dumps); keep the
            # head and tail so the model sees the shape and the conclusion.
            if len(content) > 4000:
                content = content[:2500] + "\n…[trimmed]…\n" + content[-1200:]
            transcript.append(f"RESULT of {name}:\n{content}")

    sections.append("# Conversation\n\n" + "\n\n".join(transcript))

    if tools:
        sections.append("Now reply with the single JSON object described above.")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------

def _parse_ndjson(stdout: str) -> tuple[str, dict[str, Any]]:
    """Pull the final answer and run metadata out of Cline's NDJSON stream."""
    text = ""
    meta: dict[str, Any] = {}

    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        kind = event.get("type")
        if kind == "run_result":
            text = event.get("text") or text
            meta = {
                "finish_reason": event.get("finishReason"),
                "iterations": event.get("iterations"),
                "usage": event.get("usage"),
                "model": (event.get("model") or {}).get("id"),
                "duration_ms": event.get("durationMs"),
            }
        elif kind == "agent_event":
            inner = event.get("event") or {}
            if inner.get("type") == "done" and inner.get("text"):
                text = text or inner["text"]

    return text.strip(), meta


class ClineCliError(RuntimeError):
    """The Cline CLI failed or returned nothing usable."""


class ModelGatedError(RuntimeError):
    """The model is restricted to official Cline surfaces (HTTP 403)."""


def direct_complete(
    messages: list[dict[str, Any]],
    *,
    system: str = "",
    tools: list[dict] | None = None,
    temperature: float = 0.4,
    model: str = "",
    timeout: float = 0.0,
) -> dict[str, Any]:
    """Call Cline's OpenAI-compatible endpoint directly.

    This skips the CLI entirely, which removes ~5s of process boot per turn,
    and uses native tool calling instead of the text-JSON protocol the CLI
    path needs.

    Raises :class:`ModelGatedError` when the model is restricted to official
    Cline surfaces, so the caller can fall back to the CLI.
    """
    import requests

    # A slow primary is as unusable as a down one — the same reasoning that
    # caps Gemini at 20s, applied to the provider that actually leads. It sat
    # at 180s, which meant a degraded Cline could hold a turn for three minutes
    # with nothing able to interrupt it: cancellation only bites between tool
    # rounds, so Stop can do nothing at all while the request is in flight.
    # Measured on 2026-08-24, one turn spent 52s inside this call.
    #
    # Capping is only viable because there is now somewhere to fail over to.
    # Raise `llm.cline_timeout_seconds` if you routinely ask for work that
    # legitimately takes longer than this to produce a first token.
    if timeout <= 0:
        timeout = float(config.get("llm.cline_timeout_seconds", 45) or 45)

    model = model or configured_model()
    payload: dict[str, Any] = {
        "model": model,
        "messages": _to_openai_messages(messages, system),
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = [{"type": "function", "function": t} for t in tools]
        payload["tool_choice"] = "auto"

    audit.record(
        "llm.cline.direct_request",
        model=model, messages=len(payload["messages"]), tools=len(tools or []),
    )

    started = time.monotonic()
    try:
        response = requests.post(
            COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer workos:{access_token()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        raise ClineCliError(f"could not reach the Cline API: {exc}") from exc

    if response.status_code == 401:
        # Token may have just expired; refresh once and retry.
        access_token(force=True)
        response = requests.post(
            COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer workos:{access_token()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )

    if response.status_code == 403:
        detail = response.text[:200]
        audit.record("llm.cline.gated", model=model)
        raise ModelGatedError(
            f"'{model}' is only available through the Cline CLI: {detail}"
        )

    if response.status_code != 200:
        raise ClineCliError(
            f"Cline API returned {response.status_code}: {response.text[:300]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ClineCliError(f"Cline API sent invalid JSON: {exc}") from exc

    # Responses are wrapped in a {"data": …} envelope.
    body = body.get("data", body)
    choices = body.get("choices") or []
    if not choices:
        raise ClineCliError(f"Cline API returned no choices: {str(body)[:200]}")

    audit.record(
        "llm.cline.direct_reply",
        model=model,
        usage=body.get("usage"),
        duration_ms=round((time.monotonic() - started) * 1000, 1),
    )
    return choices[0].get("message") or {}


def _to_openai_messages(messages: list[dict[str, Any]], system: str) -> list[dict]:
    """Translate the neutral message list into OpenAI chat format."""
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id") or msg.get("name", "tool"),
                "content": str(msg.get("content", "")),
            })
        elif role == "assistant":
            entry: dict[str, Any] = {
                "role": "assistant",
                "content": msg.get("content") or None,
            }
            calls = msg.get("tool_calls") or []
            if calls:
                entry["tool_calls"] = [{
                    "id": c.get("id") or c["name"],
                    "type": "function",
                    "function": {
                        "name": c["name"],
                        "arguments": json.dumps(c.get("arguments", {}), default=str),
                    },
                } for c in calls]
            out.append(entry)
        else:
            out.append({"role": "user", "content": str(msg.get("content", ""))})
    return out


def _clean_stderr(stderr: str) -> str:
    """Drop noise the CLI emits on minimal systems.

    ``hostname`` is not part of a base Arch install, and the CLI shells out to
    it; the resulting message is harmless but looks like a fatal error.
    """
    kept = []
    for line in (stderr or "").splitlines():
        if "hostname: command not found" in line:
            continue
        if line.strip() in ("", "/bin/sh: line 1:"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def run_cli(
    prompt: str,
    *,
    system: str = "",
    model: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    thinking: str = "",
    retries: int = 1,
) -> tuple[str, dict[str, Any]]:
    """Run one non-interactive Cline turn and return (text, metadata).

    The CLI occasionally exits 0 having produced nothing at all (a transient
    upstream hiccup), so an empty result is retried once before giving up.
    """
    binary = shutil.which(CLI_NAME)
    if not binary:
        raise ClineCliError(
            "The `cline` CLI is not installed or not on PATH. "
            "Install it, then run:  cline auth"
        )

    args = [
        binary,
        "--json",
        # Cline must not act on its own; Kravein owns execution and safety.
        "--auto-approve", "false",
        "--compaction", "off",
        "-m", model or configured_model(),
        "-c", str(_workspace()),
    ]
    if system:
        args += ["-s", system]
    if thinking:
        args += ["--thinking", thinking]
    args.append(prompt)

    audit.record(
        "llm.cline.request",
        model=model or configured_model(),
        prompt_chars=len(prompt),
        system_chars=len(system),
    )

    attempts = max(1, retries + 1)
    text, meta, proc = "", {}, None

    for attempt in range(attempts):
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                cwd=str(_workspace()),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise ClineCliError(
                f"the Cline CLI did not answer within {timeout:.0f}s"
            ) from None
        except OSError as exc:
            raise ClineCliError(f"could not start the Cline CLI: {exc}") from exc

        text, meta = _parse_ndjson(proc.stdout or "")
        if text:
            break

        if attempt + 1 < attempts:
            audit.record("llm.cline.empty_retry", attempt=attempt + 1)
            time.sleep(1.5)

    if not text:
        detail = _clean_stderr(proc.stderr if proc else "")
        detail = detail[-500:] or ((proc.stdout or "")[-300:] if proc else "") or "no output"
        code = proc.returncode if proc else -1

        if "auth" in detail.lower() or "unauthor" in detail.lower():
            raise ClineCliError(
                f"Cline rejected the request (exit {code}). "
                f"Re-authenticate with:  cline auth\n{detail}"
            )
        raise ClineCliError(
            f"the Cline CLI returned no answer after {attempts} attempt(s) "
            f"(exit {code}): {detail}"
        )

    audit.record(
        "llm.cline.reply",
        model=meta.get("model"),
        finish_reason=meta.get("finish_reason"),
        usage=meta.get("usage"),
        duration_ms=meta.get("duration_ms"),
        chars=len(text),
    )
    return text, meta


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class ClineProvider:
    """Provider that satisfies the same interface as Gemini and OpenRouter."""

    name = "cline"

    def __init__(self) -> None:
        self._checked: bool | None = None

    def available(self) -> bool:
        if self._checked is None:
            # The direct path only needs credentials; the CLI is optional and
            # used solely for models gated to official surfaces.
            self._checked = bool(_refresh_token()) or (
                bool(shutil.which(CLI_NAME)) and read_auth().present
            )
        return self._checked

    @staticmethod
    def _use_cli(model: str) -> bool:
        """Gated models must go through the CLI; everything else goes direct."""
        if config.get("llm.cline_force_cli", False) is True:
            return True
        return any(model.startswith(m) for m in CLI_ONLY_MODELS)

    def status(self) -> str:
        if not shutil.which(CLI_NAME):
            return "cline CLI not installed"
        return read_auth().describe()

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        model: str = "",
    ):
        from .llm import Reply, ToolCall

        primary = model or configured_model()

        # One same-provider hop before giving up, so a single bad model does
        # not take the assistant down. Both are verified for tool calling.
        candidates = [primary]
        if not model:
            spare = str(config.get("llm.cline_fallback_model", "") or "")
            if spare and spare != primary:
                candidates.append(spare)

        last: Exception | None = None
        for candidate in candidates:
            if self._use_cli(candidate):
                return self._complete_via_cli(
                    messages, system=system, tools=tools, model=candidate
                )
            try:
                return self._complete_direct(
                    messages, system=system, tools=tools,
                    temperature=temperature, model=candidate,
                )
            except ModelGatedError as exc:
                # Gated: the CLI is an allowed surface, so use it.
                audit.record(
                    "llm.cline.falling_back_to_cli",
                    model=candidate, reason=str(exc)[:200],
                )
                return self._complete_via_cli(
                    messages, system=system, tools=tools, model=candidate
                )
            except Exception as exc:
                last = exc
                audit.record(
                    "llm.cline.model_failed", model=candidate, error=str(exc)[:200]
                )
                continue

        raise ClineCliError(
            f"no Cline model answered ({', '.join(candidates)}): {last}"
        )

    def _complete_direct(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str,
        tools: list[dict] | None,
        temperature: float,
        model: str,
    ):
        """The fast path: one HTTPS request, native tool calling."""
        from .llm import Reply, ToolCall, extract_json

        message = direct_complete(
            messages, system=system, tools=tools,
            temperature=temperature, model=model,
        )

        reply = Reply(provider=self.name, model=model)
        reply.text = (message.get("content") or "").strip()

        for index, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            raw = fn.get("arguments", "{}")
            if isinstance(raw, str):
                try:
                    arguments = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    arguments = extract_json(raw)
            else:
                arguments = dict(raw or {})
            reply.tool_calls.append(ToolCall(
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
                id=call.get("id") or f"cline-{index}",
            ))

        return reply

    def _complete_via_cli(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        model: str = "",
    ):
        """The slow path, for models gated to official Cline surfaces."""
        # Imported here to avoid a circular import at module load.
        from .llm import Reply, ToolCall, extract_json

        prompt = build_prompt(messages, tools)
        text, meta = run_cli(prompt, system=system, model=model)

        reply = Reply(
            provider=self.name,
            model=meta.get("model") or model or configured_model(),
        )

        if not tools:
            reply.text = text
            return reply

        decision = extract_json(text)

        if not decision:
            # The model answered in prose despite the protocol. Take it at
            # face value rather than failing the turn.
            reply.text = text
            return reply

        raw_calls = decision.get("tool_calls")
        if isinstance(raw_calls, dict):
            raw_calls = [raw_calls]

        if isinstance(raw_calls, list) and raw_calls:
            for index, call in enumerate(raw_calls):
                if not isinstance(call, dict):
                    continue
                name = call.get("name") or call.get("tool") or ""
                if not name:
                    continue
                arguments = call.get("arguments")
                if isinstance(arguments, str):
                    arguments = extract_json(arguments) or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                reply.tool_calls.append(
                    ToolCall(name=str(name), arguments=arguments, id=f"cline-{index}")
                )

        answer = decision.get("reply") or decision.get("answer") or decision.get("text")
        if isinstance(answer, str):
            reply.text = answer.strip()

        if not reply.tool_calls and not reply.text:
            reply.text = text

        return reply

    def vision(self, prompt: str, image_b64: str, mime: str = "image/jpeg") -> str:
        """Describe an image.

        The CLI takes a prompt, not an attachment, so the image is written to
        the scratch workspace and referenced by path — Cline can read files in
        its working directory.
        """
        suffix = ".png" if "png" in mime else ".jpg"
        workspace = _workspace()

        with tempfile.NamedTemporaryFile(
            dir=workspace, suffix=suffix, delete=False
        ) as handle:
            import base64

            handle.write(base64.b64decode(image_b64))
            image_path = Path(handle.name)

        try:
            text, _ = run_cli(
                f"Look at the image file '{image_path.name}' in your working "
                f"directory and answer this question about it.\n\n{prompt}",
                system=(
                    "You are a vision assistant. Read the referenced image file "
                    "and answer concisely and factually."
                ),
                timeout=DEFAULT_TIMEOUT,
            )
            return text
        finally:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass


def doctor() -> str:
    """Human-readable health check for the Cline integration."""
    model = configured_model()
    gated = ClineProvider._use_cli(model)

    lines = [
        "Cline provider",
        f"  model    : {model}",
        f"  path     : {'CLI (model is gated)' if gated else 'direct HTTP'}",
    ]

    if not _refresh_token():
        lines.append(f"  auth     : NOT FOUND in {CREDENTIALS_PATH}")
        lines.append("  Fix      : run  cline auth")
        return "\n".join(lines)

    lines.append("  auth     : refresh token present")

    binary = shutil.which(CLI_NAME)
    if not binary:
        lines.append("  CLI      : not installed (only needed for gated models)")
        if gated:
            lines.append(f"  WARNING  : '{model}' needs the CLI, which is missing")
        lines.append(f"  Note     : {STARTUP_OVERHEAD_NOTE}")
        return "\n".join(lines)
    lines.append(f"  CLI      : {binary}")

    try:
        version = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=20
        ).stdout.strip().splitlines()[-1]
        lines.append(f"  Version  : {version}")
    except (OSError, subprocess.TimeoutExpired, IndexError):
        lines.append("  Version  : could not determine")

    auth = read_auth()
    for line in auth.describe().splitlines():
        lines.append(f"  {line}")

    lines.append(f"  Workspace: {_workspace()}")
    lines.append(f"  Note     : {STARTUP_OVERHEAD_NOTE}")
    return "\n".join(lines)
