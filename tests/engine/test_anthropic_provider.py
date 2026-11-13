"""Anthropic provider (#231): the SDK client is stubbed; no request leaves.

Covers the request shape (no forced tool choice, cached system prefix,
drop_block on preserved-thinking models), replies, usage, error mapping,
thinking-block replay across turns and flattening on failover.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

from kravein import audit, config, llm
from kravein.agents import history
from test_model_roles import MSG, facade, settings  # noqa: F401  (fixture)

KEY = "sk-ant-SECRET-abcdef"
TOOLS = [{"name": "disk_usage", "description": "Disk use.",
          "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]


class FakeClient:
    """``client.beta.messages.create(**params)``: records params, plays a script."""

    def __init__(self, script):
        self.script, self.sent = list(script), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **params):
        self.sent.append(json.loads(json.dumps(params)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(*blocks, stop="end_turn", model="claude-haiku-5-5", usage_=None, details=None):
    return NS(content=list(blocks), stop_reason=stop, stop_details=details, model=model,
              usage=usage_ or NS(input_tokens=10, output_tokens=5,
                                 cache_read_input_tokens=0, cache_creation_input_tokens=0))


def text(t):
    return NS(type="text", text=t)


def thinking(t, sig):
    return NS(type="thinking", thinking=t, signature=sig)


def tool_use(id_, name, args):
    return NS(type="tool_use", id=id_, name=name, input=args)


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(config, "anthropic_api_key", lambda: KEY)
    provider = llm.AnthropicProvider()

    def install(*script):
        fake = FakeClient(script)
        monkeypatch.setattr(provider, "_get_client", lambda key: fake)
        return fake

    provider.install = install
    return provider


def status_error(cls, status, message, headers=None):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx2.Response(status, headers=headers or {}, request=request),
               body=None)


# -- request shape and replies ------------------------------------------------------------

def test_text_reply_and_request_shape(claude):
    fake = claude.install(response(text("Hello"), text(" there")))
    reply = claude.complete(MSG, system="You are Kravein.", model="claude-haiku-5-5")
    assert reply.text == "Hello there" and reply.provider == "anthropic" and not reply.tool_calls
    sent = fake.sent[0]
    assert sent["model"] == "claude-haiku-5-5" and sent["max_tokens"] == 16000
    assert sent["system"] == [{"type": "text", "text": "You are Kravein.",
                               "cache_control": {"type": "ephemeral"}}]
    assert "tool_choice" not in sent and "temperature" not in sent
    assert sent["thinking"] == {"type": "adaptive",
                                "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert sent["betas"] == ["thinking-binding-controls-2026-08-01"]
    assert sent["messages"] == [{"role": "user", "content": [{"type": "text", "text": "x"}]}]


def test_older_models_get_no_thinking_config(claude):
    fake = claude.install(response(text("ok"), model="claude-haiku-4-5"))
    claude.complete(MSG, model="claude-haiku-4-5")
    assert "thinking" not in fake.sent[0] and "betas" not in fake.sent[0]


def test_default_model_and_effort_setting(claude, monkeypatch):
    settings(monkeypatch, **{"llm.anthropic_effort": "low"})
    fake = claude.install(response(text("ok")))
    claude.complete(MSG)
    assert fake.sent[0]["model"] == "claude-opus-5-5"
    assert fake.sent[0]["output_config"] == {"effort": "low"}


def test_tool_call_reply_carries_the_blocks(claude):
    fake = claude.install(response(
        thinking("", "sig-1"), text("Checking."), tool_use("toolu_1", "disk_usage", {"path": "/"}),
        stop="tool_use"))
    reply = claude.complete(MSG, tools=TOOLS)
    sent = fake.sent[0]
    assert sent["tools"] == [{"name": "disk_usage", "description": "Disk use.",
                              "input_schema": TOOLS[0]["parameters"],
                              "cache_control": {"type": "ephemeral"}}]   # no system: tools cached
    assert "tool_choice" not in sent
    [call] = reply.tool_calls
    assert (call.name, call.arguments, call.id) == ("disk_usage", {"path": "/"}, "toolu_1")
    assert call.signature == {"__anthropic__": [
        {"type": "thinking", "thinking": "", "signature": "sig-1"},
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "toolu_1", "name": "disk_usage", "input": {"path": "/"}},
    ]}


def test_usage_includes_cache_read_and_creation(claude):
    claude.install(response(text("ok"), usage_=NS(
        input_tokens=100, output_tokens=40, cache_read_input_tokens=9000,
        cache_creation_input_tokens=500)))
    assert claude.complete(MSG).usage == llm.Usage(input=9600, cached=9000, output=40)
    assert llm.usage_from_anthropic(None) is None
    assert llm.usage_from_anthropic(NS(input_tokens=None, output_tokens=None)) is None


# -- errors -------------------------------------------------------------------------------

def test_rate_limit_blocks_with_retry_after(claude):
    claude.install(status_error(anthropic.RateLimitError, 429, "rate limited",
                                {"retry-after": "120"}))
    with pytest.raises(llm.ProviderBlocked) as caught:
        claude.complete(MSG)
    assert caught.value.cooldown == 120.0
    claude.install(status_error(anthropic.RateLimitError, 429, "rate limited", {"retry-after": "2"}))
    with pytest.raises(llm.ProviderBlocked) as caught:
        claude.complete(MSG)
    assert caught.value.cooldown == 30.0                  # floor


@pytest.mark.parametrize("status,cls,message", [
    (402, anthropic.APIStatusError, "payment required"),
    (400, anthropic.BadRequestError, "Your credit balance is too low to access the API"),
    (429, anthropic.RateLimitError, "rate limited"),
])
def test_quota_and_credit_errors_block_for_the_configured_cooldown(claude, status, cls, message):
    claude.install(status_error(cls, status, message))
    with pytest.raises(llm.ProviderBlocked) as caught:
        claude.complete(MSG)
    assert caught.value.cooldown == 1800


def test_other_errors_fail_over_without_the_key(claude):
    claude.install(status_error(anthropic.AuthenticationError, 401, f"invalid x-api-key {KEY}"))
    with pytest.raises(llm.LLMError, match="rejected the API key") as caught:
        claude.complete(MSG)
    assert not isinstance(caught.value, llm.ProviderBlocked)
    claude.install(status_error(anthropic.BadRequestError, 400, f"bad request echo {KEY}"))
    with pytest.raises(llm.LLMError) as caught:
        claude.complete(MSG)
    assert KEY not in str(caught.value) and "[redacted]" in str(caught.value)
    claude.install(anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")))
    with pytest.raises(llm.LLMError, match="unreachable"):
        claude.complete(MSG)


def test_refusal_fails_over(claude):
    claude.install(response(stop="refusal", details=NS(category="cyber")))
    with pytest.raises(llm.LLMError, match="refusal: cyber"):
        claude.complete(MSG)


def test_no_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(config, "anthropic_api_key", lambda: "")
    provider = llm.AnthropicProvider()
    assert not provider.available()
    with pytest.raises(llm.LLMError, match="no API key"):
        provider.complete(MSG)


def test_blocked_claude_opens_the_breaker_and_keeps_the_key_out(facade, claude, monkeypatch):
    facade.anthropic = claude
    settings(monkeypatch, **{"models.roles": {"orchestrator": ["anthropic:claude-sonnet-5-5",
                                                               "gemini:g"]}})
    events = []
    real = audit.record
    monkeypatch.setattr(audit, "record", lambda e, **f: (events.append((e, f)), real(e, **f)))
    claude.install(status_error(anthropic.RateLimitError, 429, f"slow down {KEY}"))
    assert facade.complete(MSG, role="orchestrator").text == "gemini"
    assert facade._is_cooling("anthropic")
    assert KEY not in json.dumps([f for _, f in events], default=str)


# -- thinking replay and failover ---------------------------------------------------------

def agent_step(reply):
    """The assistant message exactly as Agent._ask stores it (agent.py)."""
    return {"role": "assistant", "content": reply.text, "tool_calls": [
        {"id": c.id, "name": c.name, "arguments": c.arguments, "signature": c.signature}
        for c in reply.tool_calls]}


def test_thinking_blocks_replay_unchanged_across_two_turns(claude):
    first = response(thinking("plan", "sig-A"), tool_use("toolu_1", "disk_usage", {"path": "/"}),
                     tool_use("toolu_2", "disk_usage", {"path": "/home"}), stop="tool_use")
    fake = claude.install(first, response(text("Done.")))
    reply = claude.complete(MSG, system="s", tools=TOOLS)

    transcript = [*MSG, agent_step(reply),
                  {"role": "tool", "tool_call_id": "toolu_1", "name": "disk_usage", "content": "10G"},
                  {"role": "tool", "tool_call_id": "toolu_2", "name": "disk_usage", "content": "4G"}]
    # Persisted and reloaded the way the per-thread history store does it.
    stored = json.loads(json.dumps(transcript, default=history._encode))
    for message in stored:
        history._restore_signatures(message)
    assert stored[1]["tool_calls"][0]["signature"] == reply.tool_calls[0].signature

    assert claude.complete(stored + [{"role": "user", "content": "[context] now"}],
                           system="s", tools=TOOLS).text == "Done."
    replayed = fake.sent[1]["messages"]
    assert replayed[1] == {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "plan", "signature": "sig-A"},
        {"type": "tool_use", "id": "toolu_1", "name": "disk_usage", "input": {"path": "/"}},
        {"type": "tool_use", "id": "toolu_2", "name": "disk_usage", "input": {"path": "/home"}},
    ]}
    # Both results in ONE user turn, then the context tail.
    assert replayed[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "10G"},
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "4G"},
        {"type": "text", "text": "[context] now"},
    ]}
    assert len(replayed) == 3


def test_steps_from_other_providers_are_flattened_for_claude(claude):
    fake = claude.install(response(text("ok")))
    transcript = [*MSG,
                  {"role": "assistant", "content": "", "tool_calls": [
                      {"id": "disk_usage", "name": "disk_usage", "arguments": {"path": "/"},
                       "signature": b"gemini-bytes"}]},
                  {"role": "tool", "tool_call_id": "disk_usage", "name": "disk_usage", "content": "10G"},
                  {"role": "user", "content": "and now?"}]
    claude.complete(transcript)
    assert fake.sent[0]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "x"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": '[called disk_usage({"path": "/"})]'}]},
        {"role": "user", "content": [
            {"type": "text", "text": "[result of disk_usage]: 10G"},
            {"type": "text", "text": "and now?"}]},
    ]


def test_a_rebuilt_step_is_flattened_not_replayed():
    blob = {"__anthropic__": [{"type": "tool_use", "id": "toolu_9", "name": "a", "input": {}}]}
    msgs = llm._to_anthropic_messages([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "other", "name": "a", "arguments": {}, "signature": blob}]},
        {"role": "tool", "tool_call_id": "other", "name": "a", "content": "r"}])
    assert msgs[0] == {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]}
    assert msgs[1]["content"][0]["type"] == "text"
    assert msgs[2]["content"][0] == {"type": "text", "text": "[result of a]: r"}


def test_claude_steps_are_flattened_on_failover_to_gemini_and_plain_for_openai():
    blob = {"__anthropic__": [{"type": "thinking", "thinking": "", "signature": "s"},
                              {"type": "tool_use", "id": "toolu_1", "name": "disk_usage",
                               "input": {"path": "/"}}]}
    transcript = [*MSG,
                  {"role": "assistant", "content": "", "tool_calls": [
                      {"id": "toolu_1", "name": "disk_usage", "arguments": {"path": "/"},
                       "signature": blob}]},
                  {"role": "tool", "tool_call_id": "toolu_1", "name": "disk_usage", "content": "10G"}]
    contents = llm._to_gemini_contents(transcript)
    assert contents[1].parts[0].text == '[called disk_usage({"path": "/"})]'
    assert contents[2].parts[0].text == "[result of disk_usage]: 10G"
    wire = llm._to_openai_messages(transcript, "")
    assert wire[1]["tool_calls"][0]["id"] == "toolu_1"
    assert "signature" not in json.dumps(wire)          # thinking never leaves for another provider


def test_history_keeps_the_anthropic_blob_and_drops_garbage():
    good = {"tool_calls": [{"name": "a", "signature": {"__anthropic__": [{"type": "text", "text": "t"}]}}]}
    history._restore_signatures(good)
    assert good["tool_calls"][0]["signature"] == {"__anthropic__": [{"type": "text", "text": "t"}]}
    bad = {"tool_calls": [{"name": "a", "signature": {"__anthropic__": "not a list"}}]}
    history._restore_signatures(bad)
    assert bad["tool_calls"][0]["signature"] is None


def test_anthropic_is_opt_in(facade, monkeypatch):
    monkeypatch.setattr(config, "anthropic_api_key", lambda: KEY)
    settings(monkeypatch, **{"llm.provider": "zen", "llm.provider_order": []})
    assert "anthropic" not in [p.name for p in facade._ordered()]
    assert "anthropic" in facade.known_providers()
