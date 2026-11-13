"""Generic OpenAI-compatible endpoints (#231): DeepSeek and Z.ai as settings.

All HTTP is stubbed; no test reaches a real gateway.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
import requests

from kravein import audit, config, llm, usage
from test_model_roles import MSG, facade, settings  # noqa: F401  (fixture)

KEY = "sk-deepseek-SECRET-1234"
ENDPOINTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1/", "key": "deepseek_api_key",
                 "model": "deepseek-v4.1-flash", "timeout": 30},
    "zai": {"base_url": "https://api.z.ai/api/paas/v4", "key": "zai_api_key",
            "model": "glm-5.3-flash", "max_tools": 3},
}


class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body or {}, text or json.dumps(body or {})
        self.ok = status < 400

    def json(self):
        return self._body


@pytest.fixture
def keys(monkeypatch):
    found = {"deepseek_api_key": KEY, "zai_api_key": "zai-key-5678"}
    monkeypatch.setattr(config, "api_key", lambda name: found.get(name, ""))
    return found


@pytest.fixture
def posts(monkeypatch):
    """Record every POST; reply with what the test queued."""
    sent, replies = [], []

    def post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return replies.pop(0)

    monkeypatch.setattr(requests, "post", post)
    return sent, replies


def body(content="hi", calls=None, usage_=None, model="deepseek-v4.1-flash"):
    message = {"content": content}
    if calls:
        message["tool_calls"] = calls
    out = {"model": model, "choices": [{"message": message}]}
    if usage_ is not None:
        out["usage"] = usage_
    return out


def endpoint(name="deepseek"):
    return llm.EndpointProvider(name, ENDPOINTS[name])


# -- the provider itself ------------------------------------------------------------------

def test_text_reply_goes_to_the_configured_endpoint(keys, posts):
    sent, replies = posts
    replies.append(Resp(body=body("hello")))
    reply = endpoint().complete(MSG, system="sys")
    assert reply.text == "hello" and reply.provider == "deepseek"
    assert sent[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert sent[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert sent[0]["json"]["model"] == "deepseek-v4.1-flash" and sent[0]["timeout"] == 30.0
    assert sent[0]["json"]["messages"][0] == {"role": "system", "content": "sys"}


def test_tool_call_reply(keys, posts):
    _, replies = posts
    replies.append(Resp(body=body("", calls=[{"id": "c1", "function": {
        "name": "disk_usage", "arguments": '{"path": "/"}'}}])))
    reply = endpoint().complete(MSG, tools=[{"name": "disk_usage", "description": "d",
                                             "parameters": {"type": "object"}}])
    assert [(c.name, c.arguments, c.id) for c in reply.tool_calls] == [("disk_usage", {"path": "/"}, "c1")]


def test_deepseek_cache_hit_and_miss_usage(keys, posts):
    _, replies = posts
    replies.append(Resp(body=body(usage_={
        "prompt_tokens": 1000, "completion_tokens": 50,
        "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200})))
    assert endpoint().complete(MSG).usage == llm.Usage(input=1000, cached=800, output=50)


def test_tool_cap_trims_like_novarouter(keys, posts, monkeypatch):
    sent, replies = posts
    events = []
    monkeypatch.setattr(audit, "record", lambda e, **f: events.append((e, f)))
    replies.append(Resp(body=body("ok", model="glm-5.3-flash")))
    tools = [{"name": f"t{i}", "description": "d", "parameters": {"type": "object"}} for i in range(5)]
    endpoint("zai").complete(MSG, tools=tools)
    assert len(sent[0]["json"]["tools"]) == 3
    assert any(e == "llm.zai.tools_trimmed" and len(f["dropped"]) == 2 for e, f in events)


@pytest.mark.parametrize("status", [402, 429])
def test_quota_statuses_block(keys, posts, status):
    _, replies = posts
    replies.append(Resp(status=status, text="Insufficient Balance"))
    with pytest.raises(llm.ProviderBlocked) as caught:
        endpoint().complete(MSG)
    assert caught.value.cooldown == 1800


def test_errors_never_carry_the_key(keys, posts):
    _, replies = posts
    replies.append(Resp(status=400, text=f'{{"error": "bad header Bearer {KEY}"}}'))
    with pytest.raises(llm.LLMError) as caught:
        endpoint().complete(MSG)
    assert KEY not in str(caught.value) and "[redacted]" in str(caught.value)
    replies.append(Resp(status=429, text=f"limit for {KEY}"))
    with pytest.raises(llm.ProviderBlocked) as caught:
        endpoint().complete(MSG)
    assert KEY not in str(caught.value)


def test_missing_key_or_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(config, "api_key", lambda name: "")
    assert not endpoint().available()
    monkeypatch.setattr(config, "api_key", lambda name: "k")
    assert endpoint().available()
    with pytest.raises(llm.LLMError, match="no model"):
        llm.EndpointProvider("x", {"base_url": "https://x", "key": "k"}).complete(MSG)


def test_config_api_key_env_wins(monkeypatch):
    monkeypatch.setenv("KRAVEIN_DEEPSEEK_API_KEY", "from-env")
    assert config.api_key("deepseek_api_key") == "from-env"
    monkeypatch.delenv("KRAVEIN_DEEPSEEK_API_KEY")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "plain-env")
    assert config.api_key("deepseek_api_key") == "plain-env"
    assert config.api_key("") == ""


# -- the facade ---------------------------------------------------------------------------

def test_endpoints_are_opt_in_and_addressable(facade, monkeypatch, keys, posts):
    settings(monkeypatch, **{"llm.endpoints": ENDPOINTS, "llm.provider": "zen",
                             "llm.provider_order": [],
                             "models.roles": {"utility": ["deepseek:deepseek-chat", "zai"]}})
    # A configured key alone never adds an endpoint to the global chain.
    assert "deepseek" not in [p.name for p in facade._ordered()]
    assert [(p.name, m) for p, m in facade._plan("utility", "")] == [
        ("deepseek", "deepseek-chat"), ("zai", "")]
    _, replies = posts
    replies.append(Resp(body=body("from deepseek", model="deepseek-chat")))
    assert facade.complete(MSG, role="utility").text == "from deepseek"
    # Named as the primary, it leads the global chain.
    settings(monkeypatch, **{"llm.endpoints": ENDPOINTS, "llm.provider": "deepseek",
                             "llm.provider_order": ["deepseek", "gemini"]})
    assert [p.name for p in facade._ordered()] == ["deepseek", "gemini"]


def test_profile_model_can_name_an_endpoint(facade, monkeypatch, keys):
    settings(monkeypatch, **{"llm.endpoints": ENDPOINTS, "llm.provider": "zen",
                             "llm.provider_order": []})
    plan = [(p.name, m) for p, m in facade._plan("worker:research", "deepseek:deepseek-reasoner")]
    assert plan[0] == ("deepseek", "deepseek-reasoner") and plan[1][0] == "zen"
    from kravein import agent as agent_mod
    from conftest import _agent_with

    prof = agent_mod.AgentProfile(name="r", purpose="p", tools=("memory",), model="deepseek:x")
    _agent_with([], profile=prof)       # accepted: the endpoint is a known provider


def test_blocked_endpoint_fails_over_and_opens_the_breaker(facade, monkeypatch, keys, posts):
    settings(monkeypatch, **{"llm.endpoints": ENDPOINTS,
                             "models.roles": {"orchestrator": ["deepseek", "gemini:g"]}})
    events = []
    real = audit.record
    monkeypatch.setattr(audit, "record", lambda e, **f: (events.append((e, f)), real(e, **f)))
    _, replies = posts
    replies.append(Resp(status=402, text=f"no balance {KEY}"))
    assert facade.complete(MSG, role="orchestrator").text == "gemini"
    assert facade._is_cooling("deepseek")
    assert KEY not in json.dumps([f for _, f in events], default=str)
    conn = sqlite3.connect(usage.db_path())
    rows = conn.execute("SELECT provider, ok, error FROM calls ORDER BY id").fetchall()
    conn.close()
    assert rows == [("deepseek", 0, "ProviderBlocked"), ("gemini", 1, "")]


def test_bad_endpoint_names_are_skipped(facade, monkeypatch):
    settings(monkeypatch, **{"llm.endpoints": {
        "zen": ENDPOINTS["deepseek"], "anthropic": ENDPOINTS["deepseek"],
        "Bad Name": ENDPOINTS["deepseek"], "ok": ENDPOINTS["deepseek"], "nodict": "x"}})
    assert [e.name for e in facade.endpoints()] == ["ok"]
    assert facade.zen.name == "zen"     # a built-in is never shadowed


def test_no_endpoints_configured_changes_nothing(facade, monkeypatch):
    settings(monkeypatch, **{"llm.provider": "gemini", "llm.provider_order": []})
    assert [p.name for p in facade._ordered()] == ["gemini", "zen", "cline", "openrouter", "novarouter"]
    assert facade.endpoints() == []


def test_doctor_lists_endpoints_and_anthropic_without_keys(monkeypatch, capsys, keys):
    from kravein import cli

    settings(monkeypatch, **{"llm.endpoints": {**ENDPOINTS, "Bad": ENDPOINTS["zai"]}})
    monkeypatch.setattr(config, "anthropic_api_key", lambda: "sk-ant-SECRET-9999")
    try:
        cli.doctor()
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "Endpoint 'deepseek'" in out and "Endpoint 'zai'" in out
    assert "Anthropic provider" in out and "Endpoint 'Bad': NOT USED" in out
    assert KEY not in out and "zai-key-5678" not in out and "sk-ant-SECRET-9999" not in out
