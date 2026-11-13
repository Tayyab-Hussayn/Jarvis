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


class ProviderBlocked(LLMError):
    """The provider refused for a reason that will not clear in seconds.

    Out of credit (402) or over a usage limit (429). Retrying on every request
    only pays a wasted round-trip before the failover, so the facade opens the
    breaker at once for ``cooldown`` seconds instead of waiting for N failures.
    """

    def __init__(self, message: str, cooldown: float) -> None:
        super().__init__(message)
        self.cooldown = cooldown


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
class Usage:
    """Token counts exactly as the provider reported them.

    ``input`` is every prompt token the provider counted, cached ones included;
    ``cached`` is that cached subset (0 when the provider does not say);
    ``output`` is everything generated (for Gemini: answer + thinking tokens,
    both billed as output). Never an estimate: no report means ``Reply.usage``
    is ``None``.
    """

    input: int = 0
    cached: int = 0
    output: int = 0


@dataclass
class Reply:
    """One model turn."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    usage: Usage | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _int(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


def usage_from_openai(raw: Any) -> Usage | None:
    """OpenAI-wire ``usage`` (Zen, NovaRouter, OpenRouter, Cline direct).

    Cached tokens: ``prompt_tokens_details.cached_tokens`` (OpenAI standard),
    else DeepSeek's ``prompt_cache_hit_tokens``.
    """
    if not isinstance(raw, dict):
        return None
    prompt = _int(raw.get("prompt_tokens"))
    completion = _int(raw.get("completion_tokens"))
    if prompt is None and completion is None:
        return None
    details = raw.get("prompt_tokens_details")
    cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else None
    if cached is None:
        cached = _int(raw.get("prompt_cache_hit_tokens"))
    return Usage(input=prompt or 0, cached=cached or 0, output=completion or 0)


def usage_from_gemini(response: Any) -> Usage | None:
    """``response.usage_metadata`` of a google-genai reply."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None
    prompt = _int(getattr(meta, "prompt_token_count", None))
    answer = _int(getattr(meta, "candidates_token_count", None))
    thoughts = _int(getattr(meta, "thoughts_token_count", None))
    if prompt is None and answer is None and thoughts is None:
        return None
    cached = _int(getattr(meta, "cached_content_token_count", None))
    return Usage(input=prompt or 0, cached=cached or 0, output=(answer or 0) + (thoughts or 0))


# Model roles (#228). `worker:<profile>` is open-ended; `vision` and `voice`
# exist for the resolver only (live.py and LLM.vision keep their own chains).
ROLES = ("orchestrator", "utility", "judge", "vision", "voice")


def valid_role(role: str) -> bool:
    if role in ROLES:
        return True
    prefix, _, profile = role.partition(":")
    return prefix == "worker" and bool(profile.strip())


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

        reply = Reply(provider=self.name, model=model, usage=usage_from_gemini(response))
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


def _has_signature(call: dict[str, Any]) -> bool:
    sig = call.get("signature")
    return isinstance(sig, (bytes, bytearray)) and bool(sig)


def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[Any]:
    """Translate the neutral message list into Gemini `Content` objects.

    A step whose first call has no real signature (history saved before #154
    lost it) cannot be replayed as a function call: Gemini 3.x answers 400
    "missing a thought_signature". Google documents a dummy value for that
    case, but ``gemini-3.5-flash-lite`` rejects it through the SDK ("Corrupted
    thought signature" for the bytes form, a base64 error for the literal
    string; verified live 2026-11-02), so such a call and its result are
    flattened to plain text instead. The model still sees what was done.
    """
    from google.genai import types

    # Ids (or names) of calls we flatten, so their results flatten too.
    flat: set[str] = set()
    for msg in messages:
        calls = msg.get("tool_calls") or [] if msg.get("role") == "assistant" else []
        if calls and not _has_signature(calls[0]):
            flat.update(c.get("id") or c["name"] for c in calls)

    contents: list[Any] = []
    for msg in messages:
        role = msg.get("role", "user")

        if role == "tool":
            key = msg.get("tool_call_id") or msg.get("name", "tool")
            if key in flat:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text=f"[result of {msg.get('name', 'tool')}]: {msg.get('content', '')}"
                )]))
                continue
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
            calls = msg.get("tool_calls", []) or []
            if calls and not _has_signature(calls[0]):
                for call in calls:
                    parts.append(types.Part.from_text(
                        text=f"[called {call['name']}({json.dumps(call.get('arguments', {}), default=str)})]"
                    ))
                contents.append(types.Content(role="model", parts=parts))
                continue
            for call in calls:
                part = types.Part.from_function_call(
                    name=call["name"], args=call.get("arguments", {}),
                )
                # Gemini 3.x rejects a replayed function call that has lost
                # its thought_signature, so put it back when we have one
                # (parallel calls after the first legitimately carry none).
                if _has_signature(call):
                    try:
                        part.thought_signature = call["signature"]
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
                usage=usage_from_openai(data.get("usage")),
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


class NovaRouterProvider:
    """NovaRouter (https://novarouter.site) — an OpenAI-compatible gateway.

    Added as the failover behind Cline. `provider_order` had a single entry, so
    one provider outage took the assistant down with nothing behind it — which
    is tolerable at the desk and not tolerable from a phone.

    What was verified against the live API on 2026-08-24, without a key:

    * `/v1/models` returns an OpenAI-shaped list (`{"object": "list", ...}`).
    * `kimi-k3` is present, served from nvidia-nim, 262k context, listing
      text/vision/code/reasoning modalities.
    * `/v1/chat/completions` exists and answers `401 authentication_error`.

    What is **not** verified, because it needs a key: whether tool calling
    actually works. The request is built to the OpenAI standard (`tools`, then
    `tool_calls` on the reply), which is what "OpenAI-compatible" is supposed
    to mean — but this workload lives or dies on native tool calling, so treat
    it as unproven until a real turn has run. `tools/eval_models.py` against
    the real schema is the check that settles it, not a "say OK" prompt.
    """

    name = "novarouter"
    label = "NovaRouter"
    # Statuses that open the breaker immediately with a long cooldown. Empty
    # here: a NovaRouter 429 keeps the ordinary threshold behaviour.
    BLOCK_STATUSES: tuple[int, ...] = ()

    def _blocked_cooldown(self) -> float:
        return 0.0

    # The gateway rejects more than this outright:
    #   400 validation_failed "Array must contain at most 128 element(s)"
    # There are 144 skills, so the full set never fits.
    MAX_TOOLS = 128

    # Least load-bearing last. When something has to be dropped, drop the
    # things a failing-over turn is least likely to need: you are already in a
    # degraded path, and "the assistant cannot change a setting right now" is
    # a far better outcome than "the request failed".
    _CATEGORY_PRIORITY = (
        "system", "windows", "shell", "terminal", "files", "browser",
        "apps", "interact", "media", "power", "notify", "web", "memory",
        "llm", "documents", "games", "settings",
    )

    def available(self) -> bool:
        return bool(config.novarouter_api_key())

    def _key(self) -> str:
        return config.novarouter_api_key()

    def _timeout(self) -> float:
        return REQUEST_TIMEOUT

    def _fit_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """Trim to what the gateway will accept, worst-case last.

        This exists because capping Cline's timeout turned a slow primary into
        a *failed request*: Cline would time out, the facade would fail over,
        and NovaRouter would 400 on all 144 tools — so the failover could not
        actually serve an agent turn. Measured directly; see pending.md.

        The order is deterministic so the same 128 go every time, which keeps
        behaviour reproducible and stays friendly to prompt caching. Nothing
        here is clever: it is a budget, and the model genuinely cannot call
        what it is not shown.
        """
        if not tools or len(tools) <= self.MAX_TOOLS:
            return tools

        rank = {name: i for i, name in enumerate(self._CATEGORY_PRIORITY)}
        registry_categories: dict[str, str] = {}
        try:
            from .registry import all_skills

            registry_categories = {
                name: skill.category for name, skill in all_skills().items()
            }
        except Exception:
            pass

        def sort_key(tool: dict) -> tuple[int, str]:
            category = registry_categories.get(tool.get("name", ""), "")
            return (rank.get(category, len(rank)), tool.get("name", ""))

        ordered = sorted(tools, key=sort_key)
        kept, dropped = ordered[: self.MAX_TOOLS], ordered[self.MAX_TOOLS :]
        audit.record(
            f"llm.{self.name}.tools_trimmed",   # "llm.novarouter.…" for NovaRouter
            sent=len(kept), dropped=[t.get("name", "") for t in dropped],
        )
        return kept

    def _base(self) -> str:
        return str(
            config.get("llm.novarouter_base_url", "https://novarouter.site/v1")
        ).rstrip("/")

    def model_name(self, override: str = "") -> str:
        return override or str(config.get("llm.novarouter_model", "kimi-k3"))

    def status(self) -> str:
        if not self.available():
            return (
                "no key — add novarouter_api_key to "
                f"{config.CONFIG_DIR / 'api_keys.json'}, or set NOVAROUTER_API_KEY"
            )
        return f"key present, model {self.model_name()}"

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        model: str = "",
    ) -> Reply:
        key = self._key()
        if not key:
            raise LLMError(f"{self.label} has no API key configured")

        chosen = self.model_name(model)
        payload: dict[str, Any] = {
            "model": chosen,
            "messages": _to_openai_messages(messages, system),
            "temperature": temperature,
        }
        fitted = self._fit_tools(tools)
        if fitted:
            payload["tools"] = [{"type": "function", "function": t} for t in fitted]
            payload["tool_choice"] = "auto"

        try:
            response = requests.post(
                f"{self._base()}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout(),
            )
        except requests.RequestException as exc:
            raise LLMError(f"{self.label} unreachable: {exc}") from exc

        if response.status_code in self.BLOCK_STATUSES:
            raise ProviderBlocked(
                f"{self.label} returned {response.status_code}: {response.text[:200]}",
                self._blocked_cooldown(),
            )
        if response.status_code == 401:
            raise LLMError(f"{self.label} rejected the API key")
        if not response.ok:
            raise LLMError(
                f"{self.label} returned {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{self.label} returned no choices")

        message = choices[0].get("message", {}) or {}
        reply = Reply(
            text=(message.get("content") or "").strip(),
            provider=self.name,
            model=data.get("model") or chosen,
            usage=usage_from_openai(data.get("usage")),
        )
        for call in message.get("tool_calls", []) or []:
            fn = call.get("function", {}) or {}
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (json.JSONDecodeError, TypeError):
                args = {}
            if fn.get("name"):
                reply.tool_calls.append(ToolCall(
                    name=fn["name"], arguments=args, id=call.get("id", ""),
                ))
        return reply


class ZenProvider(NovaRouterProvider):
    """OpenCode Zen (https://opencode.ai/zen) — text-mode primary.

    Same OpenAI-compatible wire shape as NovaRouter, so it reuses its request
    and reply handling. Verified 2026-11-01 against the Zen docs and the live
    `GET https://opencode.ai/inference/v1/models` listing: DeepSeek V4.1 Flash
    is `deepseek-v4.1-flash`, served on `.../inference/openai/v1/chat/completions`
    (the listing reports tool support). `-free` ids exist but are for OpenCode
    clients only, so the default is the paid id.

    Unlike NovaRouter no tool cap is known, so none is applied; if Zen ever
    answers 400 "too many tools" it falls to the next provider like any 4xx.
    """

    name = "zen"
    label = "Zen"
    MAX_TOOLS = 10**6
    # 402 = no account credit, 429 = usage limit. Neither clears on its own in
    # seconds, so skip Zen for a long while rather than pay its round-trip on
    # every request (found live 2026-11-01: 402 "Insufficient account funds").
    BLOCK_STATUSES = (402, 429)

    def _blocked_cooldown(self) -> float:
        return float(config.get("llm.zen_blocked_cooldown_seconds", 1800))

    def available(self) -> bool:
        return bool(config.zen_api_key())

    def _key(self) -> str:
        return config.zen_api_key()

    def _timeout(self) -> float:
        # Capped like Cline: a slow primary must hand over, since cancel
        # cannot interrupt a request already in flight.
        return float(config.get("llm.zen_timeout_seconds", 45))

    def _base(self) -> str:
        return str(config.get(
            "llm.zen_base_url", "https://opencode.ai/inference/openai/v1"
        )).rstrip("/")

    def model_name(self, override: str = "") -> str:
        return override or str(config.get("llm.zen_model", "deepseek-v4.1-flash"))

    def status(self) -> str:
        if not self.available():
            return (
                "no key — add opencode_zen_api_key to "
                f"{config.CONFIG_DIR / 'api_keys.json'}, or set OPENCODE_ZEN_API_KEY"
            )
        return f"key present, model {self.model_name()}"

    def report(self) -> str:
        """`kravein doctor` block: configured or missing, never the key."""
        lines = ["Zen provider (text primary)"]
        lines.append(f"  model    : {self.model_name()}")
        lines.append(f"  endpoint : {self._base()}")
        if not self.available():
            lines.append("  key      : NOT SET — text falls back to the next provider")
            lines.append(f"  Fix      : {self.status()[len('no key — '):]}")
        else:
            lines.append("  key      : present")
        return "\n".join(lines)


def _redact(text: str, *secrets: str) -> str:
    """Remove every secret from ``text`` (an error a gateway echoed back)."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return text


ENDPOINT_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class EndpointProvider(NovaRouterProvider):
    """A generic OpenAI-compatible endpoint configured in settings (#231).

    ``llm.endpoints.<name> = {"base_url", "key", "model", "max_tools"?,
    "timeout"?, "block_statuses"?, "blocked_cooldown"?}``. ``key`` is the
    ``api_keys.json`` name (``config.api_key``), never the key itself. One
    class reaches DeepSeek, Z.ai (GLM) and any other OpenAI-wire gateway, and
    it inherits NovaRouter's request/reply handling, usage parsing and the
    ``_fit_tools`` budget: a gateway with a tool cap must trim, or a failover
    onto it cannot serve an agent turn (hard-won-llm-agent).
    """

    def __init__(self, name: str, spec: dict[str, Any]) -> None:
        self.name = name
        self.label = str(spec.get("label") or name)
        self.spec = spec
        try:
            self.MAX_TOOLS = max(1, int(spec.get("max_tools") or 10**6))
        except (TypeError, ValueError):
            self.MAX_TOOLS = 10**6
        statuses = spec.get("block_statuses", [402, 429])
        self.BLOCK_STATUSES = tuple(
            int(s) for s in (statuses if isinstance(statuses, list) else []) if str(s).isdigit()
        )

    def _blocked_cooldown(self) -> float:
        try:
            return float(self.spec.get("blocked_cooldown", 1800))
        except (TypeError, ValueError):
            return 1800.0

    def key_name(self) -> str:
        return str(self.spec.get("key") or "")

    def _key(self) -> str:
        return config.api_key(self.key_name())

    def available(self) -> bool:
        return bool(self._base() and self._key())

    def _timeout(self) -> float:
        try:
            return float(self.spec.get("timeout", 45))
        except (TypeError, ValueError):
            return 45.0

    def _base(self) -> str:
        return str(self.spec.get("base_url") or "").rstrip("/")

    def model_name(self, override: str = "") -> str:
        return override or str(self.spec.get("model") or "")

    def complete(self, messages, *, system="", tools=None, temperature=0.4, model=""):
        if not self.model_name(model):
            raise LLMError(f"{self.label} has no model configured")
        # The inherited error texts quote the gateway's reply body; a gateway
        # that echoes the Authorization header must not put the key into the
        # audit log or a failover message.
        key = self._key()
        try:
            return super().complete(messages, system=system, tools=tools,
                                    temperature=temperature, model=model)
        except ProviderBlocked as exc:
            raise ProviderBlocked(_redact(str(exc), key), exc.cooldown) from None
        except LLMError as exc:
            raise LLMError(_redact(str(exc), key)) from None

    def report(self) -> str:
        """`kravein doctor` block: configured or missing, never the key."""
        lines = [f"Endpoint '{self.name}' (OpenAI-compatible)"]
        lines.append(f"  model    : {self.model_name() or 'NOT SET'}")
        lines.append(f"  endpoint : {self._base() or 'NOT SET'}")
        if not self.key_name():
            lines.append("  key      : NOT SET — add \"key\": \"<name>_api_key\" to the endpoint")
        elif not self._key():
            lines.append(
                f"  key      : NOT SET — add {self.key_name()} to "
                f"{config.CONFIG_DIR / 'api_keys.json'}, or set KRAVEIN_{self.key_name().upper()}"
            )
        else:
            lines.append("  key      : present")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Anthropic (#231)
# ---------------------------------------------------------------------------

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
ANTHROPIC_SIGNATURE_TAG = "__anthropic__"
# Models that run "preserved thinking": a replayed thinking block is bound to
# the exact earlier transcript, and on accounts created after 2026-08-31 any
# edit is a 400. Kravein's transcript is not append-only (the [context] tail
# is rebuilt every request, history is trimmed and summarised), so these
# models get `drop_block`: a stale block is dropped, never a failed turn.
_PRESERVED_THINKING = re.compile(r"^claude-(opus|sonnet|haiku)-5-5|^claude-(fable|mythos)-5-[12]")
_BINDING_BETA = "thinking-binding-controls-2026-08-01"


def _anthropic_blob(call: dict[str, Any]) -> list[dict] | None:
    sig = call.get("signature")
    if isinstance(sig, dict) and isinstance(sig.get(ANTHROPIC_SIGNATURE_TAG), list):
        return sig[ANTHROPIC_SIGNATURE_TAG]
    return None


def _replayable(msg: dict[str, Any]) -> list[dict] | None:
    """The original content blocks of an Anthropic tool step, or ``None``.

    Replayed only when the blob's tool_use ids are exactly this step's calls:
    anything else means the step was rebuilt, and an edited step is worse
    than a flattened one.
    """
    calls = msg.get("tool_calls") or []
    if not calls:
        return None
    blob = _anthropic_blob(calls[0])
    if blob is None:
        return None
    used = [b.get("id") for b in blob if isinstance(b, dict) and b.get("type") == "tool_use"]
    if used != [c.get("id") for c in calls]:
        return None
    return json.loads(json.dumps(blob))      # a private copy, byte-for-byte content


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict]:
    """The neutral message list as Anthropic ``messages``.

    An assistant tool step from Claude is replayed with its original blocks
    (thinking, redacted_thinking, text, tool_use) unchanged. A step from any
    other provider has no such blocks, so it is flattened to text with its
    results, as the Gemini path does: the model still sees what was done.
    """
    out: list[dict] = []

    def add(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    live: set[str] = set()
    for msg in messages:
        role = msg.get("role", "user")
        if role == "assistant":
            blocks = _replayable(msg)
            if blocks is not None:
                live.update(c.get("id") for c in msg["tool_calls"])
                add("assistant", blocks)
                continue
            parts = [str(msg.get("content") or "").strip()]
            for call in msg.get("tool_calls") or []:
                parts.append(f"[called {call.get('name', '?')}"
                             f"({json.dumps(call.get('arguments', {}), default=str)})]")
            text = "\n".join(p for p in parts if p)
            add("assistant", [{"type": "text", "text": text}] if text else [])
        elif role == "tool":
            key = msg.get("tool_call_id") or ""
            content = str(msg.get("content", "")) or "(empty)"
            if key in live:
                add("user", [{"type": "tool_result", "tool_use_id": key, "content": content}])
            else:
                add("user", [{"type": "text",
                              "text": f"[result of {msg.get('name', 'tool')}]: {content}"}])
        else:
            text = str(msg.get("content", "")).strip()
            add("user", [{"type": "text", "text": text or "(empty)"}])

    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]})
    return out


def _block_param(block: Any) -> dict | None:
    """A response block as the param that replays it unchanged."""
    kind = getattr(block, "type", "")
    if kind == "thinking":
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if kind == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    if kind == "text":
        return {"type": "text", "text": block.text}
    if kind == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name,
                "input": dict(block.input or {})}
    return None


def usage_from_anthropic(raw: Any) -> Usage | None:
    """Anthropic ``usage``: ``input_tokens`` counts only uncached input, so
    cache reads and cache writes are added to keep ``Usage.input`` = every
    prompt token; ``cached`` = the cache reads."""
    if raw is None:
        return None
    fresh = _int(getattr(raw, "input_tokens", None))
    output = _int(getattr(raw, "output_tokens", None))
    if fresh is None and output is None:
        return None
    read = _int(getattr(raw, "cache_read_input_tokens", None)) or 0
    written = _int(getattr(raw, "cache_creation_input_tokens", None)) or 0
    return Usage(input=(fresh or 0) + read + written, cached=read, output=output or 0)


class AnthropicProvider:
    """Claude through the official ``anthropic`` SDK (#231).

    Rules carried here (claude-api skill, Claude 5.5 family): never a forced
    ``tool_choice`` (400 on current models), no ``temperature`` (rejected on
    the current API version), thinking blocks replayed unchanged, the system
    prompt as a cached block so tools + system are one cached prefix. The SDK
    does not retry (``max_retries=0``): the facade's failover is the retry.
    """

    name = "anthropic"
    label = "Anthropic"

    def __init__(self) -> None:
        self._client = None
        self._client_for: tuple[str, float] = ("", 0.0)
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(config.anthropic_api_key())

    def model_name(self, override: str = "") -> str:
        return override or str(config.get("llm.anthropic_model", ANTHROPIC_DEFAULT_MODEL))

    def _timeout(self) -> float:
        return float(config.get("llm.anthropic_timeout_seconds", 60) or 60)

    def _get_client(self, key: str):
        import anthropic

        with self._lock:
            wanted = (key, self._timeout())
            if self._client is None or self._client_for != wanted:
                self._client = anthropic.Anthropic(
                    api_key=key, max_retries=0, timeout=wanted[1],
                )
                self._client_for = wanted
            return self._client

    def _blocked_cooldown(self) -> float:
        return float(config.get("llm.anthropic_blocked_cooldown_seconds", 1800))

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        temperature: float = 0.4,     # accepted for the interface; never sent
        model: str = "",
    ) -> Reply:
        import anthropic

        key = config.anthropic_api_key()
        if not key:
            raise LLMError("Anthropic has no API key configured")
        chosen = self.model_name(model)

        params: dict[str, Any] = {
            "model": chosen,
            "max_tokens": int(config.get("llm.anthropic_max_tokens", 16000)),
            "messages": _to_anthropic_messages(messages),
        }
        if system:
            params["system"] = [{"type": "text", "text": system,
                                 "cache_control": {"type": "ephemeral"}}]
        if tools:
            params["tools"] = [{
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters") or {"type": "object", "properties": {}},
            } for t in tools]
            if not system:
                params["tools"][-1]["cache_control"] = {"type": "ephemeral"}
        betas: list[str] = []
        if _PRESERVED_THINKING.match(chosen):
            params["thinking"] = {"type": "adaptive",
                                  "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
            betas.append(_BINDING_BETA)
        effort = str(config.get("llm.anthropic_effort", "") or "")
        if effort:
            params["output_config"] = {"effort": effort}
        if betas:
            params["betas"] = betas

        try:
            response = self._get_client(key).beta.messages.create(**params)
        except anthropic.APIStatusError as exc:
            status = int(getattr(exc, "status_code", 0) or 0)
            detail = _redact(str(getattr(exc, "message", "") or exc), key)[:200]
            if status == 401:
                raise LLMError("Anthropic rejected the API key") from None
            if status in (402, 429) or (status == 400 and "credit balance" in detail.lower()):
                cooldown = self._blocked_cooldown()
                retry = (getattr(exc, "response", None) and exc.response.headers.get("retry-after")) or ""
                if str(retry).isdigit():
                    # A plain rate limit says when it clears; a 30 s floor
                    # keeps a "retry-after: 1" from re-paying the round-trip.
                    cooldown = max(30.0, float(retry))
                raise ProviderBlocked(f"Anthropic returned {status}: {detail}", cooldown) from None
            raise LLMError(f"Anthropic returned {status}: {detail}") from None
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic unreachable: {_redact(str(exc), key)[:200]}") from None

        if getattr(response, "stop_reason", "") == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            # Fail over: another model in the chain may serve the request.
            raise LLMError(f"Anthropic declined the request (refusal: {category})")

        blocks = [b for b in (_block_param(x) for x in response.content or []) if b]
        reply = Reply(provider=self.name, model=getattr(response, "model", "") or chosen,
                      usage=usage_from_anthropic(getattr(response, "usage", None)))
        reply.text = "".join(b["text"] for b in blocks if b["type"] == "text").strip()
        for block in blocks:
            if block["type"] == "tool_use":
                reply.tool_calls.append(ToolCall(
                    name=block["name"], arguments=dict(block["input"]), id=block["id"],
                ))
        if reply.tool_calls:
            # The whole content rides on the first call: replayed unchanged on
            # the next request (thinking signatures included), flattened to
            # text by every other provider.
            reply.tool_calls[0].signature = {ANTHROPIC_SIGNATURE_TAG: blocks}
        return reply

    def report(self) -> str:
        """`kravein doctor` block: configured or missing, never the key."""
        lines = ["Anthropic provider (Claude)"]
        lines.append(f"  model    : {self.model_name()}")
        if self.available():
            lines.append("  key      : present")
        else:
            lines.append(
                f"  key      : NOT SET — add anthropic_api_key to "
                f"{config.CONFIG_DIR / 'api_keys.json'}, or set ANTHROPIC_API_KEY"
            )
        return "\n".join(lines)




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
        self.novarouter = NovaRouterProvider()
        self.zen = ZenProvider()
        self.anthropic = AnthropicProvider()

        self._failures: dict[str, int] = {}
        self._cooling_until: dict[str, float] = {}
        self._breaker_lock = threading.Lock()

    def _all(self) -> list[Any]:
        """The built-in providers: the ones the global chain may pick up."""
        return [self.zen, self.cline, self.gemini, self.openrouter, self.novarouter]

    def endpoints(self) -> list[EndpointProvider]:
        """Configured OpenAI-compatible endpoints (``llm.endpoints``), rebuilt
        from settings on every call so a settings change applies next turn.

        A name that is not a plain lowercase id, or that would shadow a
        built-in provider or ``anthropic``, is skipped (``doctor`` says so).
        """
        raw = config.get("llm.endpoints") or {}
        if not isinstance(raw, dict):
            return []
        taken = {p.name for p in self._all()} | {self.anthropic.name}
        return [
            EndpointProvider(name, spec) for name, spec in raw.items()
            if isinstance(spec, dict) and ENDPOINT_NAME.match(str(name)) and name not in taken
        ]

    def _extras(self) -> list[Any]:
        """Opt-in providers (#231): used only where a setting names them
        (``llm.provider``, ``provider_order``, ``models.roles``, a profile's
        ``model``), never added to the chain just because a key exists."""
        return [self.anthropic, *self.endpoints()]

    def _named(self) -> dict[str, Any]:
        return {p.name: p for p in [*self._all(), *self._extras()]}

    def known_providers(self) -> set[str]:
        return set(self._named())

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

    def _open_breaker(self, name: str, cooldown: float, *, reason: str = "") -> None:
        """Skip ``name`` for ``cooldown`` seconds starting now."""
        with self._breaker_lock:
            self._load_breaker()
            self._failures[name] = max(
                int(self._failures.get(name, 0)), self._trip_threshold(),
            )
            self._cooling_until[name] = time.time() + cooldown
            audit.record(
                "llm.breaker_open", provider=name, blocked=True,
                cooldown_seconds=round(cooldown), reason=reason[:200],
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
        by_name = self._named()
        builtin = [p.name for p in self._all()]
        preferred = str(config.get("llm.provider", "zen")).lower()
        configured = [str(n).lower() for n in (config.get("llm.provider_order") or [])]

        sequence = [preferred]
        for name in configured:
            if name not in sequence:
                sequence.append(name)

        # provider_order is exclusive when set: a provider left out of it is
        # left out on purpose, not merely deprioritised. Only fall back to
        # "everything available" when no order is configured at all.
        if not configured:
            for name in builtin:          # opt-in providers only when named
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

    # -- roles (#228) -----------------------------------------------------

    def role_setting(self, role: str) -> list[tuple[str, str]] | None:
        """The explicit ``models.roles`` chain for ``role`` alone, or ``None``.

        Entries are ``"provider:model"`` split on the FIRST colon (OpenRouter
        ids hold colons: ``…:free``); ``"provider"`` alone means that
        provider's configured model. An unknown provider is dropped and
        audited, never guessed. Read as a whole dict: role keys and model ids
        are not dotted-path safe.
        """
        roles = config.get("models.roles") or {}
        if not isinstance(roles, dict):
            return None
        raw = roles.get(role)
        if not isinstance(raw, list) or not raw:
            return None
        known = self.known_providers()
        chain: list[tuple[str, str]] = []
        for entry in raw:
            name, _, model = str(entry).partition(":")
            name = name.strip().lower()
            if name not in known:
                audit.record("llm.role_unknown_provider", role=role, entry=str(entry)[:100])
                continue
            chain.append((name, model.strip()))
        return chain or None

    def resolve_role(self, role: str) -> tuple[str, list[tuple[str, str]] | None]:
        """``(role that supplied the chain, chain)``; chain ``None`` = global.

        Inheritance: ``worker:<p>`` → ``orchestrator`` → global; every other
        role → ``orchestrator`` → global. ``""`` (a caller with no role) is the
        global chain, exactly as before roles existed.
        """
        if not role:
            return "", None
        if not valid_role(role):
            raise ValueError(f"unknown model role '{role}' (known: {', '.join(ROLES)}, worker:<profile>)")
        for candidate in (role, "orchestrator"):
            chain = self.role_setting(candidate)
            if chain:
                return candidate, chain
        return "", None

    def _plan(self, role: str, prefer: str) -> list[tuple[Any, str]]:
        """``[(provider, model override)]`` in the order they will be tried.

        No explicit chain for ``role`` (itself or inherited): exactly the
        pre-roles behaviour, ``_ordered()`` with ``prefer`` moved first.
        ``prefer`` (a profile's ``model:``) is only that fallback: an explicit
        setting for the requested role itself wins over it. An inherited
        orchestrator chain still takes ``prefer`` first, so a profile keeps its
        model unless the owner set ``worker:<p>``.
        """
        source, chain = self.resolve_role(role)
        if chain is not None and source == role:
            prefer = ""
        if chain is None:
            order: list[tuple[Any, str]] = [(p, "") for p in self._ordered()]
        else:
            by_name = self._named()
            usable = [(by_name[n], m) for n, m in chain if n in by_name and by_name[n].available()]
            healthy = [(p, m) for p, m in usable if not self._is_cooling(p.name)]
            order = healthy or usable

        preferred_name, _, preferred_model = (prefer or "").partition(":")
        preferred_name = preferred_name.strip().lower()
        if preferred_name:
            extras = {p.name for p in self._extras()}
            if chain is None and preferred_name not in extras:
                # Built-ins: exactly the pre-roles rule (only if in the chain).
                first = [(p, preferred_model.strip()) for p, _ in order if p.name == preferred_name]
            else:
                # The preferred provider joins the chain first even when the
                # chain does not list it, as long as it can be used at all.
                # An opt-in provider is never in the global chain, so naming
                # it in a profile's `model:` is what brings it in.
                provider = self._named().get(preferred_name)
                first = []
                if provider is not None and provider.available() and (
                    not self._is_cooling(provider.name) or not order
                ):
                    first = [(provider, preferred_model.strip())]
            if chain is None:
                order = first + [(p, m) for p, m in order if p.name != preferred_name]
            else:
                order = first + order
        return order

    def _model_label(self, provider: Any) -> str:
        """``model_of`` for the ledger: a label, so it may never raise."""
        try:
            return self.model_of(provider)
        except Exception:
            return ""

    def model_of(self, provider: Any) -> str:
        """The model a provider would use now, as ``status()`` labels it."""
        from .cline import configured_model

        if provider.name == "cline":
            return configured_model()
        if provider.name in ("gemini", "novarouter", "zen") or isinstance(
            provider, (EndpointProvider, AnthropicProvider)
        ):
            return provider.model_name()
        return ""

    def effective(self) -> tuple[str, str]:
        """``(provider, model)`` of the provider that will answer first.

        The HUD used to show ``llm.provider`` next to ``llm.cline_model``, i.e.
        "gemini · nvidia/nemotron": the configured primary with another
        provider's model. Empty strings when nothing is available.
        """
        ordered = self._ordered()
        if not ordered:
            return "", ""
        return ordered[0].name, self.model_of(ordered[0])

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
            model = self.model_of(provider)
            labels.append(f"{provider.name}:{model}" if model else provider.name)

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
        prefer: str = "",
        role: str = "",
        tags: dict[str, str] | None = None,
    ) -> Reply:
        """Get one model turn, failing over between providers.

        ``role`` picks the model chain (``models.roles``, see ``_plan``);
        ``""`` is the global chain. ``prefer`` is ``"provider:model"`` (an
        agent profile's ``model``): that provider is tried first with that
        model unless the role has its own explicit chain. Every other provider
        keeps its own configured model and the breaker/failover run as usual,
        so a bad preference costs one failed attempt, never the request. A
        provider that is unavailable or cooling down is simply not preferred.

        Every attempt, failed ones included, is one row in the usage ledger;
        ``tags`` (``principal``, ``thread``, ``agent``) label it.
        """
        from . import usage as _usage

        if temperature is None:
            temperature = float(config.get("llm.temperature", 0.4))

        order = self._plan(role, prefer)
        if not order:
            if role and self.resolve_role(role)[1] is not None and self._ordered():
                raise LLMError(f"no provider in the '{role}' model chain is available")
            raise LLMError(self.status())

        tags = dict(tags or {})
        ledger = {
            "role": role or "default",
            "principal": str(tags.get("principal") or ""),
            "thread": str(tags.get("thread") or ""),
            "agent": str(tags.get("agent") or ""),
        }

        errors: list[str] = []
        for provider, model in order:
            started = time.monotonic()
            try:
                extra = {"model": model} if model else {}
                reply = provider.complete(
                    messages, system=system, tools=tools, temperature=temperature,
                    **extra,
                )
                _usage.record(
                    provider=provider.name, model=reply.model or model or self._model_label(provider),
                    usage=reply.usage, latency_ms=(time.monotonic() - started) * 1000,
                    ok=True, **ledger,
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
                # Class name only: an error message may echo a request or key.
                _usage.record(
                    provider=provider.name, model=model or self._model_label(provider),
                    latency_ms=(time.monotonic() - started) * 1000,
                    ok=False, error=type(exc).__name__, **ledger,
                )
                errors.append(f"{provider.name}: {exc}")
                audit.record("llm.provider_failed", provider=provider.name, error=str(exc))
                if not _is_quota_error(exc):
                    # A non-quota failure on the preferred provider is still
                    # worth failing over on, but log it as unexpected.
                    audit.record("llm.unexpected_error", provider=provider.name, error=str(exc))
                if isinstance(exc, ProviderBlocked):
                    self._open_breaker(provider.name, exc.cooldown, reason=str(exc))
                else:
                    self._record_failure(provider.name)
                continue

        raise LLMError("No LLM provider could answer. " + " | ".join(errors))

    def chat(self, prompt: str, system: str = "", temperature: float | None = None,
             *, role: str = "") -> str:
        """One-shot completion, no tools."""
        reply = self.complete(
            [{"role": "user", "content": prompt}],
            system=system, temperature=temperature, role=role,
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
