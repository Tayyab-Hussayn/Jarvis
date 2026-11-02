"""Tests: "Always approve" rules and the three-button confirm contract (#199)."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import audit, config, events, safety  # noqa: E402
from kravein.safety import Danger, Verdict, classify_command  # noqa: E402

LIVE = ["web", "web-panel", "voice", "voice-live", "cli", "cli-ask"]
NOT_LIVE = ["daemon-run", "whatsapp", "cli-run", "local", "test", ""]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(config, "DEFAULT_SETTINGS", copy.deepcopy(config.DEFAULT_SETTINGS))
    monkeypatch.setattr(safety, "_auto_until", None)
    log: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda ev, **kw: log.append((ev, kw)))
    yield {"home": home, "log": log}
    safety.set_confirmer(None)


@pytest.fixture
def home(env):
    return env["home"]


def _verdict(command, home):
    return classify_command(command, cwd=home)


def _ask(verdict, *, source="web", answer=True, principal="browser", action="run `x`",
         targets=None, memory=None):
    """enforce() through a confirmer that records the offer it was handed."""
    asked: list[dict] = []

    def confirm(a, d, level):
        asked.append(safety.always_offer())
        return answer

    with safety.confirmer_scope(confirm, memory, principal=principal):
        safety.enforce(verdict, action, skill="run_command", source=source, targets=targets)
    return asked


# -- signatures --------------------------------------------------------------

def test_pkill_signature_keeps_the_name(home):
    assert _verdict("pkill firefox", home).signature == "pkill firefox"
    assert _verdict("pkill -9 firefox", home).signature == "pkill -9 firefox"
    assert _verdict("killall -TERM vlc", home).signature == "killall -TERM vlc"


@pytest.mark.parametrize("command", [
    "pkill -f firefox", "pkill -u root x1", "pkill", "pkill a b", "pkill 'fire*'", "pkill x",
    "pkill hyprland", "pkill niri", "pkill systemd", "pkill Xorg", "pkill sshd", "pkill bash",
    "pkill python3", "pkill kravein-web", "kill 123", "pkill foo; ls", "pkill foo > out",
    "pkill foo | cat", "pkill -s 9 foo",
])
def test_pkill_signature_refused(command, home):
    assert _verdict(command, home).signature == "", command


def test_chmod_signature_non_recursive_own_files(home):
    f = home / "run.sh"
    f.write_text("x")
    assert _verdict(f"chmod +x {f}", home).signature == "chmod +x *"
    assert _verdict(f"chmod 644 {f}", home).signature == "chmod 644 *"
    assert _verdict(f"chmod u+x,g-w {f}", home).signature == "chmod u+x,g-w *"
    d = home / "d"
    d.mkdir()
    assert _verdict(f"chmod 755 {d}", home).signature == "chmod 755 *"


def test_chmod_signature_refused_cases(home, tmp_path):
    f = home / "run.sh"
    f.write_text("x")
    outside = tmp_path / "outside.sh"
    outside.write_text("x")
    link = home / "link"
    link.symlink_to(f)
    for command in [
        f"chmod -R 755 {home}", f"chmod -R +x {f}", f"chmod --recursive 755 {f}",
        f"chmod 4755 {f}", f"chmod u+s {f}", f"chmod +t {f}", f"chmod 2755 {f}",
        f"chmod +x {outside}", f"chmod +x {link}", f"chmod +x {home}/missing",
        f"chmod +x {f} && ls", f"chmod +x {home}/*", f"chmod --reference={f} {f}",
        "chmod +x run.sh",                                    # relative, cwd unknown
        f"chmod +x {home}", f"chmod +x {home}/.ssh/authorized_keys",
        f"chown me {f}", f"chown -R me {home}",
    ]:
        assert _verdict(command, None).signature == "", command


def test_chmod_ssh_and_shell_startup_never_eligible(home):
    (home / ".ssh").mkdir()
    key = home / ".ssh" / "id_rsa"
    key.write_text("k")
    rc = home / ".bashrc"
    rc.write_text("x")
    for target in (key, rc):
        assert _verdict(f"chmod 600 {target}", home).signature == ""


def test_mv_signature_over_existing_owned_target(home):
    src, dest = home / "a.txt", home / "b.txt"
    src.write_text("a")
    dest.write_text("b")
    assert _verdict(f"mv {src} {dest}", home).signature == f"mv * {dest}"
    assert _verdict(f"mv -f {src} {dest}", home).signature == f"mv * {dest}"


def test_mv_signature_refused_cases(home, tmp_path):
    src, dest = home / "a.txt", home / "b.txt"
    src.write_text("a")
    dest.write_text("b")
    outside = tmp_path / "o.txt"
    outside.write_text("o")
    for command in [
        f"mv {src} {outside}", f"mv {outside} {dest}", f"mv {src} {dest} {home}/c",
        f"mv -t {home} {src}", f"mv {src} {dest} > {home}/log", f"mv {src} {dest}; ls",
        "mv a b", f"mv {src} /etc/hosts",
    ]:
        assert _verdict(command, None).signature == "", command


def test_signature_only_for_destructive_verdicts(home):
    f = home / "x"
    f.write_text("x")
    assert _verdict("ls", home).signature == ""
    assert _verdict("systemctl --user restart foo", home).signature == ""   # runs unasked
    assert _verdict("sudo pkill foo", home).signature == ""                 # CRITICAL


def test_destructive_family_never_gets_a_signature(home):
    f = home / "x"
    f.write_text("x")
    for command in [
        f"rm {f}", f"rm -rf {home}/d", f"unlink {f}", f"shred {f}", f"truncate -s0 {f}",
        f"dd if=/dev/zero of={f}", f"sed -i s/x/y/ {f}", f"chmod -R 755 {home}",
        f"chown -R me {home}", "git reset --hard", "git clean -fd", "git push --force",
        f"echo x > {f}", "pacman -Rns foo", "sudo rm x", "mkfs.ext4 /dev/sda1",
        f"find {home} -delete", f"ls | xargs rm",
    ]:
        v = _verdict(command, home)
        assert v.signature == "" and (v.blocked or v.needs_confirmation), command


# -- the card offer, rule creation and use -----------------------------------

def test_always_creates_a_rule_and_it_stops_the_question(home, env):
    v = _verdict("pkill firefox", home)
    asked = _ask(v, answer=safety.ALWAYS)
    assert asked == [{"allowed": True, "signature": "pkill firefox"}]
    rules = safety.list_rules("browser")
    assert [r["signature"] for r in rules] == ["pkill firefox"]
    assert any(e == "safety.rule_created" for e, _ in env["log"])

    assert _ask(v) == []                                    # no card the second time
    approved = [kw for e, kw in env["log"] if e == "safety.rule_approved"]
    assert approved and approved[-1]["signature"] == "pkill firefox"
    assert approved[-1]["principal"] == "browser" and approved[-1]["uses"] == 1
    _ask(v)
    assert safety.list_rules("browser")[0]["uses"] == 2     # every use counted


def test_plain_approve_creates_no_rule(home):
    _ask(_verdict("pkill firefox", home), answer=True)
    assert safety.list_rules("browser") == []


def test_always_is_not_offered_for_the_rm_family(home):
    f = home / "x"
    f.write_text("x")
    asked = _ask(_verdict(f"rm {f}", home), answer=safety.ALWAYS)
    assert asked == [{"allowed": False, "signature": ""}]
    assert safety.list_rules("browser") == []               # forged "always" = plain approve


def test_always_is_not_offered_for_critical(home):
    v = Verdict(Danger.CRITICAL, reasons=["sudo"], signature="pkill firefox")
    assert _ask(v, answer=safety.ALWAYS)[0]["allowed"] is False
    assert safety.list_rules("browser") == []


def test_rule_matches_only_its_signature(home):
    _ask(_verdict("pkill firefox", home), answer=safety.ALWAYS)
    assert _ask(_verdict("pkill vlc", home)) != []          # another name still asks
    assert _ask(_verdict("pkill -9 firefox", home)) != []   # another flag shape still asks


def test_rule_is_scoped_per_principal(home):
    v = _verdict("pkill firefox", home)
    _ask(v, answer=safety.ALWAYS, principal="browser")
    assert _ask(v, principal="device:phone1") != []
    assert _ask(v, principal="browser") == []
    assert safety.list_rules("device:phone1") == []


def test_no_principal_no_offer_no_rule(home):
    v = _verdict("pkill firefox", home)
    asked = _ask(v, answer=safety.ALWAYS, principal="")
    assert asked == [{"allowed": False, "signature": ""}]
    assert safety.list_rules("") == [] and safety.list_rules("browser") == []


@pytest.mark.parametrize("source", NOT_LIVE)
def test_rules_never_apply_to_non_live_sources(home, source):
    v = _verdict("pkill firefox", home)
    _ask(v, answer=safety.ALWAYS)                            # owner made a rule on the web
    asked = _ask(v, source=source)
    assert asked != [], source                               # still asked
    # and a card from such a source never offers Always, so none can be made
    assert asked[0]["allowed"] is False


@pytest.mark.parametrize("source", LIVE)
def test_rules_apply_to_live_sources(home, source):
    v = _verdict("pkill firefox", home)
    _ask(v, answer=safety.ALWAYS)
    assert _ask(v, source=source) == []


def test_unattended_session_ignores_rules(home):
    v = _verdict("pkill firefox", home)
    _ask(v, answer=safety.ALWAYS)
    # no confirmer at all
    with safety.confirmer_scope(None, principal="browser"):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(v, "run `pkill firefox`", source="web")


def test_detached_background_confirmer_is_asked_not_bypassed(home):
    v = _verdict("pkill firefox", home)
    _ask(v, answer=safety.ALWAYS)
    called = []

    class Detached:
        def __call__(self, *a):
            called.append(a)
            return False

        def attended(self):
            return False

    with safety.confirmer_scope(Detached(), principal="browser"):
        with pytest.raises(safety.ConfirmationRequired):
            safety.enforce(v, "run `pkill firefox`", source="web")
    assert called                                           # the rule did not answer for them


def test_decline_policy_beats_a_rule(home):
    v = _verdict("pkill firefox", home)
    _ask(v, answer=safety.ALWAYS)
    safety.set_confirmation_mode("decline", by="test")
    with pytest.raises(safety.SafetyError):
        _ask(v)


def test_declined_target_this_turn_beats_a_rule(home):
    src, dest = home / "a.txt", home / "b.txt"
    src.write_text("a")
    dest.write_text("b")
    v = _verdict(f"mv {src} {dest}", home)
    assert v.signature
    _ask(v, answer=safety.ALWAYS)                            # rule exists
    memory = safety.TurnMemory()
    # In this turn the owner declines the same target under another route ...
    other = Verdict(Danger.DESTRUCTIVE, reasons=["deletes"])
    with pytest.raises(safety.ConfirmationRequired):
        _ask(other, answer=False, memory=memory, targets=[f"path:{dest}"])
    # ... so the rule must not approve the retry that names it.
    with pytest.raises(safety.SafetyError):
        _ask(v, memory=memory, targets=[f"path:{dest}"])
    # a different target in the same turn is untouched
    assert _ask(v, memory=memory, targets=["path:/elsewhere"]) == []


def test_confirmer_returning_a_string_is_a_denial(home):
    v = _verdict("pkill firefox", home)
    for answer in ("yes", "approve", "true", "ALWAYS", ""):
        with pytest.raises(safety.ConfirmationRequired):
            _ask(v, answer=answer)
    assert safety.list_rules("browser") == []


def test_always_is_stored_only_from_the_offered_signature(home):
    # The confirmer cannot choose what gets stored: enforce() stores the
    # signature it computed, whatever the answer carried.
    v = _verdict("pkill firefox", home)
    _ask(v, answer=safety.ALWAYS)
    assert [r["signature"] for r in safety.list_rules("browser")] == ["pkill firefox"]


def test_a_hand_edited_store_cannot_widen_a_signature(home):
    safety._save_rules([{"id": "x", "signature": "rm *", "principal": "browser",
                         "created_at": 0, "uses": 0, "last_used": None}])
    f = home / "x"
    f.write_text("x")
    assert _ask(_verdict(f"rm {f}", home)) != []             # signature "" never matches


# -- store: revoke, listing, file safety ------------------------------------

def test_revoke(home, env):
    v = _verdict("pkill firefox", home)
    _ask(v, answer=safety.ALWAYS)
    rule = safety.list_rules("browser")[0]
    assert safety.revoke_rule(rule["id"], "device:other") is False     # not theirs
    assert safety.revoke_rule("nope", "browser") is False
    assert safety.revoke_rule(rule["id"], "browser", by="web") is True
    assert safety.list_rules("browser") == []
    assert any(e == "safety.rule_revoked" for e, _ in env["log"])
    assert _ask(v) != []                                              # asks again


def test_duplicate_always_makes_one_rule(home):
    v = _verdict("pkill firefox", home)
    safety.add_always_rule("pkill firefox", "browser")
    safety.add_always_rule("pkill firefox", "browser")
    assert len(safety.list_rules("browser")) == 1


def test_store_file_mode_and_corruption(home):
    safety.add_always_rule("pkill firefox", "browser")
    path = safety._rules_path()
    assert (path.stat().st_mode & 0o777) == 0o600
    path.write_text("{not json")
    assert safety.list_rules("browser") == []
    path.write_text(json.dumps({"rules": ["junk", {"id": 1}]}))
    assert safety.list_rules("browser") == []


def test_rules_file_is_not_writable_by_the_model(home):
    path = safety._rules_path()
    assert safety.check_path(path, write=True).blocked           # file tools
    for command in [
        f"echo x > {path}", f"cp /tmp/x {path}", f"sed -i s/a/b/ {path}",
        f"tee {path}", "echo x > safety_rules.json", f"python3 -c 'open(\"{path}\",\"w\")'",
        f"mv /tmp/x {path}", f"rm {path}",
    ]:
        v = classify_command(command, cwd=home)
        assert v.blocked or v.needs_confirmation, command
    assert classify_command(f"echo x > {path}", cwd=home).blocked
    # reading it stays allowed
    assert classify_command(f"cat {path}", cwd=home).danger == Danger.SAFE


def test_no_skill_touches_the_rules():
    pattern = re.compile(
        r"add_always_rule|revoke_rule|_save_rules|_load_rules|_match_rule|"
        r"safety_rules|RULES_FILENAME|\.ALWAYS|always_offer|_current_principal"
    )
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "kravein" / "skills").rglob("*.py")
        if pattern.search(p.read_text())
    ]
    assert offenders == []


# -- the confirm frame / reply contract -------------------------------------

def _card(session, answer, *, offer):
    """Raise a card on a worker; return (frame, result)."""
    out: dict = {}

    def worker():
        safety._local.always_signature = "pkill firefox" if offer else ""
        try:
            out["r"] = session.confirm("run `pkill firefox`", "destructive", Danger.DESTRUCTIVE)
        finally:
            safety._local.always_signature = ""

    t = threading.Thread(target=worker)
    t.start()
    return t, out


async def _roundtrip(answer, offer):
    session = events.Session(asyncio.get_running_loop(), confirm_timeout=3)
    t, out = _card(session, answer, offer=offer)
    frame = await asyncio.wait_for(session.next_frame(), timeout=2)
    assert frame["t"] == "confirm_request"
    ok = session.resolve_confirm(frame["confirm_id"], answer)
    resolved = await asyncio.wait_for(session.next_frame(), timeout=2)
    t.join(timeout=2)
    return frame, ok, resolved, out["r"]


@pytest.mark.asyncio
async def test_frame_carries_the_always_offer():
    frame, *_ = await _roundtrip("approve", offer=True)
    assert frame["always"] == {"allowed": True, "signature": "pkill firefox"}
    frame, *_ = await _roundtrip("approve", offer=False)
    assert frame["always"] == {"allowed": False, "signature": ""}


@pytest.mark.asyncio
async def test_decision_always_returns_the_always_marker():
    _, ok, resolved, result = await _roundtrip("always", offer=True)
    assert ok and result == safety.ALWAYS
    assert resolved["t"] == "confirm_resolved"
    assert resolved["outcome"] == "approved" and resolved["decision"] == "always"


@pytest.mark.asyncio
async def test_decision_always_on_a_card_without_the_offer_is_plain_approve():
    _, ok, resolved, result = await _roundtrip("always", offer=False)
    assert ok and result is True and resolved["decision"] == "approve"


@pytest.mark.asyncio
async def test_decision_approve_and_legacy_bool():
    _, _, resolved, result = await _roundtrip("approve", offer=True)
    assert result is True and resolved["decision"] == "approve"
    _, _, resolved, result = await _roundtrip(True, offer=True)
    assert result is True and resolved["decision"] == "approve"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["deny", False, "nonsense", "APPROVE", "", None])
async def test_deny_and_anything_unknown_denies(answer):
    _, _, resolved, result = await _roundtrip(answer, offer=True)
    assert result is False
    assert resolved["outcome"] == "denied" and resolved["decision"] == "deny"


@pytest.mark.asyncio
async def test_timeout_is_a_denial_with_decision_expired():
    session = events.Session(asyncio.get_running_loop(), confirm_timeout=0.2)
    t, out = _card(session, None, offer=True)
    await asyncio.wait_for(session.next_frame(), timeout=2)
    resolved = await asyncio.wait_for(session.next_frame(), timeout=2)
    t.join(timeout=2)
    assert out["r"] is False
    assert resolved["outcome"] == "expired" and resolved["decision"] == "expired"


@pytest.mark.asyncio
async def test_phone_path_cannot_choose_always():
    session = events.Session(asyncio.get_running_loop(), confirm_timeout=3)
    t, out = _card(session, None, offer=True)
    frame = await asyncio.wait_for(session.next_frame(), timeout=2)
    # resolve_open is the phone's entry point: a bool only
    assert events.resolve_open(frame["confirm_id"], True)
    t.join(timeout=2)
    assert out["r"] is True


def test_end_to_end_always_through_a_session_creates_the_rule(home):
    """Worker raises the card via enforce(); the 'owner' answers always."""
    loop = asyncio.new_event_loop()
    try:
        session = events.Session(loop, confirm_timeout=3)
        v = _verdict("pkill firefox", home)
        done = {}

        def worker():
            with safety.confirmer_scope(session.confirm, principal="browser"):
                safety.enforce(v, "run `pkill firefox`", skill="run_command", source="web")
            done["ok"] = True

        t = threading.Thread(target=worker)
        t.start()
        frame = loop.run_until_complete(asyncio.wait_for(session.next_frame(), 2))
        assert frame["always"]["signature"] == "pkill firefox"
        session.resolve_confirm(frame["confirm_id"], "always")
        t.join(timeout=3)
        assert done.get("ok")
        assert [r["signature"] for r in safety.list_rules("browser")] == ["pkill firefox"]
    finally:
        loop.close()


# -- REST ---------------------------------------------------------------------

TOKEN = "test-token-rules"
HEAD = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from kravein import webauth
    from kravein.server import create_app

    webauth.clear_lan_hosts()
    return TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787")


def test_rules_endpoints_need_a_token(client):
    assert client.get("/api/safety/rules").status_code == 401
    assert client.delete("/api/safety/rules/abc").status_code == 401


def test_rules_list_and_revoke(client):
    assert client.get("/api/safety/rules", headers=HEAD).json() == {"rules": []}
    rule = safety.add_always_rule("pkill firefox", "browser")
    safety.add_always_rule("pkill vlc", "device:someone-else")
    got = client.get("/api/safety/rules", headers=HEAD).json()["rules"]
    assert [r["signature"] for r in got] == ["pkill firefox"]       # own principal only
    assert {"id", "signature", "created_at", "uses", "last_used"} <= set(got[0])
    assert "principal" in got[0]

    other = safety.list_rules("device:someone-else")[0]["id"]
    assert client.delete(f"/api/safety/rules/{other}", headers=HEAD).status_code == 404
    assert client.delete("/api/safety/rules/nope", headers=HEAD).status_code == 404
    assert client.delete(f"/api/safety/rules/{rule['id']}", headers=HEAD).json() == {"ok": True}
    assert client.get("/api/safety/rules", headers=HEAD).json() == {"rules": []}
    assert safety.list_rules("device:someone-else")                  # untouched


def test_there_is_no_endpoint_to_create_a_rule(client):
    for method in ("post", "put", "patch"):
        got = getattr(client, method)("/api/safety/rules", headers=HEAD,
                                      json={"signature": "pkill x"})
        assert got.status_code in (404, 405)
    assert safety.list_rules("browser") == []


# -- review findings (self-review fork on #199) --------------------------------

@pytest.mark.parametrize("command", [
    "pkill nir", "pkill hypr", "pkill krav", "pkill pyth", "pkill syste", "pkill ssh",
    "pkill ni", "pkill 'a+'", "pkill 'e.'", "pkill sh", "pkill kravein-web", "pkill python3.14",
    "pkill uvicorn", "pkill xo", "pkill dbus",
])
def test_pkill_substring_and_regex_forms_get_no_signature(command, home):
    assert _verdict(command, home).signature == "", command


def test_a_plain_application_name_still_qualifies(home):
    for name in ("firefox", "vlc", "spotify", "chrome", "mpv"):
        assert _verdict(f"pkill {name}", home).signature == f"pkill {name}"


def test_failure_to_store_a_rule_does_not_undo_the_approval(home, monkeypatch, env):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(safety, "add_always_rule", boom)
    _ask(_verdict("pkill firefox", home), answer=safety.ALWAYS)    # does not raise
    assert any(e == "safety.rule_store_error" for e, _ in env["log"])


def test_task_confirmer_passes_always_through(home):
    from kravein.agents import tasks

    class Host:
        def confirm(self, principal, action, detail, danger, task_id):
            return safety.ALWAYS

        def attended(self, principal):
            return True

    class T:
        unattended = False
        principal = "browser"
        id = "t1"
        stop = threading.Event()

    old = tasks._host
    tasks._host = Host()
    try:
        confirm = tasks.manager._confirmer(T())
        assert confirm("a", "d", Danger.DESTRUCTIVE) == safety.ALWAYS
        T.stop.set()
        assert confirm("a", "d", Danger.DESTRUCTIVE) is False       # stop still wins
    finally:
        tasks._host = old
