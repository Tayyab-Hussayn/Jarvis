"""Tests: whatsapp.

Split out of the legacy tests/test_linux_port.py with no change to any test body.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import llm, registry, safety  # noqa: E402,F401
from kravein.safety import Danger  # noqa: E402,F401
from conftest import _at, _morning, _states, habits  # noqa: E402,F401


# Delivery escalation and the WhatsApp channel
# ---------------------------------------------------------------------------

def test_whatsapp_is_additive_never_the_only_channel(monkeypatch):
    """A reminder must not be lost because the optional half of it failed."""
    from kravein import delivery

    shown: list[str] = []
    monkeypatch.setattr(delivery, "send_notification",
                        lambda text, title, **kw: shown.append(text) or "")
    monkeypatch.setattr(delivery, "say", lambda text: "")
    monkeypatch.setattr(delivery, "send_whatsapp",
                        lambda text, ref="": "the WhatsApp service is not running")

    problems = delivery.deliver(
        "Take your meds", channels=(delivery.NOTIFY, delivery.SPEAK, delivery.WHATSAPP),
    )
    assert shown == ["Take your meds"]              # the floor still fired
    assert delivery.WHATSAPP in problems            # and the failure is reported
    assert delivery.NOTIFY not in problems


def test_a_failure_notice_is_not_escalated(monkeypatch):
    """One problem must not become three interruptions."""
    from kravein import delivery

    spoken: list[str] = []
    sent: list[str] = []
    monkeypatch.setattr(delivery, "send_notification", lambda *a, **k: "")
    monkeypatch.setattr(delivery, "say", lambda text: spoken.append(text) or "")
    monkeypatch.setattr(delivery, "send_whatsapp", lambda text, ref="": sent.append(text) or "")

    delivery.deliver("it broke", failed=True,
                     channels=(delivery.NOTIFY, delivery.SPEAK, delivery.WHATSAPP))
    assert spoken == [] and sent == []


def test_the_whatsapp_client_degrades_when_the_service_is_absent(tmp_path, monkeypatch):
    """A missing socket is the normal case, not an error."""
    from kravein.platform import whatsapp

    monkeypatch.setattr(whatsapp, "SOCKET", tmp_path / "nothing.sock")
    assert whatsapp.available() is False
    assert whatsapp.state()["state"] == "unavailable"

    sent, error = whatsapp.send("hello")
    assert sent is False and "not running" in error
    assert whatsapp.begin_link()["ok"] is False


def test_the_socket_client_never_loads_neonize(tmp_path):
    """The 8MB Go runtime lives behind the socket, not on the tick path."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c",
         "import sys\n"
         "from kravein.platform import whatsapp\n"
         "whatsapp.state(); whatsapp.send('x')\n"
         "print('NEONIZE:' + ('yes' if 'neonize' in sys.modules else 'no'))\n"],
        capture_output=True, text=True, timeout=60,
    ).stdout
    assert "NEONIZE:no" in out, "the socket client pulled in neonize"


def test_the_escalation_ladder_climbs_one_rung_at_a_time():
    from kravein import delivery

    assert delivery.escalation(1, escalate=False) == (delivery.NOTIFY,)
    assert delivery.escalation(9, escalate=False) == (delivery.NOTIFY,)
    assert delivery.escalation(1, escalate=True) == (delivery.NOTIFY,)
    assert delivery.escalation(2, escalate=True) == (delivery.NOTIFY, delivery.SPEAK)
    assert delivery.escalation(3, escalate=True) == (
        delivery.NOTIFY, delivery.SPEAK, delivery.WHATSAPP)
    # `insist` starts one rung up, and never gains a rung that blocks anything.
    assert delivery.escalation(1, escalate=True, insist=True) == (
        delivery.NOTIFY, delivery.SPEAK)


def test_the_pairing_code_encodes_to_a_square_grid():
    """The panel draws what this returns; an empty grid renders nothing."""
    from kravein.platform import whatsapp

    rows = whatsapp.qr_modules("2@abcdef==,ghijkl==,mnopqr==")
    assert rows and len(rows) == len(rows[0])
    assert set("".join(rows)) <= {"0", "1"}
    assert whatsapp.qr_modules("") == []


# ---------------------------------------------------------------------------


# The pairing QR must be dark-on-light, whatever the terminal theme
# ---------------------------------------------------------------------------

def _decode_rendered_qr(lines: list[str]) -> list[list[bool]]:
    """Read the rendered half-block rows back into a module matrix.

    Inverts exactly what the renderer did, so a polarity mistake shows up as a
    mismatch rather than as a QR nobody can scan.
    """
    rows: list[list[bool]] = []
    for line in lines:
        body = line.replace("\033[30;47m", "").replace("\033[0m", "")
        top, bottom = [], []
        for cell in body:
            top.append(cell in "█▀")
            bottom.append(cell in "█▄")
        rows.append(top)
        rows.append(bottom)
    return rows


def test_the_pairing_qr_is_rendered_dark_on_light():
    """WhatsApp will not read an inverted code, and a dark terminal inverts it.

    The first renderer printed `█` for a dark module and left the colours to
    the theme, so on a dark terminal the ink and the paper swapped and no phone
    would scan it. It looked right on a light terminal — the worst way for this
    to fail — so the polarity is asserted here rather than eyeballed.
    """
    from kravein import cli

    code = "https://wa.me/settings/linked_devices#2@abc,def,ghi,jkl,5"
    matrix = cli.qr_matrix(code)
    lines = cli.render_qr_lines(matrix)

    # Every row carries an explicit black-on-white pair; nothing is inherited.
    assert all(line.startswith("\033[30;47m") and line.endswith("\033[0m")
               for line in lines)

    decoded = _decode_rendered_qr(lines)
    for y, row in enumerate(matrix):
        assert decoded[y] == row, f"row {y} does not match the encoder"

    # The quiet zone is four modules of *light*, as the spec requires. In this
    # rendering light means background, i.e. a space.
    assert all(cell is False for cell in matrix[0])
    assert all(not row[0] for row in matrix)
    body = lines[0].replace("\033[30;47m", "").replace("\033[0m", "")
    assert body.strip() == "", "the top quiet zone is not blank"


def test_the_qr_quiet_zone_is_four_modules():
    from kravein import cli

    matrix = cli.qr_matrix("https://wa.me/settings/linked_devices#2@a,b,c,d,5")
    # Count blank leading rows: must be at least the spec's four.
    blank = 0
    for row in matrix:
        if any(row):
            break
        blank += 1
    assert blank >= 4, f"quiet zone is only {blank} modules"


# ---------------------------------------------------------------------------


# WhatsApp inbound: allowlist, vocabulary, dispatch
# ---------------------------------------------------------------------------

OWNER = "923001234567"


@pytest.fixture
def inbound(monkeypatch):
    """The inbound handler with a known owner and no real config on disk."""
    from kravein import config as config_mod
    from kravein import whatsapp_service as svc

    settings = {
        "whatsapp.owner_jid": OWNER,
        "whatsapp.inbound_max_age_seconds": 300,
        "whatsapp.inbound_rate_limit": 20,
    }
    monkeypatch.setattr(config_mod, "get",
                        lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(svc.config, "get",
                        lambda key, default=None: settings.get(key, default))
    return svc


def _msg(inbound, sender=OWNER, text="done", *, from_me=False, group=False, age=0.0):
    now = 1_000_000.0
    return inbound.handle_inbound(
        sender=sender, text=text, is_from_me=from_me, is_group=group,
        timestamp=now - age, now=now,
    )


# --- the allowlist ---------------------------------------------------------

def test_a_stranger_gets_nothing_at_all(inbound):
    """No action and, critically, no reply — a reply confirms someone is here."""
    assert _msg(inbound, sender="447700900000", text="done") == ("", "")
    assert _msg(inbound, sender="447700900000", text="status") == ("", "")
    assert _msg(inbound, sender="", text="done") == ("", "")


def test_the_owner_is_matched_on_digits_not_formatting(inbound):
    """Device suffixes and punctuation must not smuggle a near-match through."""
    assert _msg(inbound, sender=OWNER)[0] == "done"
    assert _msg(inbound, sender=f"+{OWNER}")[0] == "done"
    # A different number that merely contains the owner's is not the owner.
    assert _msg(inbound, sender=OWNER + "9") == ("", "")
    assert _msg(inbound, sender="9" + OWNER) == ("", "")


def test_inbound_is_off_until_an_owner_is_configured(inbound, monkeypatch):
    """A channel nobody set up must not be open."""
    monkeypatch.setattr(inbound.config, "get", lambda key, default=None: "")
    assert _msg(inbound, sender=OWNER, text="done") == ("", "")


def test_our_own_replies_cannot_feed_themselves(inbound):
    """A confirmation saying "Done: Breakfast" comes back through this handler."""
    assert _msg(inbound, from_me=True, text="done") == ("", "")


def test_a_group_message_is_refused_even_from_the_owner(inbound):
    """Anyone can add a number to a group; being in one is not authorisation."""
    assert _msg(inbound, group=True, text="done") == ("", "")


def test_a_replayed_message_cannot_complete_a_step(inbound):
    """Reconnecting replays undelivered messages — a morning "done" at teatime."""
    assert _msg(inbound, text="done", age=60)[0] == "done"        # fresh enough
    assert _msg(inbound, text="done", age=6 * 3600) == ("", "")   # this morning


# --- the vocabulary --------------------------------------------------------

def test_the_vocabulary_is_exactly_what_was_specified(inbound):
    assert inbound.parse_command("done") == ("done", "")
    assert inbound.parse_command("done s2") == ("done", "s2")
    assert inbound.parse_command("skip") == ("skip", "")
    assert inbound.parse_command("skip s3") == ("skip", "s3")
    assert inbound.parse_command("status") == ("status", "")
    assert inbound.parse_command("pause") == ("pause", "")
    assert inbound.parse_command("resume") == ("resume", "")
    # Phone keyboards capitalise; that is forgiven, nothing else is.
    assert inbound.parse_command("  DONE  ") == ("done", "")


def test_anything_outside_the_vocabulary_is_dropped(inbound):
    for text in (
        "",
        "hello",
        "please mark breakfast done",
        "done please",              # the argument must be a step id
        "done breakfast",           # not even a real label
        "status now",               # status takes no argument
        "pause everything",
        "resume all",
        "resumed",
        "done; rm -rf /",
        "done && curl evil.example.com",
        "done\nstatus",
        "undone",
        "d0ne",
        "x" * 200,                  # long enough to be an injection attempt
        "done ../../etc/passwd",    # path-ish argument
        "done $(whoami)",
    ):
        assert inbound.parse_command(text) is None, f"accepted {text!r}"


def test_an_unrecognised_message_from_the_owner_is_still_silent(inbound):
    """Even the owner gets no error text — the reply path is for commands only."""
    assert _msg(inbound, text="what's my score?") == ("", "")


def test_dropped_messages_are_never_logged_with_their_contents(inbound, monkeypatch):
    """The audit trail must not become a log of other people's messages."""
    from kravein import audit as audit_mod

    records: list[dict] = []
    monkeypatch.setattr(audit_mod, "record",
                        lambda event, **fields: records.append({"event": event, **fields}))
    monkeypatch.setattr(inbound.audit, "record",
                        lambda event, **fields: records.append({"event": event, **fields}))

    secret = "this is a private message nobody should keep"
    _msg(inbound, sender="447700900000", text=secret)
    _msg(inbound, sender=OWNER, text=secret)

    assert records, "nothing was recorded at all"
    blob = json.dumps(records)
    assert secret not in blob
    assert "447700900000" not in blob
    assert all(r["event"].startswith("whatsapp.inbound") for r in records)


# --- dispatch into the state machine ---------------------------------------

def test_a_whatsapp_done_confirms_the_open_step(habits, inbound, monkeypatch):
    from kravein import whatsapp_service as svc

    monkeypatch.setattr(svc, "_notify_ui", lambda *a, **k: None)
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))                       # s1 is now nagging

    reply = svc.run_command("done", "", moment=_at(6, 7, 5))
    assert "Wake up" in reply
    assert _states(habits, rid)["s1"] == "done"
    assert habits.load()["today"][rid]["steps"]["s1"]["source"] == "whatsapp"


def test_a_bare_done_with_two_steps_open_refuses_and_lists_them(habits, inbound, monkeypatch):
    """Never resolve an ambiguous target by taking the first match."""
    from kravein import whatsapp_service as svc

    monkeypatch.setattr(svc, "_notify_ui", lambda *a, **k: None)
    routine = habits.add_routine("Two", tz_name="UTC")
    habits.add_step(routine["id"], label="Alpha", at="07:00", window_minutes=180)
    habits.add_step(routine["id"], label="Beta", at="07:30", window_minutes=180)
    rid = routine["id"]
    habits.tick(_at(6, 7, 45))

    reply = svc.run_command("done", "", moment=_at(6, 7, 46))
    assert "Which one?" in reply and "Alpha" in reply and "Beta" in reply
    assert set(_states(habits, rid).values()) == {"nagging"}   # nothing was touched

    assert "Done: Beta" in svc.run_command("done", "s2", moment=_at(6, 7, 47))
    assert _states(habits, rid)["s2"] == "done"


def test_done_cannot_reach_a_locked_step(habits, inbound, monkeypatch):
    """The sequencing holds over WhatsApp exactly as it does everywhere else."""
    from kravein import whatsapp_service as svc

    monkeypatch.setattr(svc, "_notify_ui", lambda *a, **k: None)
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))                       # s1 open, s2 locked behind it

    reply = svc.run_command("done", "s2", moment=_at(6, 7, 5))
    assert "s2" in reply or "Nothing open" in reply
    assert _states(habits, rid)["s2"] == "locked"


def test_status_and_pause_report_without_touching_the_model(habits, inbound):
    from kravein import whatsapp_service as svc

    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))

    status = svc.run_command("status", "", moment=_at(6, 7, 5))
    assert "Morning" in status and "Waiting" in status

    paused = svc.run_command("pause", "", moment=_at(6, 7, 5))
    assert "Morning" in paused
    assert habits.load()["routines"][0]["enabled"] is False


def test_pause_can_be_undone_from_the_same_channel(habits, inbound):
    """A control that only goes one way strands you away from the machine."""
    from kravein import whatsapp_service as svc

    _morning(habits)
    habits.tick(_at(6, 7, 0))

    assert "Morning" in svc.run_command("pause", "", moment=_at(6, 7, 5))
    assert habits.load()["routines"][0]["enabled"] is False
    assert "already paused" in svc.run_command("pause", "", moment=_at(6, 7, 6))

    assert "Resumed" in svc.run_command("resume", "", moment=_at(6, 7, 7))
    assert habits.load()["routines"][0]["enabled"] is True
    assert "Nothing was paused" in svc.run_command("resume", "", moment=_at(6, 7, 8))


def test_the_inbound_path_never_imports_the_agent_or_the_llm():
    """Untrusted text must not be able to reach a model, structurally."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c",
         "import sys\n"
         "from kravein import whatsapp_service as svc\n"
         "svc.parse_command('done s2')\n"
         "svc.handle_inbound(sender='1', text='done', is_from_me=False,\n"
         "                   is_group=False, timestamp=0, now=0, allow='1')\n"
         "bad = sorted(m for m in sys.modules if m.startswith(("
         "'kravein.agent','kravein.llm','kravein.registry','neonize')))\n"
         "print('BAD:' + ','.join(bad))\n"],
        capture_output=True, text=True, timeout=60,
    ).stdout
    assert "BAD:" in out
    assert out.split("BAD:")[1].strip() == "", "inbound reached the model stack"


# ---------------------------------------------------------------------------
