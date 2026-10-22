"""Tests: cline.

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


# Cline provider
# ---------------------------------------------------------------------------

def test_cline_renders_compact_tool_signatures():
    from kravein import cline

    rendered = cline._render_tool({
        "name": "kill_process",
        "description": "Terminate a process.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "force": {"type": "boolean"},
            },
            "required": ["target"],
        },
    })
    # Required args have no '?', optional ones do.
    assert "kill_process(target: string, force?: boolean)" in rendered
    assert "Terminate a process." in rendered


def test_cline_prompt_includes_tools_and_transcript():
    from kravein import cline

    prompt = cline.build_prompt(
        [
            {"role": "user", "content": "what is the volume"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"name": "get_volume", "arguments": {}}]},
            {"role": "tool", "name": "get_volume", "content": "Volume is 60%."},
        ],
        registry.tool_declarations(),
    )
    assert "# Available tools" in prompt
    assert "get_volume" in prompt
    assert "what is the volume" in prompt
    assert "RESULT of get_volume" in prompt
    assert "Volume is 60%." in prompt
    assert '"tool_calls"' in prompt


def test_cline_prompt_trims_huge_tool_output():
    from kravein import cline

    prompt = cline.build_prompt(
        [{"role": "tool", "name": "big", "content": "x" * 20000}], None
    )
    assert "[trimmed]" in prompt
    assert len(prompt) < 8000


def test_cline_parses_ndjson_run_result():
    from kravein import cline

    stream = "\n".join([
        '{"type":"hook_event","hookEventName":"agent_start"}',
        '{"type":"agent_event","event":{"type":"iteration_start","iteration":1}}',
        '{"type":"run_result","finishReason":"completed","text":"the answer",'
        '"usage":{"inputTokens":10},"model":{"id":"deepseek/deepseek-v4-flash"}}',
    ])
    text, meta = cline._parse_ndjson(stream)
    assert text == "the answer"
    assert meta["model"] == "deepseek/deepseek-v4-flash"
    assert meta["finish_reason"] == "completed"


def test_cline_parse_ignores_junk_lines():
    from kravein import cline

    text, _ = cline._parse_ndjson(
        "hostname: command not found\nnot json\n"
        '{"type":"run_result","text":"ok"}'
    )
    assert text == "ok"


def _cline_reply(monkeypatch, model_output: str, tools=None):
    """Drive ClineProvider.complete with a canned CLI response."""
    from kravein import cline

    provider = cline.ClineProvider()
    monkeypatch.setattr(
        cline, "run_cli",
        lambda *a, **k: (model_output, {"model": "deepseek/deepseek-v4-flash"}),
    )
    return provider._complete_via_cli(
        [{"role": "user", "content": "hi"}],
        tools=tools if tools is not None else registry.tool_declarations(),
    )


def test_cline_decodes_a_tool_call(monkeypatch):
    reply = _cline_reply(
        monkeypatch,
        '{"tool_calls":[{"name":"get_volume","arguments":{}}]}',
    )
    assert reply.wants_tools
    assert reply.tool_calls[0].name == "get_volume"


def test_cline_decodes_multiple_tool_calls(monkeypatch):
    reply = _cline_reply(
        monkeypatch,
        '{"tool_calls":[{"name":"get_volume","arguments":{}},'
        '{"name":"system_status","arguments":{"detailed":true}}]}',
    )
    assert [c.name for c in reply.tool_calls] == ["get_volume", "system_status"]
    assert reply.tool_calls[1].arguments == {"detailed": True}


def test_cline_decodes_a_plain_reply(monkeypatch):
    reply = _cline_reply(monkeypatch, '{"reply":"All good."}')
    assert reply.text == "All good."
    assert not reply.wants_tools


def test_cline_handles_fenced_json(monkeypatch):
    reply = _cline_reply(
        monkeypatch, '```json\n{"reply":"fenced answer"}\n```'
    )
    assert reply.text == "fenced answer"


def test_cline_falls_back_to_prose(monkeypatch):
    # The model ignored the protocol; the text must still reach the user.
    reply = _cline_reply(monkeypatch, "I could not parse that request.")
    assert reply.text == "I could not parse that request."
    assert not reply.wants_tools


def test_cline_tolerates_stringified_arguments(monkeypatch):
    reply = _cline_reply(
        monkeypatch,
        '{"tool_calls":[{"name":"set_volume","arguments":"{\\"percent\\": 40}"}]}',
    )
    assert reply.tool_calls[0].arguments == {"percent": 40}


def test_cline_tolerates_a_single_object_tool_call(monkeypatch):
    reply = _cline_reply(
        monkeypatch, '{"tool_calls":{"name":"get_volume","arguments":{}}}'
    )
    assert reply.tool_calls[0].name == "get_volume"


def test_cline_routes_ungated_models_direct(monkeypatch):
    """Ungated models must skip the CLI entirely — that is the whole point."""
    from kravein import cline

    provider = cline.ClineProvider()
    assert provider._use_cli("deepseek/deepseek-v3.2") is False
    assert provider._use_cli("z-ai/glm-4.6") is False
    # deepseek-v4-flash is gated to official Cline surfaces.
    assert provider._use_cli("deepseek/deepseek-v4-flash") is True


def test_cline_direct_path_is_used_by_default(monkeypatch):
    from kravein import cline

    provider = cline.ClineProvider()
    called = {}

    def fake_direct(messages, **kwargs):
        called["direct"] = True
        return {"content": "hi", "tool_calls": []}

    monkeypatch.setattr(cline, "direct_complete", fake_direct)
    monkeypatch.setattr(
        cline, "run_cli",
        lambda *a, **k: pytest.fail("CLI must not run for an ungated model"),
    )
    reply = provider.complete([{"role": "user", "content": "x"}],
                              model="deepseek/deepseek-v3.2")
    assert called.get("direct")
    assert reply.text == "hi"


def test_cline_falls_back_to_cli_when_gated(monkeypatch):
    """A 403 must route to the CLI rather than failing the turn."""
    from kravein import cline

    provider = cline.ClineProvider()
    used = {}

    def gated(*a, **k):
        raise cline.ModelGatedError("only available via Cline product surfaces")

    monkeypatch.setattr(cline, "direct_complete", gated)
    monkeypatch.setattr(
        cline, "run_cli",
        lambda *a, **k: (used.setdefault("cli", True), ('{"reply":"from cli"}', {}))[1],
    )
    reply = provider.complete(
        [{"role": "user", "content": "x"}],
        tools=registry.tool_declarations(),
        model="some/ungated-but-403-at-runtime",
    )
    assert used.get("cli"), "must fall back to the CLI on a gate"
    assert reply.text == "from cli"


def test_cline_direct_decodes_native_tool_calls(monkeypatch):
    from kravein import cline

    provider = cline.ClineProvider()
    monkeypatch.setattr(cline, "direct_complete", lambda *a, **k: {
        "content": None,
        "tool_calls": [
            {"id": "1", "function": {"name": "get_volume", "arguments": "{}"}},
            {"id": "2", "function": {"name": "set_volume",
                                     "arguments": '{"percent": 30}'}},
        ],
    })
    reply = provider.complete([{"role": "user", "content": "x"}],
                              model="deepseek/deepseek-v3.2")
    assert [c.name for c in reply.tool_calls] == ["get_volume", "set_volume"]
    assert reply.tool_calls[1].arguments == {"percent": 30}


def test_cline_openai_message_translation():
    from kravein import cline

    out = cline._to_openai_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "get_volume", "arguments": {}},
        ]},
        {"role": "tool", "name": "get_volume", "tool_call_id": "c1", "content": "50%"},
    ], system="sys")

    assert out[0] == {"role": "system", "content": "sys"}
    assert out[2]["tool_calls"][0]["function"]["name"] == "get_volume"
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == "c1"


def test_cline_token_cache_never_writes_to_clines_own_config():
    from kravein import cline, config

    # Kravein must only ever read Cline's file.
    assert cline._token_cache_path() != cline.CREDENTIALS_PATH
    assert str(config.STATE_DIR) in str(cline._token_cache_path())


def test_agent_prompt_guards_against_overclaiming():
    """m2.5 once called open_url and reported an email as sent.

    The system prompt must forbid substituting a loosely related tool and
    describing the result as the thing the user asked for.
    """
    from kravein.agent import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "never overclaim" in lowered
    assert "not sending an email" in lowered
    assert "know when to stop" in lowered
    # And the anti-loop guidance, aimed at deepseek's runaway behaviour.
    assert "same tool repeatedly" in lowered


def test_cline_default_model_is_not_gated():
    from kravein import cline, config

    model = config.get("llm.cline_model", cline.DEFAULT_MODEL)
    assert model not in cline.CLI_ONLY_MODELS, "default must use the fast path"


def test_cline_auth_reader_never_returns_tokens():
    from kravein import cline

    auth = cline.read_auth()
    rendered = auth.describe() + repr(auth)
    # Whatever the state, no JWT material may appear.
    assert "eyJ" not in rendered
    assert "workos:" not in rendered


def test_cline_workspace_is_isolated_from_the_repo():
    from kravein import cline

    workspace = cline._workspace()
    assert workspace.is_dir()
    # It must not sit inside the user's project.
    assert Path.cwd() not in workspace.parents
    assert "cline-workspace" in str(workspace)


# ---------------------------------------------------------------------------


# Cline grounding
# ---------------------------------------------------------------------------

def test_cline_prompt_overrides_the_harness_tool_context():
    """Regression: the model claimed the listed tools were unavailable.

    The CLI wraps us in its own agent context that advertises other tools and
    reports them blocked; the prompt must explicitly override that.
    """
    from kravein import cline

    prompt = cline.build_prompt(
        [{"role": "user", "content": "close chrome"}],
        registry.tool_declarations(),
    )
    lowered = prompt.lower()
    assert "disregard any other tool list" in lowered
    assert "non-interactive session" in lowered
    assert "never tell the user you lack access" in lowered


def test_cline_prompt_states_the_tool_count():
    from kravein import cline

    tools = registry.tool_declarations()
    prompt = cline.build_prompt([{"role": "user", "content": "hi"}], tools)
    assert f"{len(tools)} of them" in prompt


# ---------------------------------------------------------------------------
