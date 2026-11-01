"""edit_file (exact unique replace) and search_text (capped content search)."""

from __future__ import annotations

import pytest

from kravein import registry, safety
from kravein.skills import files


@pytest.fixture
def enforced(monkeypatch):
    calls = []
    monkeypatch.setattr(
        files, "enforce",
        lambda verdict, desc, **kw: calls.append((verdict, desc, kw)),
    )
    return calls


def test_edit_replaces_exactly_one_match_and_declares_target(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    out = files.edit_file(str(f), "beta", "BETA")
    assert "Edited" in out and "line 2" in out
    assert f.read_text() == "alpha\nBETA\ngamma\n"
    _, _, kw = enforced[0]
    assert kw["skill"] == "edit_file"
    assert kw["targets"] == [f"file:{f}".lower()]


def test_edit_refuses_ambiguous_match_and_lists_lines(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("x\nfoo\nfoo\n")
    out = files.edit_file(str(f), "foo", "bar")
    assert out.startswith("Refused") and "2 places" in out and "2, 3" in out
    assert f.read_text() == "x\nfoo\nfoo\n"


def test_edit_refuses_missing_text(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert files.edit_file(str(f), "nope", "x").startswith("Refused")
    assert f.read_text() == "hello"


def test_edit_refuses_empty_old_text_and_noop(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert files.edit_file(str(f), "", "x").startswith("Refused")
    assert files.edit_file(str(f), "hello", "hello").startswith("Nothing to do")
    assert not enforced  # refused before anything was touched


def test_edit_keeps_crlf_and_mode(tmp_path, enforced):
    f = tmp_path / "s.sh"
    f.write_bytes(b"a\r\nb\r\n")
    f.chmod(0o755)
    files.edit_file(str(f), "b", "c")
    assert f.read_bytes() == b"a\r\nc\r\n"
    assert f.stat().st_mode & 0o777 == 0o755
    assert not list(tmp_path.glob(".*kravein-edit"))


def test_edit_refuses_missing_file_and_binary(tmp_path, enforced):
    assert files.edit_file(str(tmp_path / "none"), "a", "b").startswith("Not a file")
    b = tmp_path / "b.bin"
    b.write_bytes(b"\xff\xfe\x00")
    assert "not UTF-8" in files.edit_file(str(b), "a", "b")


def test_edit_goes_through_real_enforce_for_protected_path():
    with pytest.raises(safety.SafetyError):
        files.edit_file("/etc/hostname", "a", "b")


def test_edit_is_in_the_tool_schema():
    names = {d["name"] for d in registry.tool_declarations()}
    assert {"edit_file", "search_text"} <= names


def test_search_finds_lines_and_honours_glob(tmp_path):
    (tmp_path / "a.py").write_text("one\nNeedle here\n")
    (tmp_path / "b.txt").write_text("needle too\n")
    out = files.search_text("needle", str(tmp_path))
    assert "a.py:2: Needle here" in out and "b.txt:1" in out
    only_py = files.search_text("needle", str(tmp_path), glob="*.py")
    assert "a.py" in only_py and "b.txt" not in only_py


def test_search_caps_output(tmp_path):
    (tmp_path / "big.txt").write_text("hit\n" * 500)
    out = files.search_text("hit", str(tmp_path), max_results=7)
    assert len([l for l in out.splitlines() if "big.txt:" in l]) == 7
    assert "stopped at 7" in out
    huge = files.search_text("hit", str(tmp_path), max_results=10_000)
    assert len([l for l in huge.splitlines() if "big.txt:" in l]) == 200


def test_search_skips_vendored_hidden_and_binary(tmp_path):
    for d in ("node_modules", ".git", ".hidden"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.txt").write_text("needle")
    (tmp_path / "bin.dat").write_bytes(b"needle\x00\x01")
    out = files.search_text("needle", str(tmp_path))
    assert out.startswith("No matches")


def test_search_rejects_bad_input(tmp_path):
    assert "Give some text" in files.search_text("", str(tmp_path))
    assert "Not a directory" in files.search_text("x", str(tmp_path / "nope"))
