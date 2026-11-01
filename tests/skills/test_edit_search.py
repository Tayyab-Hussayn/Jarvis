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


# --- review findings (#136) -------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home with Kravein dirs, so sensitive classes are testable."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(safety.Path, "home", classmethod(lambda cls: h))
    from kravein import config
    for name, sub in (("CONFIG_DIR", ".config/kravein"), ("STATE_DIR", ".local/state/kravein"),
                      ("DATA_DIR", ".local/share/kravein")):
        d = h / sub
        d.mkdir(parents=True)
        monkeypatch.setattr(config, name, d)
    ws = h / ".local/share/kravein/workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return h


@pytest.mark.parametrize("rel", [".config/kravein/api_keys.json", ".local/state/kravein/x",
                                 ".local/share/kravein/memory.json"])
def test_kravein_own_dirs_are_blocked_for_writes(home, rel):
    v = safety.check_path(home / rel, write=True)
    assert v.blocked and safety.sensitive_kind(home / rel) == "blocked"


def test_kravein_workspace_is_still_writable(home):
    v = safety.check_path(home / ".local/share/kravein/workspace/a.txt", write=True)
    assert not v.blocked and v.danger < safety.Danger.DESTRUCTIVE


@pytest.mark.parametrize("rel", [".ssh/authorized_keys", ".bashrc", ".zshrc", ".profile",
                                 ".config/fish/config.fish", ".config/systemd/user/a.service",
                                 ".config/autostart/x.desktop", ".gnupg/gpg.conf"])
def test_login_surfaces_need_confirmation(home, rel):
    v = safety.check_path(home / rel, write=True)
    assert not v.blocked and v.danger >= safety.Danger.DESTRUCTIVE
    assert safety.sensitive_kind(home / rel) == "confirm"


def test_write_file_shares_the_classification(home, enforced):
    files.write_file(str(home / ".bashrc"), "x")
    assert enforced[0][0].danger >= safety.Danger.DESTRUCTIVE


def test_edit_in_sensitive_location_is_destructive_and_blocked_one_refuses(home, enforced):
    rc = home / ".bashrc"
    rc.write_text("a\n" * 50 + "export X=1\n")
    files.edit_file(str(rc), "export X=1", "export X=2")
    assert enforced[0][0].danger >= safety.Danger.DESTRUCTIVE


def test_edit_of_kravein_private_file_is_refused_before_reading(home):
    keys = home / ".config/kravein/api_keys.json"
    keys.write_text('{"k": "v"}' + " " * 100)
    with pytest.raises(safety.SafetyError):
        files.edit_file(str(keys), '"v"', '"w"')
    assert '"v"' in keys.read_text()


def test_large_replacement_is_treated_as_overwrite(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("line one\nline two\n")
    files.edit_file(str(f), "line one\nline two", "all new")
    assert enforced[0][0].danger >= safety.Danger.DESTRUCTIVE
    g = tmp_path / "b.txt"
    g.write_text("x" * 100 + "needle" + "y" * 100)
    files.edit_file(str(g), "needle", "pin")
    assert enforced[1][0].danger < safety.Danger.DESTRUCTIVE


def test_confirmation_text_previews_the_change(tmp_path, enforced):
    f = tmp_path / "a.txt"
    f.write_text("a\nb\nTARGET value\nc\n" * 3 + "unique line\n")
    files.edit_file(str(f), "unique line", "changed " * 30)
    desc = enforced[0][1]
    assert "line 13" in desc and "- 'unique line'" in desc and "+ 'changed" in desc
    assert "…" in desc  # long new text is clipped


def test_edit_through_symlink_keeps_the_link(tmp_path, enforced):
    real = tmp_path / "real.conf"
    real.write_text("a=1\nb=2\nc=3\nd=4\n")
    link = tmp_path / "link.conf"
    link.symlink_to(real)
    files.edit_file(str(link), "c=3", "c=9")
    assert link.is_symlink() and real.read_text() == "a=1\nb=2\nc=9\nd=4\n"
    assert enforced[0][2]["targets"] == [f"file:{real}".lower()]


def test_search_refuses_sensitive_roots_and_skips_secret_files(home):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("needle")
    assert "Refused" in files.search_text("needle", str(home / ".ssh"))
    assert "Refused" in files.search_text("needle", str(home / ".config/kravein"))
    proj = home / "proj"
    proj.mkdir()
    for n in ("key.pem", "id_ed25519", "api_keys.json", ".env", "ok.txt", "prod.key"):
        (proj / n).write_text("needle")
    out = files.search_text("needle", str(proj))
    assert "ok.txt" in out
    for n in ("key.pem", "id_ed25519", "api_keys.json", ".env", "prod.key"):
        assert n not in out
    (home / ".bashrc").write_text("needle")
    assert ".bashrc" not in files.search_text("needle", str(home))
