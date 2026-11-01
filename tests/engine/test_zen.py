"""Tests: OpenCode Zen provider (DeepSeek V4.1 Flash, text-mode primary)."""

from __future__ import annotations

import json
import os

import pytest
import requests

from kravein import audit, config, llm


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self.ok = status < 400
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def _keyed(monkeypatch, key="zen-key"):
    monkeypatch.setattr(config, "zen_api_key", lambda: key)


def test_defaults_point_at_the_verified_endpoint_and_model():
    p = llm.ZenProvider()
    assert p._base() == "https://opencode.ai/inference/openai/v1"
    assert p.model_name() == "deepseek-v4.1-flash"
    llm_defaults = config.DEFAULT_SETTINGS["llm"]
    assert llm_defaults["provider"] == "zen"
    assert llm_defaults["provider_order"] == ["zen", "cline", "novarouter"]


def test_settings_snapshotting_the_old_defaults_pick_up_zen(monkeypatch, tmp_path):
    """A saved settings.json pinned cline first and hid the new default."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_settings_cache", None)
    (tmp_path / "settings.json").write_text(json.dumps({"llm": {
        "provider": "cline", "provider_order": ["cline", "novarouter"],
        "cline_model": "keep-me",
    }}))
    assert config.get("llm.provider") == "zen"
    assert config.get("llm.provider_order")[0] == "zen"
    assert config.get("llm.cline_model") == "keep-me"


def test_an_explicit_other_choice_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_settings_cache", None)
    (tmp_path / "settings.json").write_text(json.dumps({"llm": {
        "provider": "cline", "provider_order": ["cline"],
    }}))
    assert config.get("llm.provider") == "cline"
    assert config.get("llm.provider_order") == ["cline"]


def test_missing_key_means_unavailable(monkeypatch):
    p = llm.ZenProvider()
    assert p.available() is False
    assert "no key" in p.status()
    assert "NOT SET" in p.report()
    with pytest.raises(llm.LLMError, match="no API key"):
        p.complete([{"role": "user", "content": "hi"}])


def test_key_resolution_from_env_and_file(monkeypatch, tmp_path):
    monkeypatch.undo()  # undo the autouse stub: exercise the real reader
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAVEIN_OPENCODE_ZEN_API_KEY", raising=False)
    assert config.zen_api_key() == ""
    (tmp_path / "api_keys.json").write_text(json.dumps({"opencode_zen_api_key": "from-file"}))
    assert config.zen_api_key() == "from-file"
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "from-env")
    assert config.zen_api_key() == "from-env"


def test_request_shape_and_tool_call_parsing(monkeypatch):
    _keyed(monkeypatch)
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout)
        return _Resp(body={
            "model": "deepseek-v4.1-flash",
            "choices": [{"message": {"content": "", "tool_calls": [{
                "id": "c1",
                "function": {"name": "list_windows", "arguments": '{"workspace": "2"}'},
            }]}}],
        })

    monkeypatch.setattr(llm.requests, "post", fake_post)
    tools = [{"name": f"t{i}", "description": "d", "parameters": {}} for i in range(200)]
    tools[0]["name"] = "list_windows"
    reply = llm.ZenProvider().complete(
        [{"role": "user", "content": "x"}], system="sys", tools=tools,
    )

    assert seen["url"] == "https://opencode.ai/inference/openai/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer zen-key"
    assert seen["payload"]["model"] == "deepseek-v4.1-flash"
    assert seen["payload"]["messages"][0] == {"role": "system", "content": "sys"}
    assert len(seen["payload"]["tools"]) == 200  # no Nova-style 128 cap
    assert seen["payload"]["tools"][0]["type"] == "function"
    assert seen["timeout"] == 45
    assert reply.provider == "zen"
    assert reply.tool_calls[0].name == "list_windows"
    assert reply.tool_calls[0].arguments == {"workspace": "2"}


@pytest.mark.parametrize("outcome", [
    _Resp(503, text="down"), _Resp(401, text="no"), requests.Timeout("slow"),
])
def test_failure_modes_raise_so_the_facade_fails_over(monkeypatch, outcome):
    _keyed(monkeypatch)

    def fake_post(*a, **k):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm.requests, "post", fake_post)
    with pytest.raises(llm.LLMError):
        llm.ZenProvider().complete([{"role": "user", "content": "x"}])


class _Next:
    name = "cline"

    def available(self):
        return True

    def complete(self, messages, **kw):
        return llm.Reply(text="from cline", provider="cline", model="m")


def test_facade_tries_zen_first_then_fails_over(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    _keyed(monkeypatch)
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Resp(503, text="down"))
    facade = llm.LLM()
    facade.cline = _Next()
    facade.gemini = facade.openrouter = facade.novarouter = _Next()
    facade.gemini.name, facade.openrouter.name, facade.novarouter.name = (
        "gemini", "openrouter", "novarouter",
    )

    assert [p.name for p in facade._ordered()][:2] == ["zen", "cline"]
    reply = facade.complete([{"role": "user", "content": "hi"}])
    assert reply.text == "from cline"


def test_unkeyed_zen_is_skipped_in_the_chain(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    names = [p.name for p in llm.LLM()._ordered()]
    assert "zen" not in names


def test_key_is_redacted_in_the_audit_log(monkeypatch, tmp_path):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(config, "AUDIT_LOG", log)
    monkeypatch.setattr(audit, "_fd", None)
    audit.record("probe", opencode_zen_api_key="sekret-zen", novarouter_api_key="sekret-nova")
    os.close(audit._fd)
    monkeypatch.setattr(audit, "_fd", None)
    text = log.read_text()
    assert "sekret-zen" not in text and "sekret-nova" not in text


def test_doctor_report_never_shows_the_key(monkeypatch):
    _keyed(monkeypatch, "sekret-zen-key")
    report = llm.ZenProvider().report()
    assert "present" in report and "sekret" not in report
