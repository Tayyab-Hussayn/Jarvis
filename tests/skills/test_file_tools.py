"""#212: line-numbered reads, read-before-edit, multi-edit, diff, regex search, mtime find."""

from __future__ import annotations

import os
import types

import pytest

from kravein import registry, safety
from kravein.safety import Danger, Verdict
from kravein.skills import files


@pytest.fixture
def enforced(monkeypatch):
    calls = []
    monkeypatch.setattr(files, "enforce",
                        lambda verdict, desc, **kw: calls.append((verdict, desc, kw)))
    return calls


def agent():
    """Stands in for the calling Agent: only tool_state matters to files.py."""
    return types.SimpleNamespace(tool_state={})


# --- read_file ---------------------------------------------------------------

def test_read_numbers_lines_clips_and_continues(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 31)) + "\n" + "x" * 3000 + "\n")
    out = files.read_file(str(f), max_lines=10)
    assert " 1\tline1" in out and "10\tline10" in out and "11\tline11" not in out
    assert "21 more line(s); continue with start_line=11" in out
    tail = files.read_file(str(f), start_line=31)
    assert "31\t" + "x" * 2000 + "… [line cut, 1000 more chars]" in tail
    assert "continue with" not in tail


def test_read_records_stamp_only_with_agent_state(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi\n")
    ctx = agent()
    files.read_file(str(f), _context=ctx)
    st = f.stat()
    assert ctx.tool_state["files_read"][str(f.resolve())] == (st.st_mtime_ns, st.st_size)
    files.read_file(str(f), _context=object())  # no tool_state: must not raise


def test_read_enforces_check_read(tmp_path, monkeypatch, enforced):
    f = tmp_path / "a.txt"
    f.write_text("secret\n")
    monkeypatch.setattr(safety, "check_read",
                        lambda p: Verdict(danger=Danger.CRITICAL, blocked=True,
                                          block_reason="no"))
    files.read_file(str(f))
    assert enforced and enforced[0][2]["skill"] == "read_file"
    assert enforced[0][0].blocked


# --- read-before-edit --------------------------------------------------------

def test_edit_requires_a_read_from_an_agent(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("alpha\n")
    out = files.edit_file(str(f), "alpha", "beta", _context=agent())
    assert out.startswith("Refused") and "Read" in out and "first" in out
    assert f.read_text() == "alpha\n"


def test_edit_refuses_stale_read(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("alpha\n")
    ctx = agent()
    files.read_file(str(f), _context=ctx)
    f.write_text("alpha changed\n")
    os.utime(f, ns=(1, 1))  # different mtime even on a coarse clock
    out = files.edit_file(str(f), "alpha", "beta", _context=ctx)
    assert "changed since you read it; read it again" in out
    assert f.read_text() == "alpha changed\n"


def test_edit_after_fresh_read_then_second_edit_without_reread(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("alpha\nbeta\n")
    ctx = agent()
    files.read_file(str(f), _context=ctx)
    assert files.edit_file(str(f), "alpha", "A", _context=ctx).startswith("Edited")
    assert files.edit_file(str(f), "beta", "B", _context=ctx).startswith("Edited")
    assert f.read_text() == "A\nB\n"


def test_cli_edit_without_context_needs_no_read(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("alpha\n")
    assert files.edit_file(str(f), "alpha", "beta").startswith("Edited")


def test_write_overwrite_needs_read_but_create_does_not(tmp_path, enforced):
    ctx = agent()
    new = tmp_path / "new.txt"
    assert files.write_file(str(new), "x", _context=ctx).startswith("Wrote")
    # Creating recorded the file as known, so a rewrite right after is allowed.
    assert files.write_file(str(new), "y", _context=ctx).startswith("Wrote")
    old = tmp_path / "old.txt"
    old.write_text("keep me\n")
    out = files.write_file(str(old), "z", _context=agent())
    assert out.startswith("Refused") and old.read_text() == "keep me\n"
    ctx2 = agent()
    files.read_file(str(old), _context=ctx2)
    assert files.write_file(str(old), "z", _context=ctx2).startswith("Wrote")


# --- edit_file features --------------------------------------------------------

def test_replace_all_and_default_still_refuses_duplicates(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("x\ny\nx\n")
    assert "matches 2 places" in files.edit_file(str(f), "x", "z") and "lines 1, 3" in \
        files.edit_file(str(f), "x", "z")
    out = files.edit_file(str(f), "x", "z", replace_all=True)
    assert "2 replacement(s)" in out and f.read_text() == "z\ny\nz\n"


def test_multi_edit_applies_in_order(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("one\ntwo\n")
    out = files.edit_file(str(f), edits=[
        {"old_text": "one", "new_text": "1"},
        {"old_text": "1\ntwo", "new_text": "1-2"},  # depends on the first edit
    ])
    assert f.read_text() == "1-2\n" and out.startswith("Edited")


def test_multi_edit_is_all_or_nothing(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("one\ntwo\n")
    out = files.edit_file(str(f), edits=[
        {"old_text": "one", "new_text": "1"},
        {"old_text": "missing", "new_text": "x"},
    ])
    assert "edit 2 of 2" in out and "not found" in out and "Nothing changed" in out
    assert f.read_text() == "one\ntwo\n"
    assert not enforced  # refused before any confirmation
    assert not list(tmp_path.glob(".*kravein-edit"))


def test_edit_result_and_card_contain_capped_diff(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("".join(f"keep\nline {i}\n" for i in range(200)))
    out = files.edit_file(str(f), "line", "LINE", replace_all=True)
    assert "--- a/a.txt" in out and "-line 0" in out and "+LINE 0" in out
    assert "diff truncated" in out
    desc = enforced[0][1]
    assert "+LINE 0" in desc and "diff truncated" in desc


def test_edit_schema_accepts_list_of_objects():
    schema = registry.build_schema(files.edit_file)
    props = schema["parameters"]["properties"]
    assert props["edits"]["type"] == "array" and props["edits"]["items"] == {"type": "object"}
    assert "replace_all" in props and "_context" not in props
    assert schema["parameters"]["required"] == ["path"]


# --- search_text ---------------------------------------------------------------

@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a.py").write_text("import os\ndef foo():\n    return 1\n# foo end\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("FOO bar\nnothing\nfoo again\n")
    (tmp_path / "id_rsa").write_text("foo secret\n")
    return tmp_path


def test_invalid_regex_returns_the_error(tree):
    out = files.search_text("fo(", str(tree), regex=True)
    assert out.startswith("Invalid regex") and "missing )" in out


def test_regex_and_plain_text_differ(tree):
    assert "a.py:2:" in files.search_text(r"def \w+\(", str(tree), regex=True)
    assert files.search_text(r"def \w+\(", str(tree)).startswith("No matches")


def test_context_lines(tree):
    out = files.search_text("def foo", str(tree), context_lines=1)
    lines = out.splitlines()
    assert any(l.endswith(":2: def foo():") for l in lines)
    assert any("a.py-1- import os" in l for l in lines)
    assert any("a.py-3-" in l and "return 1" in l for l in lines)


def test_output_modes(tree):
    paths = files.search_text("foo", str(tree), output="files").splitlines()
    assert [os.path.basename(p) for p in paths] == ["a.py", "b.txt"]
    counts = files.search_text("foo", str(tree), output="count").splitlines()
    assert counts[0].endswith("a.py: 2") and counts[1].endswith("b.txt: 2")


def test_pagination_and_past_the_end(tree):
    first = files.search_text("foo", str(tree), max_results=2)
    assert "stopped at 2 of 4" in first and "offset=2" in first
    second = files.search_text("foo", str(tree), max_results=2, offset=2)
    assert "b.txt" in second and "stopped" not in second
    out = files.search_text("foo", str(tree), offset=99)
    assert out.startswith("no results at offset 99 (total 4)")


def test_secret_names_are_never_searched(tree):
    assert "id_rsa" not in files.search_text("foo", str(tree), max_results=50)


@pytest.mark.skipif(not files.shell.have("rg"), reason="ripgrep not installed")
@pytest.mark.parametrize("kwargs", [
    {}, {"regex": True, "query": r"fo+\b"}, {"context_lines": 1},
    {"output": "files"}, {"output": "count"}, {"glob": "*.py"},
])
def test_rg_and_python_fallback_agree(tree, monkeypatch, kwargs):
    kw = {"query": "foo", **kwargs}
    with_rg = files.search_text(directory=str(tree), **kw)
    real_have = files.shell.have
    monkeypatch.setattr(files.shell, "have", lambda b: False if b == "rg" else real_have(b))
    assert files.search_text(directory=str(tree), **kw) == with_rg


def test_rg_failure_falls_back(tree, monkeypatch):
    monkeypatch.setattr(files.shell, "have", lambda b: b == "rg")
    monkeypatch.setattr(files.shell, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=2, stdout="",
                                                              stderr="boom", timed_out=False))
    assert "a.py:2:" in files.search_text("foo", str(tree))


def test_search_skips_and_counts_check_read_blocked(tree, monkeypatch):
    def check(p):
        if p.name == "b.txt":
            return Verdict(danger=Danger.DESTRUCTIVE)
        return Verdict(danger=Danger.SAFE)
    monkeypatch.setattr(safety, "check_read", check)
    out = files.search_text("foo", str(tree))
    assert "b.txt" not in out and "a.py:2:" in out
    assert "1 file(s) skipped" in out


# --- find_files -----------------------------------------------------------------

def test_find_files_newest_first_and_cap_note(tmp_path):
    for i in range(5):
        p = tmp_path / f"f{i}.log"
        p.write_text("x")
        os.utime(p, (1000 + i, 1000 + i))
    out = files.find_files("*.log", str(tmp_path), max_results=3)
    names = [l.split()[0].rsplit("/", 1)[-1] for l in out.splitlines()[1:4]]
    assert names == ["f4.log", "f3.log", "f2.log"]
    assert "2 more not shown" in out and "narrow" in out
    assert "more not shown" not in files.find_files("*.log", str(tmp_path), max_results=10)


# --- review fixes -----------------------------------------------------------------

def test_hidden_files_and_formfeed_lines_agree_across_engines(tmp_path, monkeypatch):
    (tmp_path / ".hidden.txt").write_text("foo\n")
    (tmp_path / "ff.txt").write_text("a\x0cfoo\nb\n")
    with_rg = files.search_text("foo", str(tmp_path))
    real_have = files.shell.have
    monkeypatch.setattr(files.shell, "have", lambda b: False if b == "rg" else real_have(b))
    assert files.search_text("foo", str(tmp_path)) == with_rg
    assert ".hidden" not in with_rg and "ff.txt:1:" in with_rg
    assert "1\ta\x0cfoo" in files.read_file(str(tmp_path / "ff.txt"))


def test_context_marks_only_this_pages_matches(tmp_path):
    (tmp_path / "x.txt").write_text("foo\nfoo\nfoo\n")
    out = files.search_text("foo", str(tmp_path), max_results=1, context_lines=1)
    lines = out.splitlines()
    assert lines[0].endswith("x.txt:1: foo") and lines[1].endswith("x.txt-2- foo")


def test_find_files_content_scan_stops_after_one_page(tmp_path, monkeypatch):
    for i in range(6):
        (tmp_path / f"f{i}.txt").write_text("needle")
    reads = []
    real = files.Path.read_text
    monkeypatch.setattr(files.Path, "read_text",
                        lambda self, *a, **k: (reads.append(self), real(self, *a, **k))[1])
    out = files.find_files("*.txt", str(tmp_path), max_results=2, content="needle")
    assert len(reads) == 3 and "more not shown" in out


def test_write_file_declares_its_target(tmp_path, enforced):
    files.write_file(str(tmp_path / "n.txt"), "x")
    assert enforced[0][2]["targets"] == [f"file:{tmp_path / 'n.txt'}"]
