"""Model roles (#228): per-role chains, inheritance, and the unchanged default.

With no ``models.roles`` the facade must try exactly the providers, models and
order it tried before roles existed; every test that sets roles does so through
``config.get`` so nothing touches the owner's settings.json.
"""

from __future__ import annotations

import pytest

from kravein import agent as agent_mod
from kravein import config, llm
from conftest import _agent_with


class Named:
    """A provider stub: records the model it was asked for, fails on demand."""

    def __init__(self, name, *, fail=False, available=True):
        self.name, self.fail, self._available, self.models = name, fail, available, []

    def available(self):
        return self._available

    def model_name(self, override=""):
        return override or f"{self.name}-default"

    def complete(self, messages, *, system="", tools=None, temperature=0.4, model=""):
        self.models.append(model)
        if self.fail:
            raise RuntimeError("down")
        return llm.Reply(text=self.name, provider=self.name, model=model or self.model_name())


@pytest.fixture
def facade(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    f = llm.LLM()
    # All five replaced: cline/gemini/openrouter per the test convention, plus
    # zen/novarouter so no real backend can be reached.
    for name in ("zen", "cline", "gemini", "openrouter", "novarouter"):
        setattr(f, name, Named(name))
    return f


def settings(monkeypatch, **values):
    """Overlay dotted settings on the real (isolated) config.get."""
    real = config.get

    def fake(key, default=None):
        return values[key] if key in values else real(key, default)

    monkeypatch.setattr(config, "get", fake)


def names(plan):
    return [(p.name, m) for p, m in plan]


MSG = [{"role": "user", "content": "x"}]


# -- the default is untouched ------------------------------------------------------------

@pytest.mark.parametrize("role", ["", "orchestrator", "worker:research", "utility",
                                  "judge", "vision", "voice"])
@pytest.mark.parametrize("prefer", ["", "cline:big", "gemini", "ghost:m"])
def test_no_roles_configured_is_exactly_the_old_chain(facade, monkeypatch, role, prefer):
    settings(monkeypatch, **{"llm.provider": "gemini",
                             "llm.provider_order": ["cline", "zen", "novarouter"]})
    old = facade._ordered()
    # The pre-#228 complete(): _ordered(), preferred provider first with its model.
    pname, _, pmodel = prefer.partition(":")
    expected = [(p.name, pmodel.strip() if p.name == pname else "") for p in old if p.name == pname]
    expected += [(p.name, "") for p in old if p.name != pname]
    assert names(facade._plan(role, prefer)) == expected
    assert [p for p, _ in expected][0] == ("cline" if pname == "cline" else "gemini")


def test_no_roles_complete_calls_providers_in_the_old_order(facade, monkeypatch):
    settings(monkeypatch, **{"llm.provider": "zen", "llm.provider_order": ["cline", "gemini"]})
    facade.zen.fail = facade.cline.fail = True
    reply = facade.complete(MSG, role="orchestrator")
    assert reply.text == "gemini"
    assert facade.zen.models == [""] and facade.cline.models == [""]
    assert facade.openrouter.models == []          # provider_order stays exclusive


# -- explicit roles and inheritance ------------------------------------------------------

def test_role_chain_is_used_in_order_with_its_models(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"utility": ["gemini:flash-lite", "zen:ds"]}})
    assert names(facade._plan("utility", "")) == [("gemini", "flash-lite"), ("zen", "ds")]
    assert facade.complete(MSG, role="utility").model == "flash-lite"
    assert facade.gemini.models == ["flash-lite"]


def test_role_chain_is_exclusive_and_fails_over_inside_it(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"judge": ["gemini:a", "gemini:b", "zen"]}})
    facade.gemini.fail = True
    reply = facade.complete(MSG, role="judge")
    assert reply.text == "zen" and reply.model == "zen-default"
    assert facade.gemini.models == ["a", "b"]
    assert facade.cline.models == [] and facade.openrouter.models == []


def test_worker_inherits_orchestrator_then_global(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"orchestrator": ["cline:k"]},
                             "llm.provider": "zen", "llm.provider_order": []})
    assert facade.resolve_role("worker:research") == ("orchestrator", [("cline", "k")])
    assert names(facade._plan("worker:research", "")) == [("cline", "k")]
    assert names(facade._plan("utility", "")) == [("cline", "k")]
    settings(monkeypatch, **{"models.roles": {}, "llm.provider": "zen", "llm.provider_order": []})
    assert facade.resolve_role("worker:research") == ("", None)
    assert facade._plan("worker:research", "")[0][0].name == "zen"


def test_explicit_worker_role_beats_the_profile_model(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"worker:research": ["gemini:w"],
                                              "orchestrator": ["zen:o"]}})
    assert names(facade._plan("worker:research", "cline:p")) == [("gemini", "w")]
    # Unset worker role: the profile model leads, then the inherited chain.
    assert names(facade._plan("worker:ops", "cline:p")) == [("cline", "p"), ("zen", "o")]


def test_unavailable_and_cooling_entries_are_skipped(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"utility": ["gemini:a", "zen:b", "cline:c"]}})
    facade.gemini._available = False
    facade._open_breaker("zen", 600, reason="402")
    assert names(facade._plan("utility", "")) == [("cline", "c")]
    # Everything cooling: trying beats giving up (same rule as the global chain).
    facade._open_breaker("cline", 600, reason="402")
    assert names(facade._plan("utility", "")) == [("zen", "b"), ("cline", "c")]


def test_provider_blocked_opens_the_breaker_inside_a_role_chain(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"utility": ["zen:x", "gemini:y"]}})

    def blocked(*a, **k):
        raise llm.ProviderBlocked("Zen returned 402", 1800)

    monkeypatch.setattr(facade.zen, "complete", blocked)
    assert facade.complete(MSG, role="utility").text == "gemini"
    assert facade._is_cooling("zen")
    assert names(facade._plan("utility", "")) == [("gemini", "y")]


def test_bad_role_entries_and_names(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"utility": ["ghost:m", "openrouter:meta/x:free"],
                                              "judge": "zen", "vision": []}})
    # Split on the first colon only; unknown providers dropped.
    assert facade.role_setting("utility") == [("openrouter", "meta/x:free")]
    assert facade.role_setting("judge") is None        # not a list: ignored
    assert facade.role_setting("vision") is None
    with pytest.raises(ValueError):
        facade._plan("boss", "")
    with pytest.raises(ValueError):
        facade._plan("worker:", "")
    assert llm.valid_role("voice") and llm.valid_role("worker:x")


def test_a_role_chain_with_nothing_usable_says_so(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"utility": ["gemini:a"]}})
    facade.gemini._available = False
    with pytest.raises(llm.LLMError, match="'utility' model chain"):
        facade.complete(MSG, role="utility")


def test_thought_signature_survives_a_role_chain(facade, monkeypatch):
    settings(monkeypatch, **{"models.roles": {"orchestrator": ["gemini:g"]}})
    sig = b"opaque-signature"

    def with_sig(messages, **kw):
        return llm.Reply(tool_calls=[llm.ToolCall("x", {}, "x", signature=sig)],
                         provider="gemini", model=kw.get("model", ""))

    monkeypatch.setattr(facade.gemini, "complete", with_sig)
    assert facade.complete(MSG, role="orchestrator").tool_calls[0].signature is sig


# -- callers pass their role --------------------------------------------------------------

def _capture(agent, monkeypatch):
    seen = []
    monkeypatch.setattr(agent._llm, "complete",
                        lambda messages, **kw: seen.append(kw) or llm.Reply(text="ok"))
    return seen


def test_agent_roles_orchestrator_worker_and_utility(monkeypatch):
    plain, _ = _agent_with([])
    seen = _capture(plain, monkeypatch)
    plain.ask("hi")
    assert seen[-1]["role"] == "orchestrator" and "prefer" not in seen[-1]
    assert seen[-1]["tags"]["agent"] == "default"

    prof = agent_mod.AgentProfile(name="research", purpose="p", tools=("memory",),
                                  model="zen:m")
    worker, _ = _agent_with([], profile=prof, thread_id="t1")
    worker.principal = "owner"
    seen = _capture(worker, monkeypatch)
    worker.ask("hi")
    assert seen[-1]["role"] == "worker:research" and seen[-1]["prefer"] == "zen:m"
    assert seen[-1]["tags"] == {"principal": "owner", "thread": "t1", "agent": "research"}

    worker._summarize([{"role": "user", "content": "old"}])
    assert seen[-1]["role"] == "utility" and "prefer" not in seen[-1]

    worker._wrap_up(agent_mod.Turn(), "sys", "run out of rounds")
    assert seen[-1]["role"] == "utility" and seen[-1]["prefer"] == "zen:m"


def test_orchestrator_profile_uses_the_orchestrator_role():
    prof = agent_mod.AgentProfile(name="orchestrator", purpose="p", tools=("memory",))
    agent, _ = _agent_with([], profile=prof)
    assert agent.model_role == "orchestrator"
