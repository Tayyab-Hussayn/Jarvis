"""Reply.usage per provider and the usage ledger (#228).

Usage numbers are only the provider's own: a reply without a report has
``usage is None``. The ledger gets one row per attempt, failed ones included,
and a ledger failure never breaks a turn.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
import requests

from kravein import audit, config, llm, usage
from kravein import cline as cline_mod
from test_model_roles import MSG, Named, facade, settings  # noqa: F401  (fixture)


# -- Reply.usage per provider --------------------------------------------------------------

class _Resp:
    status_code = 200
    ok = True
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


OPENAI_BODY = {
    "model": "served-model",
    "choices": [{"message": {"content": "hi"}}],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 40,
              "prompt_tokens_details": {"cached_tokens": 1000}},
}


@pytest.mark.parametrize("cls,keyattr", [(llm.ZenProvider, "zen_api_key"),
                                         (llm.NovaRouterProvider, "novarouter_api_key")])
def test_openai_wire_gateways_fill_usage(monkeypatch, cls, keyattr):
    monkeypatch.setattr(config, keyattr, lambda: "k")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(OPENAI_BODY))
    reply = cls().complete(MSG)
    assert reply.usage == llm.Usage(input=1200, cached=1000, output=40)

    body = dict(OPENAI_BODY)
    del body["usage"]
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(body))
    assert cls().complete(MSG).usage is None


def test_openrouter_fills_usage(monkeypatch):
    provider = llm.OpenRouterProvider()
    monkeypatch.setattr(provider, "_post", lambda payload: OPENAI_BODY)
    assert provider.complete(MSG, model="m").usage == llm.Usage(1200, 1000, 40)


def test_openai_usage_parsing_edges():
    assert llm.usage_from_openai(None) is None
    assert llm.usage_from_openai({}) is None
    assert llm.usage_from_openai({"total_tokens": 5}) is None
    # DeepSeek reports cache hits under its own key; cached defaults to 0.
    assert llm.usage_from_openai({"prompt_tokens": 10, "completion_tokens": 2,
                                  "prompt_cache_hit_tokens": 7}) == llm.Usage(10, 7, 2)
    assert llm.usage_from_openai({"prompt_tokens": 10, "completion_tokens": 2}) == llm.Usage(10, 0, 2)


def test_gemini_fills_usage_from_usage_metadata(monkeypatch):
    part = SimpleNamespace(text="answer", function_call=None)
    response = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
        usage_metadata=SimpleNamespace(prompt_token_count=900, cached_content_token_count=None,
                                       candidates_token_count=30, thoughts_token_count=12),
    )
    provider = llm.GeminiProvider()
    provider._client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kw: response))
    reply = provider.complete(MSG, model="g")
    assert reply.text == "answer"
    assert reply.usage == llm.Usage(input=900, cached=0, output=42)   # thinking is output

    response.usage_metadata = None
    assert provider.complete(MSG, model="g").usage is None


def test_cline_direct_fills_usage(monkeypatch):
    monkeypatch.setattr(cline_mod, "access_token", lambda force=False: "tok")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp({"data": OPENAI_BODY}))
    message = cline_mod.direct_complete(MSG, model="m")
    assert message["content"] == "hi"

    monkeypatch.setattr(cline_mod, "direct_complete", lambda *a, **k: message)
    provider = cline_mod.ClineProvider()
    monkeypatch.setattr(provider, "_use_cli", lambda model: False)
    assert provider.complete(MSG, model="m").usage == llm.Usage(1200, 1000, 40)


def test_cline_cli_fills_usage(monkeypatch):
    provider = cline_mod.ClineProvider()
    meta = {"model": "m", "usage": {"inputTokens": 100, "outputTokens": 9, "cacheReadTokens": 50}}
    monkeypatch.setattr(cline_mod, "run_cli", lambda prompt, **kw: ("plain answer", meta))
    reply = provider._complete_via_cli(MSG, model="m")
    assert reply.usage == llm.Usage(input=150, cached=50, output=9)

    monkeypatch.setattr(cline_mod, "run_cli", lambda prompt, **kw: ("plain answer", {"model": "m"}))
    assert provider._complete_via_cli(MSG, model="m").usage is None


# -- ledger rows ---------------------------------------------------------------------------

def rows():
    conn = sqlite3.connect(usage.db_path())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM calls ORDER BY id")]
    finally:
        conn.close()


def _with_usage(provider, u):
    original = provider.complete

    def complete(messages, **kw):
        reply = original(messages, **kw)
        reply.usage = u
        return reply

    provider.complete = complete


def test_success_writes_one_row_with_tags_and_tokens(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"utility": ["gemini:flash"]}})
    _with_usage(facade.gemini, llm.Usage(100, 20, 5))
    facade.complete(MSG, role="utility",
                    tags={"principal": "owner", "thread": "t9", "agent": "research"})
    [row] = rows()
    assert row["ok"] == 1 and row["error"] == ""
    assert (row["role"], row["provider"], row["model"]) == ("utility", "gemini", "flash")
    assert (row["principal"], row["thread"], row["agent"]) == ("owner", "t9", "research")
    assert (row["input"], row["cached"], row["output"]) == (100, 20, 5)
    assert row["latency_ms"] >= 0 and row["ts"] > 0
    assert row["cost"] is None                      # no price configured


def test_failover_writes_a_row_per_attempt(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"orchestrator": ["zen:a", "cline:b", "gemini:c"]}})
    facade.zen.fail = True

    def blocked(*a, **k):
        raise llm.ProviderBlocked("Cline returned 402: key sk-secret", 60)

    facade.cline.complete = blocked
    assert facade.complete(MSG, role="orchestrator").text == "gemini"
    got = [(r["provider"], r["model"], r["ok"], r["error"]) for r in rows()]
    assert got == [("zen", "a", 0, "RuntimeError"), ("cline", "b", 0, "ProviderBlocked"),
                   ("gemini", "c", 1, "")]
    assert all(r["input"] is None for r in rows())  # the stub reported nothing
    assert "sk-secret" not in str(rows())           # error class only, never the message


def test_no_role_is_ledgered_as_default(facade):
    facade.complete(MSG)
    assert rows()[0]["role"] == "default"


def test_cost_only_when_a_price_is_configured(facade, monkeypatch):
    settings(monkeypatch, **{
        "models.roles": {"utility": ["gemini:gemini-3.5-flash-lite"]},
        # Dotted model ids must work: prices are read as a whole dict.
        "models.prices": {"gemini:gemini-3.5-flash-lite":
                          {"input": 1.0, "cached": 0.25, "output": 4.0}},
    })
    _with_usage(facade.gemini, llm.Usage(input=1_000_000, cached=400_000, output=100_000))
    facade.complete(MSG, role="utility")
    # 600k fresh * $1 + 400k cached * $0.25 + 100k out * $4 = 0.6 + 0.1 + 0.4
    assert rows()[0]["cost"] == pytest.approx(1.1)
    assert usage.cost_of("gemini", "other", llm.Usage(1, 0, 1)) is None
    assert usage.cost_of("gemini", "gemini-3.5-flash-lite", None) is None


def test_cached_price_defaults_to_the_input_price(monkeypatch):
    settings(monkeypatch, **{"models.prices": {"zen:m": {"input": 2.0, "output": 0}}})
    assert usage.cost_of("zen", "m", llm.Usage(500_000, 500_000, 0)) == pytest.approx(1.0)
    settings(monkeypatch, **{"models.prices": {"zen:m": {"input": "x", "output": 1}}})
    assert usage.cost_of("zen", "m", llm.Usage(1, 0, 1)) is None


def test_a_broken_ledger_never_breaks_the_turn(facade, monkeypatch):
    events = []
    monkeypatch.setattr(audit, "record", lambda event, **f: events.append((event, f)))

    def broken():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(usage, "_connect", broken)
    assert facade.complete(MSG).text
    assert any(e == "usage.write_failed" and "disk I/O" in f["error"] for e, f in events)


def test_report_and_cli(facade, monkeypatch, capsys):
    settings(monkeypatch, **{"models.roles": {"utility": ["zen:a", "gemini:b"]},
                             "models.prices": {"gemini:b": {"input": 1, "output": 1}}})
    facade.zen.fail = True
    _with_usage(facade.gemini, llm.Usage(1000, 0, 1000))
    facade.complete(MSG, role="utility")
    facade.complete(MSG, role="utility")
    summary = {(r["role"], r["provider"]): r for r in usage.summary(1)}
    assert summary[("utility", "zen")]["calls"] == 2 and summary[("utility", "zen")]["failures"] == 2
    assert summary[("utility", "gemini")]["input"] == 2000
    assert summary[("utility", "gemini")]["cost"] == pytest.approx(0.004)
    assert summary[("utility", "zen")]["cost"] is None

    from kravein.cli import main

    assert main(["usage", "--days", "1"]) == 0
    out = capsys.readouterr().out
    assert "gemini:b" in out and "$0.0040" in out and "zen:a" in out
    assert "(+2 unreported)" in out                 # zen's failures reported no tokens


def test_report_with_no_rows():
    assert "No model calls" in usage.report(3)
