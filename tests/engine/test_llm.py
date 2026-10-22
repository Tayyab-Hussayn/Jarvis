"""Tests: llm.

Split out of the legacy tests/test_linux_port.py with no change to any test body.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import llm, registry, safety  # noqa: E402,F401
from kravein.safety import Danger  # noqa: E402,F401
from conftest import _agent_with  # noqa: E402


# LLM plumbing
# ---------------------------------------------------------------------------

def test_tool_declarations_are_wellformed():
    for decl in registry.tool_declarations():
        assert decl["name"] and decl["description"]
        assert decl["parameters"]["type"] == "object"


def test_gemini_sanitizer_preserves_parameter_names():
    clean = llm._sanitize_tools(registry.tool_declarations())
    run_command = next(t for t in clean if t["name"] == "run_command")
    assert "command" in run_command["parameters"]["properties"]


def test_gemini_sanitizer_strips_unknown_keys():
    dirty = [{
        "name": "x", "description": "d",
        "parameters": {
            "type": "object",
            "additionalProperties": False,   # Gemini rejects this
            "properties": {"a": {"type": "string", "default": "z"}},
            "required": ["a"],
        },
    }]
    clean = llm._sanitize_tools(dirty)[0]
    assert "additionalProperties" not in clean["parameters"]
    assert "default" not in clean["parameters"]["properties"]["a"]
    assert clean["parameters"]["properties"]["a"]["type"] == "string"


def test_gemini_sanitizer_omits_empty_parameters():
    clean = llm._sanitize_tools([
        {"name": "x", "description": "d",
         "parameters": {"type": "object", "properties": {}, "required": []}}
    ])[0]
    assert "parameters" not in clean


def test_gemini_tool_calls_carry_a_thought_signature():
    """Regression: Gemini 3.x rejects replayed calls with no signature.

    Sending a function call back in history without its thought_signature
    fails the request with 400 INVALID_ARGUMENT, which silently pushed the
    whole turn onto the fallback provider.
    """
    call = llm.ToolCall(name="get_volume", arguments={}, id="c1", signature=b"sig")
    assert call.signature == b"sig"
    # Default must exist so non-Gemini providers need no changes.
    assert llm.ToolCall(name="x", arguments={}).signature is None


def test_gemini_history_replays_the_signature():
    from google.genai import types  # noqa: F401  (import guard)

    contents = llm._to_gemini_contents([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "get_volume", "arguments": {}, "signature": b"abc"},
        ]},
        {"role": "tool", "name": "get_volume", "content": "50%"},
    ])
    model_turn = next(c for c in contents if c.role == "model")
    part = model_turn.parts[0]
    assert part.function_call.name == "get_volume"
    assert part.thought_signature == b"abc"


def test_gemini_history_survives_a_missing_signature():
    # Older providers and replayed transcripts may not have one.
    contents = llm._to_gemini_contents([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "get_volume", "arguments": {}},
        ]},
    ])
    model_turn = next(c for c in contents if c.role == "model")
    assert model_turn.parts[0].function_call.name == "get_volume"


def test_agent_preserves_signatures_in_its_transcript():
    script = [
        llm.Reply(tool_calls=[
            llm.ToolCall("get_volume", {}, "c1", signature=b"zz")
        ], provider="stub"),
        llm.Reply(text="done", provider="stub"),
    ]
    agent, _ = _agent_with(script, confirmer=lambda *_: True)
    agent.ask("volume?")
    assistant = next(m for m in agent.messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert assistant["tool_calls"][0]["signature"] == b"zz"


def test_gemini_default_model_is_not_the_retired_one():
    from kravein import config

    # gemini-2.5-flash returns 404 for keys created after its retirement.
    assert config.get("llm.gemini_model") != "gemini-2.5-flash"
    assert config.get("llm.provider") in ("gemini", "cline", "openrouter")


def test_gemini_uses_exactly_one_model():
    """No intra-Gemini retry chain: one model, then the next provider."""
    provider = llm.GeminiProvider()
    assert provider.model_name() == "gemini-3.6-flash"
    assert provider.model_name("gemini-3.5-flash") == "gemini-3.5-flash"
    assert not hasattr(provider, "_models_to_try")


def test_gemini_overload_propagates_instead_of_retrying(monkeypatch):
    provider = llm.GeminiProvider()
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("503 UNAVAILABLE. The model is overloaded.")

    monkeypatch.setattr(provider, "_complete_one", boom)
    with pytest.raises(RuntimeError):
        provider.complete([{"role": "user", "content": "x"}])
    assert len(calls) == 1, "must not retry other Gemini models"


def test_breaker_skips_a_failing_provider(monkeypatch, tmp_path):
    """An outage must cost the timeout once, not on every request."""
    from kravein import config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    _real_get = config.get
    monkeypatch.setattr(
        config, "get",
        lambda k, d=None: ["gemini", "cline"] if k == "llm.provider_order"
        else ("gemini" if k == "llm.provider" else _real_get(k, d)),
    )
    facade = llm.LLM()
    attempts = {"gemini": 0, "cline": 0}

    class Broken:
        name = "gemini"
        def available(self): return True
        def model_name(self, override=""): return "gemini-3.6-flash"
        def complete(self, *a, **k):
            attempts["gemini"] += 1
            raise RuntimeError("503 UNAVAILABLE. The model is overloaded.")

    class Standby:
        name = "cline"
        def available(self): return True
        def complete(self, *a, **k):
            attempts["cline"] += 1
            return llm.Reply(text="ok", provider="cline", model="deepseek/deepseek-v3.2")

    facade.gemini = Broken()
    facade.cline = Standby()
    facade.openrouter = type("X", (), {"name": "openrouter",
                                       "available": lambda self: False})()

    for _ in range(5):
        facade.complete([{"role": "user", "content": "x"}])

    # Threshold is 2, so gemini is tried twice then skipped entirely.
    assert attempts["gemini"] == 2, f"gemini retried too often: {attempts}"
    assert attempts["cline"] == 5
    assert "gemini" in facade.breaker_status()


def test_breaker_survives_a_new_process(monkeypatch, tmp_path):
    from kravein import config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)

    first = llm.LLM()
    first._record_failure("gemini")
    first._record_failure("gemini")

    # A completely separate instance, as if a new `kravein ask` had started.
    second = llm.LLM()
    assert second._is_cooling("gemini"), "breaker state must persist to disk"


def test_breaker_resets_after_a_success(monkeypatch, tmp_path):
    from kravein import config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    _real_get = config.get
    monkeypatch.setattr(
        config, "get",
        lambda k, d=None: ["gemini", "cline"] if k == "llm.provider_order"
        else ("gemini" if k == "llm.provider" else _real_get(k, d)),
    )
    facade = llm.LLM()
    facade._record_failure("gemini")
    facade._record_failure("gemini")
    assert facade._is_cooling("gemini")

    facade._record_success("gemini")
    assert not facade._is_cooling("gemini")
    assert facade.breaker_status() == "all providers healthy"


def test_breaker_never_leaves_zero_providers(monkeypatch, tmp_path):
    """If everything is cooling, try anyway rather than refusing outright."""
    from kravein import config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    facade = llm.LLM()
    for name in ("gemini", "cline", "openrouter"):
        facade._record_failure(name)
        facade._record_failure(name)

    monkeypatch.setattr(facade.gemini, "available", lambda: True)
    monkeypatch.setattr(facade.cline, "available", lambda: True)
    assert facade._ordered(), "must still return something to try"


def test_gemini_timeout_is_clamped_to_the_api_minimum():
    # Gemini rejects a deadline under 10s outright, which would break every
    # request instead of only slow ones.
    assert llm.GEMINI_MIN_TIMEOUT == 10.0


def test_default_provider_is_cline_not_gemini():
    """Gemini proved unreliable on this account, so it does not lead.

    It stays configured for voice (Live native-audio) and vision only.
    """
    from kravein import config

    assert config.get("llm.provider") == "cline"
    assert "gemini" not in (config.get("llm.provider_order") or [])
    assert config.get("llm.cline_model").endswith(":free"), \
        "default must run on a zero balance"


def test_gemini_remains_available_for_voice_and_vision():
    from kravein import config, live

    # Removing Gemini from the text chain must not touch the voice pipeline.
    assert live.LIVE_MODEL == "models/gemini-2.5-flash-native-audio-preview-12-2025"
    assert config.get("voice.live_voice") == "Charon"
    assert config.get("voice.tts_voice") == "en-US-GuyNeural"
    assert config.get("llm.gemini_model")  # still set, for vision


def test_provider_order_is_exclusive(monkeypatch):
    """A provider left out of provider_order is left out on purpose."""
    from kravein import config

    facade = llm.LLM()
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: {
            "llm.provider": "cline",
            "llm.provider_order": ["cline"],
        }.get(key, default),
    )
    monkeypatch.setattr(facade.cline, "available", lambda: True)
    monkeypatch.setattr(facade.gemini, "available", lambda: True)
    assert [p.name for p in facade._ordered()] == ["cline"]


def test_failover_hands_gemini_503_to_cline(monkeypatch, tmp_path):
    """The whole point: one clear hop when Gemini is overloaded."""
    from kravein import config

    # Isolate breaker state so a real outage on this machine cannot leak in.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    # This test is about a two-provider chain; the shipped default is
    # cline-only, so declare the order explicitly.
    _real_get = config.get
    monkeypatch.setattr(
        config, "get",
        lambda k, d=None: ["gemini", "cline"] if k == "llm.provider_order"
        else ("gemini" if k == "llm.provider" else _real_get(k, d)),
    )
    facade = llm.LLM()

    class Overloaded:
        name = "gemini"
        def available(self): return True
        def model_name(self, override=""): return "gemini-3.6-flash"
        def complete(self, *a, **k):
            raise RuntimeError("503 UNAVAILABLE. The model is overloaded.")

    class Standby:
        name = "cline"
        def available(self): return True
        def complete(self, *a, **k):
            return llm.Reply(text="from cline", provider="cline",
                             model="deepseek/deepseek-v3.2")

    facade.gemini = Overloaded()
    facade.cline = Standby()
    facade.openrouter = type("X", (), {"name": "openrouter",
                                       "available": lambda self: False})()

    assert [p.name for p in facade._ordered()] == ["gemini", "cline"]
    reply = facade.complete([{"role": "user", "content": "x"}])
    assert reply.provider == "cline"
    assert reply.model == "deepseek/deepseek-v3.2"


def test_live_fallback_chain_is_audio_capable():
    from kravein import live

    assert live.LIVE_MODEL in live.LIVE_FALLBACKS
    assert live.LIVE_FALLBACKS[0] == live.LIVE_MODEL
    for model in live.LIVE_FALLBACKS:
        assert "audio" in model or "live" in model


def test_extract_json_handles_fenced_output():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    assert llm.extract_json("not json at all") == {}


# ---------------------------------------------------------------------------
