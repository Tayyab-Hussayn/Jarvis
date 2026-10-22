"""Tests: novarouter.

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


# NovaRouter failover provider
# ---------------------------------------------------------------------------

def test_novarouter_stays_inert_without_a_key(monkeypatch):
    """An unkeyed backup must not enter the chain or break the primary."""
    from kravein import config as cfg
    from kravein import llm as llm_mod

    monkeypatch.setattr(cfg, "novarouter_api_key", lambda: "")
    provider = llm_mod.NovaRouterProvider()
    assert provider.available() is False
    assert "no key" in provider.status()

    with pytest.raises(llm_mod.LLMError):
        provider.complete([{"role": "user", "content": "hi"}])


def test_novarouter_joins_the_chain_once_keyed(monkeypatch, tmp_path):
    """The whole point: one provider outage must have somewhere to go."""
    from kravein import config as cfg
    from kravein import llm as llm_mod

    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cfg, "novarouter_api_key", lambda: "nova-key")
    monkeypatch.setattr(llm_mod.config, "novarouter_api_key", lambda: "nova-key")
    monkeypatch.setattr(
        cfg, "get",
        lambda key, default=None: (
            ["cline", "novarouter"] if key == "llm.provider_order" else default
        ),
    )

    facade = llm_mod.LLM()
    names = [p.name for p in facade._ordered()]
    assert "novarouter" in names, f"failover never joins the chain: {names}"


def test_novarouter_sends_openai_tools_and_reads_tool_calls(monkeypatch):
    """This workload lives on native tool calling, so the shape matters."""
    from kravein import llm as llm_mod

    captured: dict = {}

    class _Response:
        status_code = 200
        ok = True

        def json(self):
            return {
                "model": "kimi-k3",
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "function": {
                                "name": "list_windows",
                                "arguments": '{"workspace": "2"}',
                            },
                        }],
                    }
                }],
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["auth"] = headers["Authorization"]
        captured["payload"] = json
        return _Response()

    monkeypatch.setattr(llm_mod.config, "novarouter_api_key", lambda: "nova-key")
    monkeypatch.setattr(llm_mod.requests, "post", fake_post)

    provider = llm_mod.NovaRouterProvider()
    reply = provider.complete(
        [{"role": "user", "content": "what is open"}],
        tools=[{"name": "list_windows", "description": "d", "parameters": {}}],
    )

    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer nova-key"
    assert captured["payload"]["model"] == "kimi-k3"
    assert captured["payload"]["tools"][0]["type"] == "function"
    assert reply.tool_calls[0].name == "list_windows"
    assert reply.tool_calls[0].arguments == {"workspace": "2"}
    assert reply.provider == "novarouter"


def test_novarouter_reports_a_rejected_key_clearly(monkeypatch):
    from kravein import llm as llm_mod

    class _Response:
        status_code = 401
        ok = False
        text = "unauthenticated"

    monkeypatch.setattr(llm_mod.config, "novarouter_api_key", lambda: "bad")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Response())

    with pytest.raises(llm_mod.LLMError, match="rejected the API key"):
        llm_mod.NovaRouterProvider().complete([{"role": "user", "content": "hi"}])


def test_cline_direct_timeout_is_capped_and_configurable(monkeypatch):
    """A slow primary must hand over, not hold the turn.

    Cancellation only bites between tool rounds, so a request in flight is a
    window where Stop can do nothing at all. It sat at 180s; one measured turn
    spent 52s inside a single call.
    """
    from kravein import cline as cline_mod

    seen: dict = {}

    class _Response:
        status_code = 200
        ok = True

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(cline_mod, "access_token", lambda force=False: "tok")
    monkeypatch.setattr(cline_mod, "configured_model", lambda: "m")
    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    monkeypatch.setattr(
        cline_mod.config, "get",
        lambda key, default=None: 45 if key == "llm.cline_timeout_seconds" else default,
    )
    cline_mod.direct_complete([{"role": "user", "content": "hi"}])
    assert seen["timeout"] == 45

    # Configurable, for workloads that legitimately take longer.
    monkeypatch.setattr(
        cline_mod.config, "get",
        lambda key, default=None: 90 if key == "llm.cline_timeout_seconds" else default,
    )
    cline_mod.direct_complete([{"role": "user", "content": "hi"}])
    assert seen["timeout"] == 90


def test_novarouter_fits_the_tool_list_to_the_gateway_cap():
    """A failover that 400s is not a failover.

    Capping Cline's timeout turned a slow primary into a *failed request*:
    Cline timed out, the facade failed over, and NovaRouter rejected all 144
    tools with `Array must contain at most 128 element(s)`. Verified against
    the live API before this trim existed.
    """
    from kravein import llm as llm_mod
    from kravein import registry

    registry.load_skills()
    tools = registry.tool_declarations()
    assert len(tools) > llm_mod.NovaRouterProvider.MAX_TOOLS, (
        "this test is meaningless if the skill count drops below the cap"
    )

    provider = llm_mod.NovaRouterProvider()
    fitted = provider._fit_tools(tools)
    assert len(fitted) == provider.MAX_TOOLS

    # Deterministic: same input, same 128, so behaviour is reproducible and
    # prompt caching is not defeated by a shuffling tool list.
    assert [t["name"] for t in fitted] == [t["name"] for t in provider._fit_tools(tools)]

    # The core of the assistant survives the cut; a degraded path that cannot
    # look at the system or its windows is not worth failing over to.
    kept = {t["name"] for t in fitted}
    for essential in ("list_windows", "system_status", "run_command", "read_file"):
        assert essential in kept, f"{essential} was dropped from the failover set"

    # Under the cap it is left completely alone.
    assert provider._fit_tools(tools[:10]) == tools[:10]
    assert provider._fit_tools(None) is None


# ---------------------------------------------------------------------------
