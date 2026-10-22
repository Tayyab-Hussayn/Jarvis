"""`serve --lan` must serve the address asked for and never guess among several."""
from __future__ import annotations

import pytest

from kravein import cli, server, webauth

DOCKER, WIFI = "172.17.0.1", "192.168.1.20"


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server, "serve", lambda **kw: calls.append(kw) or 0
    )
    return calls


def _run(monkeypatch, addresses, *argv):
    monkeypatch.setattr(webauth, "local_addresses", lambda: addresses)
    return cli.main(["serve", "--lan", "--no-open", *argv])


def test_explicit_host_is_honoured(monkeypatch, served):
    assert _run(monkeypatch, [DOCKER, WIFI], "--host", WIFI) == 0
    assert served[0]["host"] == WIFI


def test_foreign_host_is_refused_and_candidates_listed(monkeypatch, served, capsys):
    assert _run(monkeypatch, [DOCKER, WIFI], "--host", "10.9.9.9") == 1
    out = capsys.readouterr().out
    assert "10.9.9.9" in out and DOCKER in out and WIFI in out
    assert served == []


def test_public_host_is_refused(monkeypatch, served):
    assert _run(monkeypatch, [WIFI], "--host", "8.8.8.8") == 1
    assert served == []


def test_ambiguous_without_host_refuses_and_lists(monkeypatch, served, capsys):
    assert _run(monkeypatch, [DOCKER, WIFI]) == 1
    out = capsys.readouterr().out
    assert DOCKER in out and WIFI in out
    assert served == []


def test_single_address_is_used(monkeypatch, served):
    assert _run(monkeypatch, [WIFI]) == 0
    assert served[0]["host"] == WIFI


def test_no_address_refuses(monkeypatch, served):
    assert _run(monkeypatch, []) == 1
    assert served == []
