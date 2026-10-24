"""#72: a confirmation resolved anywhere closes every card, and a paired phone
lives 14 days then is removed cleanly."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from kravein import config, events, phone, webauth
from kravein.safety import Danger


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(phone, "PHONES_FILE", tmp_path / "phones.json")
    monkeypatch.setattr(phone, "_offer", None)
    monkeypatch.setattr(phone, "_links", {})
    monkeypatch.setattr(events, "_open_confirms", {})
    records: list[tuple[str, dict]] = []
    monkeypatch.setattr(phone.audit, "record", lambda e, **f: records.append((e, f)))
    monkeypatch.setattr(events.audit, "record", lambda e, **f: records.append((e, f)))
    webauth.clear_lan_hosts()
    return records


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    t = threading.Thread(target=lp.run_forever, daemon=True)
    t.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    t.join(2)


def _frames(session, loop, until, timeout=3.0):
    out = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            f = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(session.next_frame(), 0.5), loop
            ).result(1)
        except Exception:
            continue
        out.append(f)
        if f["t"] == until:
            break
    return out


def _confirm_in_thread(session, result):
    def go():
        result.append(session.confirm("close workspace", "ws 3", Danger.DESTRUCTIVE))

    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


def _wait_open(timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if events.open_confirms():
            return events.open_confirms()[0][0]
        time.sleep(0.01)
    raise AssertionError("confirmation never opened")


@pytest.mark.parametrize("approve,outcome", [(True, "approved"), (False, "denied")])
def test_phone_answer_closes_the_web_card(loop, approve, outcome):
    session = events.Session(loop, confirm_timeout=5)
    result: list[bool] = []
    th = _confirm_in_thread(session, result)
    cid = _wait_open()
    assert events.resolve_open(cid, approve)
    th.join(2)
    frames = _frames(session, loop, "confirm_resolved")
    resolved = [f for f in frames if f["t"] == "confirm_resolved"]
    assert resolved and resolved[0]["confirm_id"] == cid
    assert resolved[0]["outcome"] == outcome and resolved[0]["by"] == "phone"
    assert result == [approve]


def test_timeout_resolves_as_expired(loop):
    session = events.Session(loop, confirm_timeout=0.1)
    result: list[bool] = []
    _confirm_in_thread(session, result).join(2)
    kinds = {f["t"]: f for f in _frames(session, loop, "confirm_expired")}
    assert kinds["confirm_resolved"]["outcome"] == "expired"
    assert "confirm_expired" in kinds and result == [False]


def test_cancel_resolves_as_denied(loop):
    session = events.Session(loop, confirm_timeout=5)
    result: list[bool] = []
    th = _confirm_in_thread(session, result)
    _wait_open()
    assert session.deny_pending() == 1
    th.join(2)
    resolved = [f for f in _frames(session, loop, "confirm_resolved") if f["t"] == "confirm_resolved"]
    assert resolved[0]["outcome"] == "denied" and result == [False]


def test_answering_a_closed_card_does_nothing(loop):
    session = events.Session(loop, confirm_timeout=5)
    result: list[bool] = []
    th = _confirm_in_thread(session, result)
    cid = _wait_open()
    assert events.resolve_open(cid, False)
    th.join(2)
    assert not events.resolve_open(cid, True)
    assert not session.resolve_confirm(cid, True)
    assert result == [False]


# -- 14-day pairing ---------------------------------------------------------

def _redeem() -> dict:
    offer = phone.create_offer("127.0.0.1", 8787)
    return phone.redeem_offer(offer["code"], "Pixel")


def _header(cred) -> str:
    return f"Device {cred['device_id']}:{cred['device_secret']}"


def test_default_lifetime_is_14_days():
    cred = _redeem()
    [dev] = phone.list_devices()
    assert abs(dev["expires_at"] - (time.time() + 14 * 86400)) < 120
    assert phone.authenticate(_header(cred)) is not None


def test_credential_survives_a_restart_within_ttl(monkeypatch):
    cred = _redeem()
    monkeypatch.setattr(phone, "_wall", lambda: time.time() + 13 * 86400)
    assert phone.authenticate(_header(cred)) is not None
    assert phone.sweep_expired() == []


def test_expired_credential_is_removed_and_audited(isolated, monkeypatch):
    cred = _redeem()
    revoked = []
    monkeypatch.setattr(webauth, "revoke_ui_tokens", lambda d: revoked.append(d))
    monkeypatch.setattr(phone, "_wall", lambda: time.time() + 15 * 86400)
    assert phone.authenticate(_header(cred)) is None
    assert phone.list_devices() == []
    assert revoked == [cred["device_id"]]
    assert ("phone.expired", {"device_id": cred["device_id"]}) in isolated
    monkeypatch.setattr(phone, "_wall", time.time)
    assert phone.authenticate(_header(cred)) is None


def test_sweep_closes_a_live_socket(monkeypatch):
    cred = _redeem()
    closed = []

    class Link:
        device_id = cred["device_id"]

        def close(self, reason):
            closed.append(reason)

    monkeypatch.setattr(phone, "_links", {cred["device_id"]: Link()})
    monkeypatch.setattr(phone, "_wall", lambda: time.time() + 15 * 86400)
    assert phone.sweep_expired() == [cred["device_id"]]
    assert closed == ["expired"] and phone.list_devices() == []


def test_ttl_is_configurable(monkeypatch):
    cred = _redeem()
    real = config.get
    monkeypatch.setattr(config, "get", lambda k, d=None: 1 if k == "phone.device_ttl_days" else real(k, d))
    monkeypatch.setattr(phone, "_wall", lambda: time.time() + 2 * 86400)
    assert phone.authenticate(_header(cred)) is None


def test_unreadable_paired_at_fails_closed():
    cred = _redeem()
    devices = phone._load_devices()
    devices[cred["device_id"]]["paired_at"] = "garbage"
    phone._save_devices(devices)
    assert phone.authenticate(_header(cred)) is None
