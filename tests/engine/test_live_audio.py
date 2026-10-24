"""Tests: pluggable audio I/O for Gemini Live (#83), against a fake Live backend."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import config, live  # noqa: E402
from kravein.live import Control, LiveSession, NetworkAudio  # noqa: E402


class FakeSession:
    def __init__(self, script):
        self.sent: list[bytes] = []
        self._script = script  # async callable(session) -> yields responses

    async def send_realtime_input(self, media):
        self.sent.append(media["data"])

    async def send_tool_response(self, function_responses):
        pass

    async def receive(self):
        async for item in self._script(self):
            yield item
        await asyncio.Event().wait()  # then park on the "socket", like the real one


class FakeConnect:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def backend(monkeypatch):
    """Install a fake genai client; returns the FakeSession once connected."""
    holder = SimpleNamespace(session=None, script=None)

    async def idle(_s):
        return
        yield  # pragma: no cover

    holder.script = idle

    class Client:
        def __init__(self, **_kw):
            holder.session = FakeSession(lambda s: holder.script(s))
            self.aio = SimpleNamespace(live=SimpleNamespace(
                connect=lambda model, config: FakeConnect(holder.session)))

    import google.genai as genai

    monkeypatch.setattr(genai, "Client", Client)
    monkeypatch.setattr(config, "gemini_api_key", lambda: "k")
    monkeypatch.setattr(LiveSession, "_config", lambda self: None)
    monkeypatch.setattr(config, "STATE_DIR", Path("/nonexistent-kravein-test"))
    return holder


def _run_in_thread(session):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("turns", session.run()), daemon=True)
    t.start()
    return t, out


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _resp(**kw):
    return SimpleNamespace(data=kw.get("data"), server_content=kw.get("server_content"),
                           tool_call=None)


def test_input_audio_reaches_the_model(backend):
    net = NetworkAudio()
    s = LiveSession(source=net, sink=net)
    t, _ = _run_in_thread(s)
    net.push_input(b"\x01\x00\x02\x00")
    assert _wait(lambda: backend.session and backend.session.sent)
    assert b"".join(backend.session.sent) == b"\x01\x00\x02\x00"
    s.stop()
    t.join(3)
    assert not t.is_alive()


def test_audio_pushed_before_the_session_is_up_is_not_lost(backend):
    net = NetworkAudio()
    net.push_input(b"\x07\x00")
    s = LiveSession(source=net, sink=net)
    t, _ = _run_in_thread(s)
    assert _wait(lambda: backend.session and backend.session.sent)
    s.stop()
    t.join(3)


def test_odd_byte_chunks_never_split_a_sample(backend):
    net = NetworkAudio()
    s = LiveSession(source=net, sink=net)
    t, _ = _run_in_thread(s)
    net.push_input(b"\x01\x00\x02")
    net.push_input(b"\x00\x03\x00")
    assert _wait(lambda: len(b"".join(backend.session.sent if backend.session else [])) == 6)
    assert b"".join(backend.session.sent) == b"\x01\x00\x02\x00\x03\x00"
    s.stop()
    t.join(3)


def test_model_audio_comes_out_of_the_sink(backend):
    async def script(_s):
        yield _resp(data=b"\x10\x00\x20\x00")

    backend.script = script
    net = NetworkAudio()
    s = LiveSession(source=net, sink=net)
    t, _ = _run_in_thread(s)
    assert net.pull_output(timeout=3) == b"\x10\x00\x20\x00"
    s.stop()
    t.join(3)
    assert not t.is_alive()


def test_barge_in_clears_queued_playback(backend):
    gate = threading.Event()

    async def script(_s):
        yield _resp(data=b"\x01\x00")
        yield _resp(data=b"\x02\x00")
        # Wait until the test has not collected anything, then interrupt.
        while not gate.is_set():
            await asyncio.sleep(0.01)
        yield _resp(server_content=SimpleNamespace(interrupted=True))

    backend.script = script
    net = NetworkAudio()
    s = LiveSession(source=net, sink=net)
    t, _ = _run_in_thread(s)
    assert _wait(lambda: net._out.qsize() == 2)  # uncollected audio is queued
    gate.set()
    assert _wait(lambda: net._out.qsize() == 1)
    # Whatever was uncollected is gone; the client is told to flush its own buffer.
    assert net.pull_output(timeout=1) is Control.CLEAR
    s.stop()
    t.join(3)


def test_client_disconnect_ends_the_session_and_wakes_the_reader(backend):
    net = NetworkAudio()
    s = LiveSession(source=net, sink=net)
    t, out = _run_in_thread(s)
    assert _wait(lambda: backend.session is not None)
    net.close()
    t.join(3)
    assert not t.is_alive() and "turns" in out
    assert net.pull_output(timeout=1) is Control.CLOSED
    assert net.closed


def test_stop_closes_network_audio_so_a_blocked_reader_wakes(backend):
    net = NetworkAudio()
    s = LiveSession(source=net, sink=net)
    t, _ = _run_in_thread(s)
    got = []
    reader = threading.Thread(target=lambda: got.append(net.pull_output(timeout=5)), daemon=True)
    reader.start()
    assert _wait(lambda: backend.session is not None)
    s.stop()
    reader.join(3)
    t.join(3)
    assert got == [Control.CLOSED]
    assert not t.is_alive() and not reader.is_alive()


def test_push_after_close_is_ignored():
    net = NetworkAudio()
    net.close()
    net.push_input(b"\x01\x00")  # must not raise or queue
    assert net.pull_output(timeout=0) is Control.CLOSED
    assert net.pull_output(timeout=0) is None


def test_output_backlog_is_bounded():
    net = NetworkAudio()
    for i in range(NetworkAudio.MAX_OUTPUT_ITEMS + 50):
        net._emit_out(bytes([i % 256, 0]))
    assert net._out.qsize() == NetworkAudio.MAX_OUTPUT_ITEMS


def test_default_path_is_sounddevice(monkeypatch):
    s = LiveSession()
    assert isinstance(s._source, live.SoundDeviceSource)
    assert isinstance(s._sink, live.SoundDeviceSink)


def test_network_rates_are_the_gemini_live_rates():
    assert live.SEND_SAMPLE_RATE == 16000 and live.RECEIVE_SAMPLE_RATE == 24000


def test_default_laptop_path_still_streams_through_sounddevice(backend, monkeypatch):
    written = []
    callbacks = []

    class In:
        def __init__(self, **kw):
            callbacks.append(kw["callback"])
            assert kw["samplerate"] == 16000 and kw["dtype"] == "int16"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Out:
        def __init__(self, **kw):
            assert kw["samplerate"] == 24000

        def start(self): pass
        def write(self, chunk): written.append(chunk)
        def abort(self): pass
        def close(self): pass

    monkeypatch.setitem(sys.modules, "sounddevice",
                        SimpleNamespace(RawInputStream=In, RawOutputStream=Out))

    async def script(_s):
        yield _resp(data=b"\x05\x00")

    backend.script = script
    s = LiveSession()
    t, _ = _run_in_thread(s)
    assert _wait(lambda: callbacks)
    callbacks[0](b"\x09\x00", 1, None, None)
    assert _wait(lambda: written and backend.session.sent)
    assert written == [b"\x05\x00"] and backend.session.sent == [b"\x09\x00"]
    s.stop()
    t.join(3)
    assert not t.is_alive()
