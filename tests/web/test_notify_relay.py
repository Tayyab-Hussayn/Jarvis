"""Reminders and job notifications forwarded to paired phones (#62).

The tick process appends to an outbox file; the server's `OutboxRelay` turns
new lines into `notify` frames. The invariants: stale entries are never
delivered, the file stays bounded, and a failed write never fails the job.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from kravein import config, jobs, phone


@pytest.fixture(autouse=True)
def outbox(tmp_path, monkeypatch):
    path = tmp_path / "phone_outbox.jsonl"
    monkeypatch.setattr(phone, "OUTBOX_FILE", path)
    monkeypatch.setattr(phone, "_links", {})
    monkeypatch.setattr(config, "get", lambda key, default=None: default)
    return path


class _Link:
    ready = True
    closed = False

    def __init__(self):
        self.sent = []

    def _send(self, payload, request_id=""):
        self.sent.append(payload)


@pytest.fixture
def link(monkeypatch):
    lk = _Link()
    monkeypatch.setattr(phone, "_ready_links", lambda: [lk])
    return lk


def test_append_writes_private_entry(outbox):
    assert phone.outbox_append("Tea", "tea is ready", "reminder")
    (entry,) = [json.loads(l) for l in outbox.read_text().splitlines()]
    assert entry["title"] == "Tea" and entry["kind"] == "reminder" and entry["id"]
    assert os.stat(outbox).st_mode & 0o777 == 0o600


def test_relay_sends_notify_frame(outbox, link):
    relay = phone.OutboxRelay()
    phone.outbox_append("Tea", "ready", "reminder")
    assert relay.poll() == 1
    (frame,) = link.sent
    assert frame["type"] == "notify"
    assert frame["payload"]["title"] == "Tea" and frame["payload"]["kind"] == "reminder"
    assert isinstance(frame["payload"]["at"], int)
    phone.parse_frame(json.dumps(frame), inbound=False)  # valid per the protocol
    assert relay.poll() == 0  # nothing new, nothing resent


def test_each_entry_gets_a_unique_request_id(outbox, link):
    relay = phone.OutboxRelay()
    phone.outbox_append("a", "1", "job")
    phone.outbox_append("b", "2", "job")
    assert relay.poll() == 2
    assert len({f["request_id"] for f in link.sent}) == 2


def test_truncates_title_and_body(outbox, link):
    relay = phone.OutboxRelay()
    phone.outbox_append("t" * 500, "b" * 5000, "job")
    relay.poll()
    p = link.sent[0]["payload"]
    assert len(p["title"]) == 120 and len(p["body"]) == 1000


def test_stale_entries_are_dropped(outbox, link):
    relay = phone.OutboxRelay()
    phone.outbox_append("old", "x", "reminder", now=time.time() - phone.OUTBOX_MAX_AGE - 5)
    assert relay.poll() == 0 and link.sent == []


def test_entries_from_before_server_start_are_not_replayed(outbox, link):
    phone.outbox_append("before", "x", "reminder")
    relay = phone.OutboxRelay()
    assert relay.poll() == 0 and link.sent == []


def test_trim_bounds_the_file_and_drops_stale(outbox):
    old = time.time() - phone.OUTBOX_MAX_AGE - 60
    for i in range(phone.OUTBOX_MAX_ENTRIES):
        phone.outbox_append("old", str(i), "job", now=old)
    phone.outbox_append("fresh", "x", "job")
    lines = outbox.read_text().splitlines()
    assert [json.loads(l)["title"] for l in lines] == ["fresh"]


def test_trim_survives_for_a_running_relay(outbox, link):
    relay = phone.OutboxRelay()
    old = time.time() - phone.OUTBOX_MAX_AGE - 60
    for i in range(phone.OUTBOX_MAX_ENTRIES):
        phone.outbox_append("old", str(i), "job", now=old)
    relay.poll()
    phone.outbox_append("fresh", "x", "job")  # triggers the rewrite
    assert relay.poll() == 1
    assert [f["payload"]["title"] for f in link.sent] == ["fresh"]


def test_write_failure_does_not_raise(outbox, monkeypatch):
    monkeypatch.setattr(phone, "OUTBOX_FILE", outbox / "not-a-dir" / "x.jsonl")
    outbox.write_text("")  # a file where a directory is needed
    assert phone.outbox_append("t", "b", "job") is False


def test_setting_off_sends_nothing(outbox, link, monkeypatch):
    relay = phone.OutboxRelay()
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: False if key == "phone.forward_notifications" else default,
    )
    assert phone.outbox_append("t", "b", "job") is False
    assert not outbox.exists()
    # Turned off after the tick queued it: the relay must not send either.
    monkeypatch.setattr(config, "get", lambda key, default=None: default)
    phone.outbox_append("t", "b", "job")
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: False if key == "phone.forward_notifications" else default,
    )
    assert relay.poll() == 0 and link.sent == []


def test_job_delivery_queues_for_phones_and_survives_failure(outbox, monkeypatch):
    from kravein import delivery

    monkeypatch.setattr(delivery, "deliver", lambda *a, **k: {})
    jobs.deliver({"id": "j1", "title": "Reminder", "kind": "notify"}, "stretch")
    jobs.deliver({"id": "j2", "title": "Report", "kind": "agent"}, "done")
    kinds = [json.loads(l)["kind"] for l in outbox.read_text().splitlines()]
    assert kinds == ["reminder", "job"]

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(phone.os, "open", boom)
    jobs.deliver({"id": "j3", "title": "x", "kind": "notify"}, "still fine")  # no raise
