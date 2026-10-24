"""TLS for `serve --lan` (#65): certificate lifecycle, pin, offer fields, Origin."""

from __future__ import annotations

import hashlib
import os
import socket
import ssl
import threading

import pytest

from kravein import config, phone, webauth


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(phone, "_offer", None)
    monkeypatch.setattr(phone.audit, "record", lambda *a, **k: None)
    webauth.clear_lan_hosts()


def test_generates_once_and_reuses():
    cert, key, fp, fresh = webauth.ensure_tls_cert("192.168.1.20")
    assert fresh and len(fp) == 64 and fp == fp.lower()
    assert os.stat(key).st_mode & 0o777 == 0o600
    cert2, _, fp2, fresh2 = webauth.ensure_tls_cert("192.168.1.20")
    assert not fresh2 and fp2 == fp and cert2 == cert


def test_keeps_the_certificate_when_the_lan_address_changes():
    # The app pins the fingerprint, not the IP: a new DHCP lease must not
    # force every paired phone to pair again.
    _, _, fp, _ = webauth.ensure_tls_cert("192.168.1.20")
    _, _, fp2, fresh = webauth.ensure_tls_cert("10.0.0.5")
    assert not fresh and fp2 == fp


def test_regenerates_a_corrupt_or_keyless_pair():
    cert, key, fp, _ = webauth.ensure_tls_cert("192.168.1.20")
    key.unlink()
    assert webauth.ensure_tls_cert("192.168.1.20")[3]
    cert.write_text("garbage")
    assert webauth.ensure_tls_cert("192.168.1.20")[3]


def test_regenerates_when_about_to_expire(monkeypatch):
    webauth.ensure_tls_cert("192.168.1.20")
    monkeypatch.setattr(webauth, "TLS_RENEW_BEFORE_DAYS", 10_000)
    assert webauth.ensure_tls_cert("192.168.1.20")[3]


def test_fingerprint_matches_the_served_certificate():
    cert, key, fp, _ = webauth.ensure_tls_cert("127.0.0.1")
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve_once():
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True) as tls:
                tls.recv(1)
        except OSError:
            pass

    threading.Thread(target=serve_once, daemon=True).start()
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(listener.getsockname(), timeout=5) as raw:
        with client_ctx.wrap_socket(raw) as tls:
            der = tls.getpeercert(binary_form=True)
    listener.close()
    assert hashlib.sha256(der).hexdigest() == fp


def test_offer_without_tls_has_no_fingerprint():
    offer = phone.create_offer("192.168.1.20", 8787)
    assert offer["scheme"] == "http" and "cert_sha256" not in offer


def test_offer_with_tls_carries_the_pin():
    fp = "AB" * 32
    offer = phone.create_offer("192.168.1.20", 8787, scheme="https", cert_sha256=fp)
    assert offer["scheme"] == "https" and offer["cert_sha256"] == "ab" * 32
    assert offer["v"] == phone.protocol_version()


def test_https_offer_without_fingerprint_is_refused():
    with pytest.raises(ValueError):
        phone.create_offer("192.168.1.20", 8787, scheme="https")


def test_api_offer_reflects_tls_state():
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    app = create_app("tok")
    app.state.public_host, app.state.public_port = "192.168.1.20", 8787
    webauth.allow_lan_host("192.168.1.20")
    client = TestClient(app, base_url="http://192.168.1.20:8787")
    auth = {"Authorization": "Bearer tok"}
    plain = client.post("/api/phone/offer", headers=auth).json()["offer"]
    assert plain["scheme"] == "http" and "cert_sha256" not in plain
    app.state.cert_sha256 = "cd" * 32
    secure = client.post("/api/phone/offer", headers=auth).json()["offer"]
    assert secure["scheme"] == "https" and secure["cert_sha256"] == "cd" * 32


def test_origin_check_with_https():
    webauth.allow_lan_host("192.168.1.20")
    assert webauth.origin_allowed("https://192.168.1.20:8787")
    assert not webauth.origin_allowed("https://evil.example.com")
    assert not webauth.origin_allowed("https://192.168.1.99:8787")
    assert webauth.host_allowed("192.168.1.20:8787")
    assert not webauth.host_allowed("evil.example.com")
