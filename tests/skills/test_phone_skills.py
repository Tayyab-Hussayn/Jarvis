"""Tests: phone_* skills on the phone channel.

``phone.connected``, ``phone.call`` and ``phone.list_devices`` are mocked; no
socket, server or real phone is involved. The channel's own validation
(``validate_command``) is real, because the skills rely on it to refuse a bad
argument *before* asking the user.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import audit, phone, registry, safety  # noqa: E402
from kravein.safety import Danger  # noqa: E402
from kravein.skills import phone as ps  # noqa: E402

ALL = sorted(phone.command_names())
PIXEL = {"device_id": "dev-pixel", "name": "Pixel", "capabilities": ALL}
TAB = {"device_id": "dev-tab", "name": "Tab", "capabilities": ALL}

OK_DATA = {
    "get_battery": {"percent": 81, "charging": True},
    "volume_get": {"percent": 40},
    "get_device_info": {"model": "Pixel 9", "os_version": "16", "app_version": "1.2"},
    "flashlight_on": {"flashlight": "on"},
    "flashlight_off": {"flashlight": "off"},
}


class FakeChannel:
    """Records calls; answers from ``OK_DATA``, echoes args, or raises ``fail``."""

    def __init__(self, links, fail=None):
        self.links = links
        self.fail = fail or {}
        self.calls = []

    def connected(self):
        return [dict(l) for l in self.links]

    def call(self, command, args=None, *, device=None, timeout=None):
        self.calls.append((command, dict(args or {}), device))
        if command in self.fail:
            raise self.fail[command]
        if command in OK_DATA:
            return dict(OK_DATA[command])
        if command == "launch_app":
            return {"package": "com.whatsapp"}
        return dict(args or {})


@pytest.fixture
def chan(monkeypatch):
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)

    def install(links, fail=None, paired=None):
        fake = FakeChannel(links, fail)
        monkeypatch.setattr(phone, "connected", fake.connected)
        monkeypatch.setattr(phone, "call", fake.call)
        monkeypatch.setattr(phone, "list_devices", lambda: paired or [])
        return fake

    return install


@pytest.fixture
def asked():
    """Approve everything, and record what each confirmation said."""
    prompts = []

    def confirm(action, detail, danger):
        prompts.append((action, danger))
        return True

    with safety.confirmer_scope(confirm):
        yield prompts


@pytest.fixture
def enforced(monkeypatch):
    """Capture every enforce() call while keeping its real behaviour."""
    seen = []
    real = safety.enforce

    def spy(verdict, action_desc, **kw):
        seen.append({"danger": verdict.danger, "action": action_desc, **kw})
        return real(verdict, action_desc, **kw)

    monkeypatch.setattr(ps, "enforce", spy)
    return seen


# -- registration and the model-facing schema ------------------------------

@pytest.mark.parametrize("name,danger", [
    ("list_phones", Danger.SAFE),
    ("phone_status", Danger.SAFE),
    ("phone_flashlight", Danger.MODERATE),
    ("phone_set_volume", Danger.MODERATE),
    ("phone_open_url", Danger.DESTRUCTIVE),
    ("phone_launch_app", Danger.DESTRUCTIVE),
])
def test_registered_in_phone_category_with_level(name, danger):
    entry = registry.get_skill(name)
    assert entry is not None
    assert entry.category == "phone"
    assert entry.danger == danger
    assert "phone" in entry.description.lower()


def test_schema_hides_injected_params_and_documents_every_arg():
    for name in ("phone_status", "phone_flashlight", "phone_set_volume",
                 "phone_open_url", "phone_launch_app"):
        params = registry.get_skill(name).parameters["properties"]
        assert not any(p.startswith("_") for p in params), name
        for pname, spec in params.items():
            assert spec.get("description"), f"{name}.{pname} has no description"


def test_desktop_counterparts_point_at_the_phone_tools():
    """The names must not be confusable: each side names the other."""
    assert "phone_set_volume" in registry.get_skill("set_volume").description
    assert "phone_open_url" in registry.get_skill("open_url").description
    assert "set_volume" in registry.get_skill("phone_set_volume").description
    assert "open_url" in registry.get_skill("phone_open_url").description


def test_wrapped_args_line_reaches_the_schema_whole():
    url = registry.get_skill("phone_open_url").parameters["properties"]["url"]["description"]
    assert url.endswith("gets https:// added.")


# -- read-only --------------------------------------------------------------

def test_status_happy_path(chan):
    fake = chan([PIXEL])
    out = ps.phone_status()
    assert "battery 81% (charging)" in out
    assert "media volume 40%" in out
    assert "Pixel 9" in out
    assert {c[2] for c in fake.calls} == {"dev-pixel"}


def test_status_does_not_enforce_or_confirm(chan, enforced):
    chan([PIXEL])
    with safety.confirmer_scope(lambda *a: pytest.fail("a read asked for confirmation")):
        ps.phone_status()
    assert enforced == []


def test_status_skips_missing_capability_without_calling(chan):
    fake = chan([{**PIXEL, "capabilities": ["get_battery"]}])
    out = ps.phone_status()
    assert "battery 81%" in out
    assert "not supported" in out
    assert [c[0] for c in fake.calls] == ["get_battery"]


def test_status_stops_after_a_timeout(chan):
    fake = chan([PIXEL], fail={"get_battery": phone.PhoneTimeout("no answer in 15s")})
    out = ps.phone_status()
    assert [c[0] for c in fake.calls] == ["get_battery"], "kept waiting on a silent phone"
    assert "cannot confirm" in out.lower() or "did not answer" in out


def test_list_phones(chan):
    chan([], paired=[
        {"device_id": "dev-pixel", "name": "Pixel", "connected": True},
        {"device_id": "dev-tab", "name": "Tab", "connected": False},
    ])
    out = ps.list_phones()
    assert "Pixel (id dev-pixel): connected" in out
    assert "Tab (id dev-tab): offline" in out


# -- state-changing: happy paths and enforce targets -----------------------

def test_flashlight_on_moderate_enforced_with_target_no_prompt(chan, enforced, asked):
    fake = chan([PIXEL])
    out = ps.phone_flashlight(on=True, device="Pixel")
    assert out == "The flashlight on Pixel (dev-pixel) is now on."
    assert fake.calls == [("flashlight_on", {}, "dev-pixel")]
    (e,) = enforced
    assert e["danger"] == Danger.MODERATE
    assert e["targets"] == ["phone:dev-pixel", "phone:dev-pixel:flashlight_on"]
    assert asked == [], "MODERATE must not prompt"


def test_flashlight_reports_the_state_the_phone_returned(chan, asked, monkeypatch):
    chan([PIXEL])
    monkeypatch.setitem(OK_DATA, "flashlight_off", {"flashlight": "on"})
    out = ps.phone_flashlight(on=False)
    assert "reports it is on" in out


def test_set_volume_reports_what_the_phone_reports(chan, enforced, asked, monkeypatch):
    fake = chan([PIXEL])
    assert ps.phone_set_volume(percent=55) == "Media volume on Pixel (dev-pixel) is now 55%."
    assert fake.calls[-1] == ("volume_set", {"percent": 55}, "dev-pixel")
    assert enforced[-1]["danger"] == Danger.MODERATE
    # Android snaps to its own steps; report the real value, not the request.
    monkeypatch.setattr(fake, "call", lambda c, a=None, **k: {"percent": 53})
    monkeypatch.setattr(phone, "call", fake.call)
    assert "is now 53%" in ps.phone_set_volume(percent=55)


def test_set_volume_out_of_range_refused_before_enforce(chan, enforced):
    fake = chan([PIXEL])
    out = ps.phone_set_volume(percent=150)
    assert "Nothing was sent" in out
    assert fake.calls == [] and enforced == []


def test_open_url_confirms_naming_phone_and_url(chan, enforced, asked):
    fake = chan([PIXEL])
    out = ps.phone_open_url(url="example.com/a")
    assert out == "Pixel (dev-pixel) opened https://example.com/a."
    assert fake.calls == [("open_url", {"url": "https://example.com/a"}, "dev-pixel")]
    (action, danger), = asked
    assert danger == Danger.DESTRUCTIVE
    assert "Pixel (dev-pixel)" in action and "https://example.com/a" in action
    assert enforced[0]["targets"] == [
        "phone:dev-pixel", "phone:dev-pixel:open_url", "phone:implicit",
    ]


def test_open_url_bad_scheme_refused_before_asking(chan, asked):
    fake = chan([PIXEL])
    out = ps.phone_open_url(url="intent://evil#Intent;end")
    assert "Nothing was sent" in out
    assert asked == [] and fake.calls == []


def test_launch_app_confirms_and_reports_package(chan, asked):
    fake = chan([PIXEL])
    out = ps.phone_launch_app(app="WhatsApp", device="dev-pixel")
    assert out == "Pixel (dev-pixel) opened com.whatsapp."
    assert fake.calls == [("launch_app", {"app": "WhatsApp"}, "dev-pixel")]
    assert "WhatsApp" in asked[0][0] and "Pixel (dev-pixel)" in asked[0][0]


def test_missing_capability_refused_before_asking(chan, asked):
    fake = chan([{**PIXEL, "capabilities": ["get_battery"]}])
    out = ps.phone_launch_app(app="Maps")
    assert "does not support launch_app" in out and "Nothing was sent" in out
    assert asked == [] and fake.calls == []


# -- denial -----------------------------------------------------------------

def test_denied_confirmation_sends_nothing_and_blocks_retry(chan):
    fake = chan([PIXEL])
    prompts = []
    with safety.confirmer_scope(lambda a, d, g: (prompts.append(a), False)[1]):
        with pytest.raises(safety.ConfirmationRequired):
            ps.phone_open_url(url="https://example.com")
        # Same phone, different verb: refused outright, no second prompt.
        with pytest.raises(safety.SafetyError):
            ps.phone_launch_app(app="Chrome")
    assert len(prompts) == 1
    assert fake.calls == []


def test_unattended_session_cannot_open_url(chan):
    fake = chan([PIXEL])
    with safety.confirmer_scope(None):
        out = registry.dispatch("phone_open_url", {"url": "https://example.com"})
    assert "needs confirmation" in out or "refused" in out.lower()
    assert fake.calls == []


# -- ambiguous / unknown device -------------------------------------------

def test_two_phones_no_name_refuses_and_lists_both(chan, asked):
    fake = chan([PIXEL, TAB])
    out = ps.phone_flashlight(on=True)
    assert "Pixel (dev-pixel)" in out and "Tab (dev-tab)" in out
    assert "Nothing was sent" in out
    assert fake.calls == [] and asked == []


def test_two_phones_same_name_refuses_with_ids(chan):
    fake = chan([PIXEL, {**TAB, "name": "pixel"}])
    out = ps.phone_status(device="Pixel")
    assert "dev-pixel" in out and "dev-tab" in out
    assert fake.calls == []


def test_named_phone_picks_it_among_several(chan, asked):
    fake = chan([PIXEL, TAB])
    ps.phone_flashlight(on=True, device="tab")
    assert fake.calls == [("flashlight_on", {}, "dev-tab")]


def test_unknown_name_lists_connected(chan):
    chan([PIXEL])
    out = ps.phone_status(device="Nokia")
    assert "Nokia" in out and "Pixel (dev-pixel)" in out


def test_not_connected_names_offline_paired_phone(chan):
    chan([], paired=[{"device_id": "dev-pixel", "name": "Pixel", "connected": False}])
    out = ps.phone_flashlight(on=True)
    assert "no phone is connected" in out and "Paired but offline: Pixel" in out
    assert "Nothing was sent" in out


# -- every PhoneError becomes an honest result ------------------------------

@pytest.mark.parametrize("exc,must,must_not", [
    (phone.PhoneTimeout("did not answer within 15s"), "cannot confirm", "is now"),
    (phone.PhoneDisconnected("phone 'Pixel' disconnected"), "unknown", "is now"),
    (phone.PhoneBadResult("malformed"), "cannot confirm", "is now"),
    (phone.PhoneCommandFailed("camera busy", code="unavailable"), "could not", "is now"),
    (phone.PhoneCommandFailed("no permission", code="permission_denied"), "permission", "is now"),
    (phone.PhoneCommandRejected("phone 'Pixel' does not support flashlight_on",
                                code="unknown_capability"), "Nothing was sent", "is now"),
    (phone.PhoneNotConnected("no phone is connected"), "Nothing was sent", "is now"),
    (phone.PhoneAmbiguous("several phones are connected"), "Nothing was sent", "is now"),
    (phone.PhoneError("phone.call() must not run on the server's event loop"),
     "phone error", "is now"),
])
def test_phone_errors_never_claim_success(chan, asked, exc, must, must_not):
    chan([PIXEL], fail={"flashlight_on": exc})
    out = ps.phone_flashlight(on=True)
    assert must.lower() in out.lower(), out
    assert must_not not in out, out


def test_error_on_confirmed_action_after_approval_is_honest(chan, asked):
    chan([PIXEL], fail={"open_url": phone.PhoneTimeout("did not answer open_url within 15s")})
    out = ps.phone_open_url(url="https://example.com")
    assert len(asked) == 1
    assert "may or may not have happened" in out
    assert "opened" not in out
