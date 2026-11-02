"""Live model order and failover (#195 follow-up): 3.1 first, 2.5 behind it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import audit, config, live  # noqa: E402
from kravein.live import LiveSession, NetworkAudio  # noqa: E402

M31 = "models/gemini-3.1-flash-live-preview"
M25 = "models/gemini-2.5-flash-native-audio-preview-12-2025"
M25L = "models/gemini-2.5-flash-native-audio-latest"


@pytest.fixture
def tried(monkeypatch):
    """Record each model `run()` connects to; `fail[model]` makes that one raise."""
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(config, "STATE_DIR", Path("/nonexistent-kravein-test"))
    state = type("S", (), {"models": [], "fail": {}})()

    async def main(self):
        state.models.append(self.model)
        err = state.fail.get(self.model)
        if err:
            raise RuntimeError(err)

    monkeypatch.setattr(LiveSession, "_main", main)
    return state


def _session(**kw):
    net = NetworkAudio()
    return LiveSession(source=net, sink=net, **kw)


def _configured(monkeypatch, value):
    real = config.get
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: value if key == "voice.live_model" else real(key, default))


def test_default_order_is_3_1_then_the_2_5_models():
    assert live.LIVE_MODEL == M31
    assert live.LIVE_FALLBACKS == [M31, M25, M25L]


def test_default_session_uses_3_1_and_stops_there(tried, monkeypatch):
    _configured(monkeypatch, "")
    _session().run()
    assert tried.models == [M31]


def test_3_1_failing_falls_back_to_2_5(tried, monkeypatch):
    _configured(monkeypatch, "")
    tried.fail[M31] = "received 1007 (invalid frame payload data)"
    _session().run()
    assert tried.models == [M31, M25]


@pytest.mark.parametrize("error", ["429 RESOURCE_EXHAUSTED", "503 UNAVAILABLE",
                                   "404 NOT_FOUND", "timed out during opening handshake"])
def test_unavailable_3_1_falls_through_the_whole_chain(tried, monkeypatch, error):
    _configured(monkeypatch, "")
    tried.fail[M31] = tried.fail[M25] = error
    _session().run()
    assert tried.models == [M31, M25, M25L]


def test_configured_model_is_tried_first_then_the_rest(tried, monkeypatch):
    _configured(monkeypatch, M25)
    tried.fail[M25] = tried.fail[M31] = "503 UNAVAILABLE"
    _session().run()
    assert tried.models == [M25, M31, M25L]


def test_configured_unknown_model_keeps_the_fallbacks(tried, monkeypatch):
    _configured(monkeypatch, "models/some-future-live")
    tried.fail["models/some-future-live"] = "404 NOT_FOUND"
    _session().run()
    assert tried.models == ["models/some-future-live", M31]


def test_an_unrelated_error_does_not_swap_models(tried, monkeypatch):
    _configured(monkeypatch, "")
    events = []
    tried.fail[M31] = "API key not valid"
    _session(observer=lambda k, t: events.append(k)).run()
    assert tried.models == [M31] and "error" in events


def test_a_1007_after_a_turn_is_not_retried_on_another_model(tried, monkeypatch):
    # Mid-conversation the voice must not change under the owner.
    _configured(monkeypatch, "")
    tried.fail[M31] = "received 1007"
    s = _session()
    s.turns = 2
    s.run()
    assert tried.models == [M31]


def test_the_english_pin_is_kept():
    # Measured in #193: en-US and en-IN tie on 3.1; the pin stays en-US.
    assert live.DEFAULT_LANGUAGE == "en-US"
