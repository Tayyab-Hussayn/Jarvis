"""Model catalog, probe, role validation and doctor checks (#234).

Providers are stubbed; the probe never reaches a real model and never runs a
skill. Settings writes land in the isolated CONFIG_DIR (tests/conftest.py).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from kravein import config, llm, usage
from test_model_roles import MSG, Named, facade  # noqa: F401  (fixture)


def write_models(**section):
    config.update_settings(lambda s: s.setdefault("models", {}).update(section))


class Scripted(Named):
    """A provider that plays one reply (or exception) per call."""

    def __init__(self, name, script, **kw):
        super().__init__(name, **kw)
        self.script, self.calls = list(script), []

    def complete(self, messages, *, system="", tools=None, temperature=0.4, model=""):
        self.calls.append({"messages": messages, "system": system, "tools": tools, "model": model})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def tool_reply(name="disk_usage", sig=b"sig"):
    return llm.Reply(tool_calls=[llm.ToolCall(name, {"path": "/"}, "c1", signature=sig)],
                     provider="gemini", model="g", usage=llm.Usage(100, 40, 5))


SURFACE = ("SYSTEM", [{"name": "disk_usage", "description": "d", "parameters": {"type": "object"}}])


@pytest.fixture
def surface(monkeypatch):
    monkeypatch.setattr(usage, "_probe_surface", lambda: SURFACE)


def ledger():
    conn = sqlite3.connect(usage.db_path())
    try:
        return conn.execute("SELECT role, provider, model, ok, error FROM calls ORDER BY id").fetchall()
    finally:
        conn.close()


# -- catalog ------------------------------------------------------------------------------

def test_catalog_read_write_and_price_precedence():
    assert usage.catalog() == {} and usage.tool_status("gemini:g") == "untested"
    usage.update_entry("gemini:gemini-3.5-flash-lite", as_of="2026-11-14",
                       price={"input": 0.3, "output": 2.5})
    assert usage.catalog()["gemini:gemini-3.5-flash-lite"]["as_of"] == "2026-11-14"
    assert usage.price_source("gemini", "gemini-3.5-flash-lite") == (
        {"input": 0.3, "output": 2.5}, "catalog")
    # models.prices (#230) still answers when the catalog has no price...
    write_models(prices={"zen:m": {"input": 1, "output": 2},
                         "gemini:gemini-3.5-flash-lite": {"input": 9, "output": 9}})
    assert usage.price_source("zen", "m") == ({"input": 1.0, "output": 2.0}, "prices")
    # ...but the catalog is the one source of truth when both exist.
    assert usage.price_of("gemini", "gemini-3.5-flash-lite")["input"] == 0.3
    assert usage.price_source("zen", "nope") == (None, "")
    # A bad catalog entry is ignored, not fatal.
    write_models(catalog={"x:y": "not a dict", **usage.catalog()})
    assert "x:y" not in usage.catalog()


def test_update_entry_merges():
    usage.update_entry("zen:m", as_of="2026-11-01", context=128000)
    usage.update_entry("zen:m", tool_calling="verified")
    assert usage.catalog()["zen:m"] == {"as_of": "2026-11-01", "context": 128000,
                                        "tool_calling": "verified"}


# -- probe --------------------------------------------------------------------------------

def test_probe_success_records_verified_with_usage(facade, surface):
    facade.gemini = Scripted("gemini", [
        llm.Reply(text="ready", provider="gemini", model="g", usage=llm.Usage(10, 0, 1)),
        tool_reply(),
        llm.Reply(text="Your disk is 41% full.", provider="gemini", model="g",
                  usage=llm.Usage(150, 100, 9)),
    ])
    entry = usage.probe("gemini:g", facade=facade)
    assert entry["key"] == "gemini:g" and entry["tool_calling"] == "verified"
    assert entry["verified_at"] == entry["as_of"] == time.strftime("%Y-%m-%d")
    assert entry["opaque_replay"] is True and entry["streaming"] is False
    assert entry["probe"]["ok"] and entry["probe"]["input"] == 260 and entry["probe"]["cached"] == 140
    calls = facade.gemini.calls
    assert [c["model"] for c in calls] == ["g", "g", "g"]
    assert calls[1]["system"] == "SYSTEM" and calls[1]["tools"] == SURFACE[1]
    replay = calls[2]["messages"]
    assert replay[1]["tool_calls"][0]["signature"] == b"sig"     # opaque block replayed
    assert replay[2]["role"] == "tool" and replay[2]["tool_call_id"] == "c1"
    assert ledger() == [("probe", "gemini", "g", 1, "")] * 3
    assert usage.tool_status("gemini:g") == "verified"


@pytest.mark.parametrize("script,step,reason", [
    ([llm.LLMError("Zen returned 402: key sk-SECRET in body")], "plain", "LLMError 402"),
    ([llm.Reply(text="")], "plain", "empty_reply"),
    ([llm.Reply(text="ready"), llm.Reply(text="I cannot check that.")], "tool_call", "no_tool_call"),
    ([llm.Reply(text="ready"), tool_reply("rm_everything")], "tool_call", "unknown_tool"),
    ([llm.Reply(text="ready"), tool_reply(), RuntimeError("signature lost")], "replay", "RuntimeError"),
])
def test_probe_failures_record_failed_and_why_without_secrets(facade, surface, script, step, reason):
    facade.gemini = Scripted("gemini", script)
    entry = usage.probe("gemini:g", facade=facade)
    assert entry["tool_calling"] == "failed" and "verified_at" not in entry
    assert (entry["probe"]["step"], entry["probe"]["error"]) == (step, reason)
    assert "SECRET" not in str(usage.catalog()) and "SECRET" not in str(ledger())


def test_probe_keeps_a_previous_verified_date_but_marks_failed(facade, surface):
    usage.update_entry("gemini:g", tool_calling="verified", verified_at="2026-10-01",
                       streaming=True)
    facade.gemini = Scripted("gemini", [RuntimeError("down")])
    entry = usage.probe("gemini:g", facade=facade)
    assert entry["tool_calling"] == "failed" and entry["verified_at"] == "2026-10-01"
    assert entry["streaming"] is True                 # a hand-set fact is not overwritten


def test_probe_bare_provider_resolves_its_model_and_unknown_raises(facade, surface):
    facade.gemini = Scripted("gemini", [RuntimeError("x")])
    assert usage.probe("gemini", facade=facade)["key"] == "gemini:gemini-default"
    with pytest.raises(ValueError, match="unknown provider"):
        usage.probe("ghost:m", facade=facade)


def test_probe_of_an_unavailable_provider_fails_without_calls(facade, surface):
    facade.gemini = Scripted("gemini", [], available=False)
    entry = usage.probe("gemini:g", facade=facade)
    assert entry["probe"]["step"] == "setup" and facade.gemini.calls == []


def test_probe_surface_is_the_orchestrators_real_prompt_and_tools():
    from kravein import registry
    from kravein.agents import load_profile

    system, tools = usage._probe_surface()
    expected = registry.resolve_tools(load_profile("orchestrator").tools)
    assert {t["name"] for t in tools} <= expected and "disk_usage" in {t["name"] for t in tools}
    assert "## Your role: orchestrator" in system


def test_cli_probe_and_list(facade, surface, monkeypatch, capsys):
    from kravein import cli

    monkeypatch.setattr(llm, "client", lambda: facade)
    facade.gemini = Scripted("gemini", [llm.Reply(text="ready"), tool_reply(),
                                        llm.Reply(text="41%")])
    assert cli.main(["models", "probe", "gemini:g"]) == 0
    assert "verified" in capsys.readouterr().out
    assert cli.main(["models", "probe"]) == 2
    assert cli.main(["models", "list"]) == 0
    out = capsys.readouterr().out
    assert "gemini:g" in out and "verified" in out


# -- role validation ----------------------------------------------------------------------

def test_agent_roles_need_a_verified_first_choice(facade):
    problems = usage.validate_role_setting("orchestrator", ["gemini:g", "zen:z"], facade)
    assert problems and "untested" in problems[0] and "models probe gemini:g" in problems[0]
    usage.update_entry("gemini:g", tool_calling="failed")
    assert "failed" in usage.validate_role_setting("worker:research", ["gemini:g"], facade)[0]
    usage.update_entry("gemini:g", tool_calling="verified")
    assert usage.validate_role_setting("orchestrator", ["gemini:g", "zen:z"], facade) == []
    # Only the first choice is gated; non-agent roles are checked for shape only.
    assert usage.validate_role_setting("utility", ["zen:unprobed"], facade) == []


def test_role_validation_rejects_bad_input(facade):
    assert "unknown role" in usage.validate_role_setting("boss", ["zen"], facade)[0]
    assert "non-empty list" in usage.validate_role_setting("utility", [], facade)[0]
    assert "non-empty list" in usage.validate_role_setting("utility", "zen", facade)[0]
    assert "unknown provider 'ghost'" in usage.validate_role_setting("utility", ["ghost:x"], facade)[0]


def test_set_role_chain_writes_only_when_valid(facade):
    with pytest.raises(ValueError, match="untested"):
        usage.set_role_chain("orchestrator", ["gemini:g"], facade)
    assert not (config.get("models.roles") or {}).get("orchestrator")
    usage.update_entry("gemini:g", tool_calling="verified")
    usage.set_role_chain("orchestrator", ["gemini:g", " zen:z "], facade)
    assert config.get("models.roles")["orchestrator"] == ["gemini:g", "zen:z"]
    usage.set_role_chain("orchestrator", None, facade)
    assert "orchestrator" not in config.get("models.roles")


# -- doctor -------------------------------------------------------------------------------

def test_doctor_warnings(facade):
    facade.zen._available = False
    write_models(roles={"orchestrator": ["gemini:g", "zen:z"], "utility": ["gemini:g", "cline:c"],
                        "bogus": ["gemini"]})
    usage.update_entry("gemini:g", tool_calling="verified", as_of=time.strftime("%Y-%m-%d"))
    usage.update_entry("cline:c", tool_calling="verified", as_of="2026-01-01")
    usage.update_entry("zen:z", as_of="not-a-date")
    warnings = "\n".join(usage.doctor_warnings(facade))
    assert "role 'orchestrator' has 1 live chain entry" in warnings     # zen unavailable
    assert "role 'utility'" not in warnings                            # two live entries
    assert "role 'bogus' is not a known role" in warnings
    assert "cline:c is" in warnings and "days old" in warnings
    assert "zen:z has no valid as_of" in warnings
    assert "zen:z (orchestrator) is untested" in warnings
    assert "gemini:g (" not in warnings                                # verified: no warning


def test_doctor_warns_about_an_unprobed_global_primary(facade, monkeypatch):
    real = config.get
    monkeypatch.setattr(config, "get", lambda k, d=None: "gemini" if k == "llm.provider"
                        else ([] if k == "llm.provider_order" else real(k, d)))
    assert any("gemini:gemini-default ((global)) is untested" in w
               for w in usage.doctor_warnings(facade))


def test_doctor_prints_the_model_roles_section(monkeypatch, capsys, facade):
    from kravein import cli

    monkeypatch.setattr(usage, "doctor_warnings", lambda facade=None: ["role 'x' needs a failover"])
    try:
        cli.doctor()
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "Model roles" in out and "role 'x' needs a failover" in out
