"""Live voice through the client's mic and speaker (#90): binary audio on /ws,
fed to a LiveSession through NetworkAudio, with a fake Live backend.

The fake runs the real `NetworkAudio.capture`/`play` on an event loop, exactly
as `LiveSession` does, so these cover the server's frame handling and teardown
without a Gemini connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time

import pytest

from kravein import live, voice, webauth
from kravein.server import MAX_AUDIO_FRAME, _AudioOut, _is_loopback_peer

TOKEN = "test-token-device-voice"
WS_HEADERS = {"Host": "127.0.0.1:8787"}
BARGE = b"\xff\xff"


class FakeLive:
    """Echoes every microphone chunk back as model audio; BARGE interrupts."""

    instances: list["FakeLive"] = []

    def __init__(self, observer=None, confirmer=None, model=None, source=None, sink=None):
        self.source, self.sink = source, sink
        self.heard: list[bytes] = []
        self.stopped = threading.Event()
        self.finished = threading.Event()
        FakeLive.instances.append(self)

    def stop(self):
        self.stopped.set()
        close = getattr(self.source, "close", None)
        if close:
            close()

    def run(self):
        async def main():
            audio_q: asyncio.Queue = asyncio.Queue()
            play = asyncio.create_task(self.sink.play(audio_q, self.stopped))

            def put(data: bytes) -> None:
                self.heard.append(data)
                if data == BARGE:
                    self.sink.interrupt()
                else:
                    audio_q.put_nowait(data)

            try:
                await self.source.capture(put, None, lambda: None)
            finally:
                self.sink.close()
                # Like LiveSession: the player parks on an empty queue, so it
                # is cancelled, not awaited.
                play.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await play

        try:
            asyncio.run(main())
        finally:
            self.finished.set()
        return 0


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    webauth.clear_lan_hosts()
    FakeLive.instances = []
    monkeypatch.setattr(voice, "live_available", lambda: (True, ""))
    monkeypatch.setattr(voice, "readiness", lambda: (False, ["no stt"]))
    monkeypatch.setattr(live, "LiveSession", FakeLive)
    return TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")


def _connect(client):
    ws = client.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS)
    return ws


def _next(ws):
    message = ws.receive()
    if message.get("bytes") is not None:
        return ("bytes", message["bytes"])
    return ("json", json.loads(message["text"]))


def _until(ws, want, limit=40):
    """Read frames until `want(kind, value)`; returns everything read."""
    seen = []
    for _ in range(limit):
        item = _next(ws)
        seen.append(item)
        if want(*item):
            return seen
    raise AssertionError(f"never saw the frame; got {seen[-5:]}")


def _phase(name):
    return lambda kind, v: kind == "json" and v.get("t") == "voice_state" and v["phase"] == name


def _start_device(ws):
    ws.send_text(json.dumps({"t": "voice_start", "mode": "auto", "audio": "device"}))
    return _until(ws, _phase("started"))[-1][1]


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _no_voice_threads():
    return not [t for t in threading.enumerate() if t.name.startswith("kravein-web-voice")]


def test_hello_says_whether_the_page_is_on_the_laptop(client):
    with _connect(client) as ws:
        hello = _next(ws)[1]
    # The test client's peer is not an IP literal; the real checks are below.
    assert hello["local_client"] is False
    assert _is_loopback_peer("127.0.0.1") and _is_loopback_peer("::1")
    assert not _is_loopback_peer("192.168.1.20")
    assert not _is_loopback_peer("")


def test_device_session_round_trip_and_barge_in(client):
    with _connect(client) as ws:
        _next(ws)  # hello
        started = _start_device(ws)
        assert started["text"] == "live" and started["audio"] == "device"

        ws.send_bytes(b"\x01\x00\x02\x00")
        # What the mic sent reaches the session; what the model says comes back
        # as a binary frame.
        kind, audio = _until(ws, lambda k, v: k == "bytes")[-1]
        assert audio == b"\x01\x00\x02\x00"
        assert FakeLive.instances[0].heard == [b"\x01\x00\x02\x00"]

        # Barge-in arrives as a text marker, in order with the audio.
        ws.send_bytes(BARGE)
        _until(ws, lambda k, v: k == "json" and v.get("t") == "audio_clear")

        ws.send_text(json.dumps({"t": "voice_stop"}))
        _until(ws, _phase("stopped"))
    assert _wait(_no_voice_threads)


def test_stop_releases_everything_and_late_audio_is_ignored(client):
    with _connect(client) as ws:
        _next(ws)
        _start_device(ws)
        ws.send_text(json.dumps({"t": "voice_stop"}))
        _until(ws, _phase("stopped"))
        assert FakeLive.instances[0].finished.wait(3)
        assert _wait(_no_voice_threads)

        # A frame still in flight after stop: no reply, no crash, socket alive.
        ws.send_bytes(b"\x01\x00")
        ws.send_text(json.dumps({"t": "ping"}))
        _until(ws, lambda k, v: k == "json" and v.get("t") == "pong")
        assert FakeLive.instances[0].heard == []


def test_socket_drop_ends_the_session_after_the_grace_and_leaves_no_thread(
    client, monkeypatch
):
    from fastapi.testclient import TestClient

    from kravein import server

    # A drop no longer stops voice at once (#110); it does when the grace ends.
    # One portal for the whole test, or the timer's loop dies with the socket.
    monkeypatch.setattr(server, "_voice_grace", lambda: 0.2)
    with TestClient(client.app, base_url="http://127.0.0.1:8787") as shared:
        with _connect(shared) as ws:
            _next(ws)
            _start_device(ws)
            fake = FakeLive.instances[0]
        assert fake.finished.wait(3)
    assert fake.stopped.is_set()
    assert _wait(_no_voice_threads)


def test_oversize_and_stray_binary_frames_are_dropped(client):
    with _connect(client) as ws:
        _next(ws)
        # No session yet: dropped, and the socket keeps answering.
        ws.send_bytes(b"\x01\x00")
        _start_device(ws)
        ws.send_bytes(b"\x00" * (MAX_AUDIO_FRAME + 2))
        ws.send_bytes(b"\x05\x00")
        _until(ws, lambda k, v: k == "bytes")
        assert FakeLive.instances[0].heard == [b"\x05\x00"]
        ws.send_text(json.dumps({"t": "voice_stop"}))
        _until(ws, _phase("stopped"))


def test_device_audio_needs_live_and_is_never_downgraded(client, monkeypatch):
    with _connect(client) as ws:
        _next(ws)
        # An explicit local pipeline cannot use a remote mic.
        ws.send_text(json.dumps({"t": "voice_start", "mode": "local", "audio": "device"}))
        frame = _until(ws, _phase("error"))[-1][1]
        assert "Live" in frame["text"]

        # No Live key: refused, not quietly run on the laptop's mic.
        monkeypatch.setattr(voice, "live_available", lambda: (False, "No Gemini key"))
        ws.send_text(json.dumps({"t": "voice_start", "mode": "auto", "audio": "device"}))
        frame = _until(ws, _phase("error"))[-1][1]
        assert "No Gemini key" in frame["text"]
    assert FakeLive.instances == []


def test_laptop_audio_is_unchanged(client):
    with _connect(client) as ws:
        _next(ws)
        ws.send_text(json.dumps({"t": "voice_start", "mode": "live"}))
        started = _until(ws, _phase("started"))[-1][1]
        assert started["audio"] == "laptop"
        # Binary frames mean nothing without a device session.
        ws.send_bytes(b"\x01\x00")
        ws.send_text(json.dumps({"t": "voice_stop"}))
        _until(ws, _phase("stopped"))
    assert FakeLive.instances[0].source is None  # default SoundDevice path


def test_audio_out_drops_oldest_and_clear_keeps_order():
    async def run():
        out = _AudioOut()
        for i in range(_AudioOut.MAX_ITEMS + 5):
            out.put(bytes([i % 256]))
        first = await out.get()
        assert first == bytes([5])          # the five oldest were dropped
        out.clear()
        out.put(b"new")
        marker = await out.get()
        assert marker is not b"new" and not isinstance(marker, bytes)
        assert await out.get() == b"new"    # stale audio is gone, new survives

    asyncio.run(run())
