"""eval_models.py --scenarios: parsing and first-choice scoring, no live calls."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "eval_models", Path(__file__).with_name("eval_models.py"))
em = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(em)  # import must not run main()

REAL = Path(__file__).parent / "eval" / "agents"


def _reply(name=None, text="", **args):
    calls = [SimpleNamespace(name=name, arguments=args)] if name else []
    return SimpleNamespace(tool_calls=calls, text=text)


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_real_scenario_dir_loads_all_four_agents():
    scs = em.load_scenarios(REAL)
    assert {s["agent"] for s in scs} == {"orchestrator", "system-ops", "research", "reviewer"}
    assert all(s["expect_first_tool"] for s in scs)


def test_load_rejects_malformed(tmp_path):
    bad = _write(tmp_path, "a.json", {"agent": "x", "scenarios": [{"id": "1", "prompt": "p"}]})
    with pytest.raises(ValueError, match="expect_first_tool"):
        em.load_scenarios(bad)
    d = _write(tmp_path, "b.json", {"agent": "x", "scenarios": [
        {"id": "1", "prompt": "p", "expect_first_tool": "delegate"}]})
    with pytest.raises(ValueError, match="expect_delegate_to"):
        em.load_scenarios(d)
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no scenario files"):
        em.load_scenarios(tmp_path / "empty")


def test_score_first_tool_only():
    sc = {"expect_first_tool": "volume"}
    assert em.score_first_choice(sc, _reply("volume"))[0]
    ok, why = em.score_first_choice(sc, _reply("list_windows"))
    assert not ok and "list_windows" in why
    ok, why = em.score_first_choice(sc, _reply(text="sure, done"))
    assert not ok and "no tool call" in why


def test_score_delegate_target():
    sc = {"expect_first_tool": "delegate", "expect_delegate_to": "research"}
    assert em.score_first_choice(sc, _reply("delegate", agent="research"))[0]
    ok, why = em.score_first_choice(sc, _reply("delegate", agent="reviewer"))
    assert not ok and "reviewer" in why


def test_evaluate_tallies_per_agent_and_lists_misses():
    scs = [
        {"agent": "a", "id": "1", "prompt": "p1", "expect_first_tool": "x"},
        {"agent": "a", "id": "2", "prompt": "p2", "expect_first_tool": "y"},
        {"agent": "b", "id": "3", "prompt": "p3", "expect_first_tool": "z"},
    ]
    replies = {"1": _reply("x"), "2": _reply("wrong"), "3": RuntimeError("boom")}

    def call(spec, sc):
        r = replies[sc["id"]]
        if isinstance(r, Exception):
            raise r
        return r

    res = em.evaluate_scenarios("gemini:m", scs, call=call)
    assert res["per_agent"] == {"a": [1, 2], "b": [0, 0]}
    assert [m["id"] for m in res["misses"]] == ["2"]
    # A call that did not run is neither a pass nor a miss: it leaves the denominator.
    assert [e["id"] for e in res["errors"]] == ["3"] and "boom" in res["errors"][0]["why"]


class _Provider:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def complete(self, messages, **kw):
        self.calls.append(kw)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def _patch_agent(monkeypatch, provider):
    class FakeAgent:
        def __init__(self, **kw):
            self.profile = kw["profile"]
            self._llm = SimpleNamespace(gemini=provider)

        def system_prompt(self):
            return "SYS"

        def _tools_for_turn(self, text):
            return [{"name": "volume"}]

    monkeypatch.setattr(em, "Agent", FakeAgent)


def test_first_choice_retries_transient_but_not_real_errors(monkeypatch):
    sc = {"agent": "orchestrator", "prompt": "hi"}
    ok = _reply("volume")
    p = _Provider([RuntimeError("503 UNAVAILABLE"), ok])
    _patch_agent(monkeypatch, p)
    assert em.first_choice("gemini:m", sc, sleep=lambda s: None) is ok
    assert len(p.calls) == 2
    assert p.calls[0]["model"] == "m" and p.calls[0]["system"] == "SYS"
    assert p.calls[0]["tools"] == [{"name": "volume"}]  # the profile's real schema

    p = _Provider([RuntimeError("400 bad request")])
    _patch_agent(monkeypatch, p)
    with pytest.raises(RuntimeError):
        em.first_choice("gemini:m", sc, sleep=lambda s: None)
    assert len(p.calls) == 1


def test_first_choice_needs_provider_prefix(monkeypatch):
    _patch_agent(monkeypatch, _Provider([]))
    with pytest.raises(ValueError, match="provider:model"):
        em.first_choice("gemini-3.5", {"agent": "x", "prompt": "p"})
    with pytest.raises(ValueError, match="unknown provider"):
        em.first_choice("nope:m", {"agent": "x", "prompt": "p"})
