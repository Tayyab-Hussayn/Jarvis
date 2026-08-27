"""LLM access: Gemini first, OpenRouter as fallback.

Preserves the original's two-provider design (``main.py`` used Gemini and
fell back to ``or_client.py`` on quota errors) behind one interface that
supports tool calling.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from . import audit, config

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Free-tier OpenRouter models, in descending order of capability. Kept from
# the original or_client.py; the first that answers wins.
OPENROUTER_TEXT_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen3-coder:free",
    "google/gemma-3-12b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]

OPENROUTER_VISION_MODELS = [
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "google/gemma-3-27b-it:free",
    "google/gemma-3-12b-it:free",
]

REQUEST_TIMEOUT = 90
RATE_LIMIT_COOLDOWN = 60

# Gemini rejects a client deadline below this outright, so a smaller setting
# would break every call instead of only slow ones.
GEMINI_MIN_TIMEOUT = 10.0


class LLMError(RuntimeError):
    """No provider could answer."""


@dataclass
class ToolCall:
    """A tool the model wants to run.

    ``signature`` carries Gemini 3.x's ``thought_signature``. Thinking models
    require it to be echoed back verbatim when the call appears in history —
    rebuilding the call without it fails the next request with
    "Function call is missing a thought_signature". It is opaque; just carry it.
    """

    name: str
    arguments: dict[str, Any]
    id: str = ""
    signature: Any = None


@dataclass
class Reply:
    """One model turn."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _is_quota_error(exc: Exception) -> bool:
    """Recognise the quota/rate-limit failures worth failing over on."""
    message = str(exc).lower()
    return any(token in message for token in (
        "429", "resource_exhausted", "quota", "rate limit", "too many requests",
        "exceeded", "1008", "permission denied", "access denied", "unauthenticated",
        "api key not valid", "503", "unavailable", "overloaded",
    ))


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        self._client = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(config.gemini_api_key())

    def _get_client(self):
        with self._lock:
            if self._client is None:
                import logging

                from google import genai

                # The SDK warns that automatic function calling in
                # generate_content is discouraged. We explicitly disable AFC
                # and drive the tool loop ourselves, so the warning is noise.
                logging.getLogger("google_genai.models").setLevel(logging.ERROR)

                # A slow Gemini is as unusable as a down one. Cap the wait so
                # a stalled request hands over to the next provider instead of
                # leaving the user staring at a prompt. Measured normal is
                # 2-6s; Google occasionally takes 20-30s.
                #
                # The API rejects anything under 10s with
                # "Manually set deadline Ns is too short", which would break
                # every request rather than just slow ones, so clamp it.
                timeout_s = max(
                    GEMINI_MIN_TIMEOUT,
                    float(config.get("llm.gemini_timeout_seconds", 20) or 20),
                )

                self._client = genai.Client(
                    api_key=config.gemini_api_key(),
                    # HttpOptions.timeout is in milliseconds.
                    http_options={"timeout": int(timeout_s * 1000)},
                )
            return self._client

    def model_name(self, override: str = "") -> str:
        return override or config.get("llm.gemini_model", "gemini-3.6-flash")

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        model: str = "",
    ) -> Reply:
        """One model, one attempt.

        There is deliberately no second Gemini model to fall back to. If this
        one is overloaded the error propagates and :class:`LLM` moves to the
        next *provider* — one clear hop instead of a chain of half-working
        models that each add latency before the real fallback.
        """
        name = self.model_name(model)
        try:
            return self._complete_one(
                messages, system=system, tools=tools,
                temperature=temperature, model=name,
            )
        except Exception as exc:
            text = str(exc)
            lowered = text.lower()
            if "503" in text or "UNAVAILABLE" in text or "overload" in lowered:
                audit.record("llm.gemini.overloaded", model=name)
            elif (
                "504" in text
                or "DEADLINE_EXCEEDED" in text
                or "timeout" in lowered
                or "timed out" in lowered
            ):
                audit.record("llm.gemini.timeout", model=name)
                # Make the reason obvious in the failover log.
                raise LLMError(
                    "gemini exceeded its "
                    f"{config.get('llm.gemini_timeout_seconds', 20)}s deadline"
                ) from exc
            elif "404" in text and "no longer available" in text:
                audit.record("llm.gemini.retired", model=name)
            raise

    def _complete_one(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        model: str = "",
    ) -> Reply:
        from google.genai import types

        client = self._get_client()

        contents = _to_gemini_contents(messages)

        cfg: dict[str, Any] = {"temperature": temperature}
        if system:
            cfg["system_instruction"] = system
        if tools:
            cfg["tools"] = [types.Tool(function_declarations=_sanitize_tools(tools))]
            # Let the model choose between answering and calling a tool.
            cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
                disable=True
            )

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**cfg),
        )

        reply = Reply(provider=self.name, model=model)
        text_parts: list[str] = []

        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                call = getattr(part, "function_call", None)
                if call is not None and getattr(call, "name", None):
                    reply.tool_calls.append(ToolCall(
                        name=call.name,
                        arguments=dict(call.args or {}),
                        id=getattr(call, "id", "") or call.name,
                        # Must be replayed verbatim on the next request.
                        signature=getattr(part, "thought_signature", None),
                    ))

        reply.text = "".join(text_parts).strip()
        if not reply.text and not reply.tool_calls:
            reply.text = (getattr(response, "text", "") or "").strip()
        return reply

    def vision(self, prompt: str, image_b64: str, mime: str = "image/jpeg") -> str:
        import base64

        from google.genai import types

        client = self._get_client()
        response = client.models.generate_content(
            model=self.model_name(),
            contents=[
                types.Part.from_bytes(data=base64.b64decode(image_b64), mime_type=mime),
                prompt,
            ],
        )
        return (getattr(response, "text", "") or "").strip()


def _sanitize_tools(tools: list[dict]) -> list[dict]:
    """Strip JSON-Schema keys the Gemini function-declaration parser rejects."""
    allowed = {"type", "description", "properties", "required", "items", "enum", "nullable"}

    def clean(node: Any) -> Any:
        """Clean one *schema* node.

        Note ``properties`` maps parameter names to schemas, so its keys are
        arbitrary and must be preserved — only its values get cleaned.
        """
        if not isinstance(node, dict):
            return node

        out: dict[str, Any] = {}
        for key, value in node.items():
            if key not in allowed:
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: clean(schema) for name, schema in value.items()}
            elif key == "items":
                out[key] = clean(value)
            elif key in ("required", "enum") and isinstance(value, list):
                out[key] = [v for v in value if isinstance(v, str)]
            else:
                out[key] = value

        # Gemini requires an explicit type on every schema node.
        if "properties" in out and "type" not in out:
            out["type"] = "object"
        if "items" in out and "type" not in out:
            out["type"] = "array"
        return out

    result = []
    for tool in tools:
        params = clean(tool.get("parameters", {}))
        if not params.get("properties"):
            # Gemini rejects an empty object schema; omit parameters entirely.
            result.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
            })
        else:
            result.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": params,
            })
    return result


def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[Any]:
    """Translate the neutral message list into Gemini `Content` objects."""
    from google.genai import types

    contents: list[Any] = []
    for msg in messages:
        role = msg.get("role", "user")

        if role == "tool":
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(
                    name=msg.get("name", "tool"),
                    response={"result": msg.get("content", "")},
                )],
            ))
            continue

        if role == "assistant":
            parts: list[Any] = []
            if msg.get("content"):
                parts.append(types.Part.from_text(text=msg["content"]))
            for call in msg.get("tool_calls", []) or []:
                part = types.Part.from_function_call(
                    name=call["name"], args=call.get("arguments", {}),
                )
                # Gemini 3.x rejects a replayed function call that has lost
                # its thought_signature, so put it back when we have one.
                signature = call.get("signature")
                if signature is not None:
                    try:
                        part.thought_signature = signature
                    except (AttributeError, ValueError):
                        pass
                parts.append(part)
            if parts:
                contents.append(types.Content(role="model", parts=parts))
            continue

        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=str(msg.get("content", "")))],
        ))
    return contents


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

class OpenRouterProvider:
    name = "openrouter"

    def __init__(self) -> None:
        self._rate_limited: dict[str, float] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(config.openrouter_api_key())

    def _usable_models(self, models: list[str]) -> list[str]:
        now = time.time()
        with self._lock:
            return [
                m for m in models
                if now - self._rate_limited.get(m, 0) > RATE_LIMIT_COOLDOWN
            ]

    def _mark_limited(self, model: str) -> None:
        with self._lock:
            self._rate_limited[model] = time.time()

    def _post(self, payload: dict) -> dict:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {config.openrouter_api_key()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/kravein",
                "X-Title": "Kravein",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RuntimeError("429 rate limited")
        response.raise_for_status()
        return response.json()

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        model: str = "",
    ) -> Reply:
        candidates = [model] if model else self._usable_models(OPENROUTER_TEXT_MODELS)
        if not candidates:
            candidates = OPENROUTER_TEXT_MODELS[:1]

        payload_messages = _to_openai_messages(messages, system)
        last_error: Exception | None = None

        for candidate in candidates:
            payload: dict[str, Any] = {
                "model": candidate,
                "messages": payload_messages,
                "temperature": temperature,
            }
            if tools:
                payload["tools"] = [
                    {"type": "function", "function": t} for t in tools
                ]
                payload["tool_choice"] = "auto"

            try:
                data = self._post(payload)
            except Exception as exc:
                last_error = exc
                if "429" in str(exc):
                    self._mark_limited(candidate)
                continue

            choices = data.get("choices") or []
            if not choices:
                last_error = RuntimeError("empty response")
                continue

            message = choices[0].get("message", {}) or {}
            reply = Reply(
                text=(message.get("content") or "").strip(),
                provider=self.name,
                model=candidate,
            )
            for call in message.get("tool_calls", []) or []:
                fn = call.get("function", {}) or {}
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                reply.tool_calls.append(ToolCall(
                    name=fn.get("name", ""), arguments=args, id=call.get("id", ""),
                ))
            return reply

        raise LLMError(f"all OpenRouter models failed; last error: {last_error}")

    def vision(self, prompt: str, image_b64: str, mime: str = "image/jpeg") -> str:
        for candidate in self._usable_models(OPENROUTER_VISION_MODELS):
            try:
                data = self._post({
                    "model": candidate,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url",
                             "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                        ],
                    }],
                })
            except Exception as exc:
                if "429" in str(exc):
                    self._mark_limited(candidate)
                continue
            choices = data.get("choices") or []
            if choices:
                return (choices[0].get("message", {}).get("content") or "").strip()
        raise LLMError("no OpenRouter vision model answered")


def _to_openai_messages(messages: list[dict[str, Any]], system: str) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id") or msg.get("name", "tool"),
                "name": msg.get("name", "tool"),
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
                        "arguments": json.dumps(c.get("arguments", {})),
                    },
                } for c in calls]
            out.append(entry)
        else:
            out.append({"role": "user", "content": str(msg.get("content", ""))})
    return out


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class LLM:
    """Provider-agnostic entry point with automatic failover.

    A provider that fails repeatedly is skipped for a cooldown rather than
    being retried on every request. Without that, a slow Gemini costs its full
    deadline on *every* turn before handing over — the failover works but the
    user still waits. One request pays the cost; the rest go straight to the
    fallback until the cooldown expires.
    """

    def __init__(self) -> None:
        from .cline import ClineProvider

        self.cline = ClineProvider()
        self.gemini = GeminiProvider()
        self.openrouter = OpenRouterProvider()

        self._failures: dict[str, int] = {}
        self._cooling_until: dict[str, float] = {}
        self._breaker_lock = threading.Lock()

    def _all(self) -> list[Any]:
        return [self.cline, self.gemini, self.openrouter]

    # -- circuit breaker -------------------------------------------------

    def _trip_threshold(self) -> int:
        return int(config.get("llm.breaker_failures", 2))

    def _cooldown(self) -> float:
        return float(config.get("llm.breaker_cooldown_seconds", 180))

    # Breaker state lives on disk: every `kravein ask` is a fresh process, so
    # in-memory state alone would forget the outage and re-pay the timeout.
    def _breaker_path(self) -> Any:
        from pathlib import Path

        return Path(config.STATE_DIR) / "llm-breaker.json"

    def _load_breaker(self) -> None:
        try:
            blob = json.loads(self._breaker_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(blob, dict):
            self._failures.update(blob.get("failures") or {})
            self._cooling_until.update(blob.get("cooling") or {})

    def _save_breaker(self) -> None:
        try:
            config.ensure_dirs()
            path = self._breaker_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({
                    "failures": self._failures,
                    "cooling": self._cooling_until,
                }),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            pass

    def _is_cooling(self, name: str) -> bool:
        with self._breaker_lock:
            if not self._cooling_until and not self._failures:
                self._load_breaker()
            return time.time() < float(self._cooling_until.get(name, 0.0))

    def _record_failure(self, name: str) -> None:
        with self._breaker_lock:
            self._load_breaker()
            count = int(self._failures.get(name, 0)) + 1
            self._failures[name] = count
            if count >= self._trip_threshold():
                self._cooling_until[name] = time.time() + self._cooldown()
                audit.record(
                    "llm.breaker_open", provider=name,
                    failures=count, cooldown_seconds=round(self._cooldown()),
                )
            self._save_breaker()

    def _record_success(self, name: str) -> None:
        with self._breaker_lock:
            self._load_breaker()
            # Pop both unconditionally: `or` would short-circuit and leave a
            # recovered provider cooling until its timer happened to expire.
            had_failures = self._failures.pop(name, 0)
            was_cooling = self._cooling_until.pop(name, 0)
            if had_failures or was_cooling:
                audit.record("llm.breaker_reset", provider=name)
                self._save_breaker()

    def reset_breaker(self) -> str:
        """Clear all cooldowns and try every provider again."""
        with self._breaker_lock:
            self._failures.clear()
            self._cooling_until.clear()
            self._save_breaker()
        return "Provider cooldowns cleared."

    def breaker_status(self) -> str:
        """Which providers are currently being skipped, and for how long."""
        now = time.time()
        with self._breaker_lock:
            # State lives on disk and this may be a fresh process.
            self._load_breaker()
            cooling = {
                name: float(until) - now
                for name, until in self._cooling_until.items()
                if float(until) > now
            }
        if not cooling:
            return "all providers healthy"
        return "cooling down: " + ", ".join(
            f"{name} ({secs:.0f}s left)" for name, secs in cooling.items()
        )

    def providers(self) -> list[str]:
        return [p.name for p in self._all() if p.available()]

    def _ordered(self) -> list[Any]:
        """Providers in the order they will be tried.

        ``llm.provider`` names the primary; ``llm.provider_order`` gives the
        fallback sequence after it. Unavailable providers are dropped, so the
        list is exactly what will actually be attempted.
        """
        by_name = {p.name: p for p in self._all()}
        preferred = str(config.get("llm.provider", "cline")).lower()
        configured = [str(n).lower() for n in (config.get("llm.provider_order") or [])]

        sequence = [preferred]
        for name in configured:
            if name not in sequence:
                sequence.append(name)

        # provider_order is exclusive when set: a provider left out of it is
        # left out on purpose, not merely deprioritised. Only fall back to
        # "everything available" when no order is configured at all.
        if not configured:
            for name in by_name:
                if name not in sequence:
                    sequence.append(name)

        usable = [
            by_name[name] for name in sequence
            if name in by_name and by_name[name].available()
        ]

        # Skip providers that are cooling down — unless that would leave us
        # with nothing, in which case trying is better than giving up.
        healthy = [p for p in usable if not self._is_cooling(p.name)]
        return healthy or usable

    def status(self) -> str:
        active = self.providers()
        if not active:
            return (
                "No LLM provider is configured.\n"
                "  Cline    : install the CLI and run `cline auth`\n"
                f"  Or a key : {config.CONFIG_DIR / 'api_keys.json'}, or set "
                "KRAVEIN_GEMINI_API_KEY / KRAVEIN_OPENROUTER_API_KEY"
            )

        from .cline import configured_model

        labels = []
        for provider in self._ordered():
            if provider.name == "gemini":
                labels.append(f"gemini:{self.gemini.model_name()}")
            elif provider.name == "cline":
                labels.append(f"cline:{configured_model()}")
            else:
                labels.append(provider.name)

        preferred = str(config.get("llm.provider", "gemini")).lower()
        detail = " → ".join(labels)
        breaker = self.breaker_status()
        cooling = breaker != "all providers healthy"

        if preferred not in [p.name for p in self._ordered()]:
            if cooling and preferred in breaker:
                detail += f"  (skipping '{preferred}' — see below)"
            else:
                detail += f"  (preferred '{preferred}' is unavailable)"

        if cooling:
            detail += f"\n  breaker: {breaker} — retry now with reset_llm_failover"
        return "LLM: " + detail

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> Reply:
        """Get one model turn, failing over between providers."""
        if temperature is None:
            temperature = float(config.get("llm.temperature", 0.4))

        order = self._ordered()
        if not order:
            raise LLMError(self.status())

        errors: list[str] = []
        for provider in order:
            try:
                reply = provider.complete(
                    messages, system=system, tools=tools, temperature=temperature
                )
                self._record_success(provider.name)
                audit.record(
                    "llm.reply",
                    provider=reply.provider, model=reply.model,
                    tool_calls=[c.name for c in reply.tool_calls],
                    chars=len(reply.text),
                )
                return reply
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                audit.record("llm.provider_failed", provider=provider.name, error=str(exc))
                if not _is_quota_error(exc):
                    # A non-quota failure on the preferred provider is still
                    # worth failing over on, but log it as unexpected.
                    audit.record("llm.unexpected_error", provider=provider.name, error=str(exc))
                self._record_failure(provider.name)
                continue

        raise LLMError("No LLM provider could answer. " + " | ".join(errors))

    def chat(self, prompt: str, system: str = "", temperature: float | None = None) -> str:
        """One-shot completion, no tools."""
        reply = self.complete(
            [{"role": "user", "content": prompt}],
            system=system, temperature=temperature,
        )
        return reply.text

    def chat_json(self, prompt: str, system: str = "") -> dict:
        """Completion coerced to a JSON object."""
        raw = self.chat(
            prompt,
            system=system or "Reply with valid JSON only. No prose, no markdown fences.",
            temperature=0.1,
        )
        return extract_json(raw)

    def vision(self, prompt: str, image_b64: str, mime: str = "image/jpeg") -> str:
        errors: list[str] = []
        # Gemini first for vision: it takes the image inline, whereas the
        # Cline CLI has to round-trip it through a file on disk.
        for provider in (self.gemini, self.openrouter, self.cline):
            if not provider.available():
                continue
            try:
                return provider.vision(prompt, image_b64, mime)
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
        raise LLMError("No vision provider answered. " + " | ".join(errors))


def extract_json(raw: str) -> dict:
    """Pull a JSON object out of a model reply that may be fenced or chatty."""
    text = (raw or "").strip()
    if not text:
        return {}

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced {...} block.
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return {}


_client: LLM | None = None


def client() -> LLM:
    global _client
    if _client is None:
        _client = LLM()
    return _client
