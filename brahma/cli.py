"""Interactive terminal assistant and command-line entry point.

This is the primary front-end. It supplies the confirmation prompt that the
safety layer calls into, which is why destructive actions can only be
approved from a real terminal.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import audit, config, registry
from .agent import Agent
from .llm import client
from .platform import detect
from .safety import DANGER_LABELS, Danger

# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(t: str) -> str:
    return _c(t, "2")


def bold(t: str) -> str:
    return _c(t, "1")


def cyan(t: str) -> str:
    return _c(t, "36")


def yellow(t: str) -> str:
    return _c(t, "33")


def red(t: str) -> str:
    return _c(t, "31")


def green(t: str) -> str:
    return _c(t, "32")


DANGER_COLOR = {
    Danger.SAFE: green,
    Danger.MODERATE: cyan,
    Danger.DESTRUCTIVE: yellow,
    Danger.CRITICAL: red,
}


def terminal_confirmer(action: str, detail: str, danger: Danger) -> bool:
    """Ask the user to approve a destructive action.

    This is the only place a destructive action can be approved interactively.
    """
    colorise = DANGER_COLOR.get(danger, yellow)
    label = DANGER_LABELS[danger].upper()

    print()
    print(colorise(f"  ┌─ CONFIRM [{label}]"))
    print(colorise(f"  │  {action}"))
    if detail:
        print(colorise(f"  │  {detail}"))
    print(colorise("  └─"))

    prompt = colorise("  Proceed? [y/N] ")
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    approved = answer in ("y", "yes")
    print(dim("  → approved" if approved else "  → cancelled"))
    print()
    return approved


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

BANNER = r"""
  ██████  ██████   █████  ██   ██ ███    ███  █████
  ██   ██ ██   ██ ██   ██ ██   ██ ████  ████ ██   ██
  ██████  ██████  ███████ ███████ ██ ████ ██ ███████
  ██   ██ ██   ██ ██   ██ ██   ██ ██  ██  ██ ██   ██
  ██████  ██   ██ ██   ██ ██   ██ ██      ██ ██   ██   echo
"""

HELP = """
Commands
  /voice                start hands-free voice mode (Ctrl-C to leave)
  /voice live           use the Gemini Live pipeline instead (needs a key)
  /voice status         check the microphone, recogniser and speech setup
  /voice test           record you once and prove the whole chain works
  /say <text>           speak something out loud
  /help                 show this
  /skills [category]    list available skills
  /skill <name>         show one skill's parameters
  /run <name> [k=v …]   run a skill directly, bypassing the model
  /env                  environment and capability report
  /audit [n]            recent audit entries
  /reset                clear the conversation history
  /quit                 exit

Anything else is sent to the assistant as text.
"""


class Repl:
    """The interactive loop."""

    def __init__(self) -> None:
        self.agent = Agent(source="cli", confirmer=terminal_confirmer,
                           observer=self._observe)
        self._last_kind = ""

    def _observe(self, kind: str, text: str) -> None:
        if kind == "tool":
            print(dim(f"  · {text}"))
        elif kind == "thinking" and text.strip():
            print(dim(f"  {text.strip()}"))
        elif kind == "error":
            print(red(f"  ! {text}"))

    # -- slash commands --------------------------------------------------

    def _cmd_skills(self, arg: str) -> None:
        groups = registry.categories()
        if arg:
            groups = {k: v for k, v in groups.items() if k.startswith(arg.lower())}
            if not groups:
                print(f"No category matching '{arg}'. Categories: "
                      + ", ".join(registry.categories()))
                return

        total = 0
        for category, skills in groups.items():
            print(bold(f"\n{category}"))
            for entry in skills:
                ok, why = entry.available()
                colorise = DANGER_COLOR.get(entry.danger, cyan)
                marker = colorise("●")
                suffix = dim(f"  (unavailable: {why})") if not ok else ""
                print(f"  {marker} {entry.name:<26} {entry.description[:70]}{suffix}")
                total += 1
        print(dim(f"\n{total} skill(s). ● colour = risk level."))

    def _cmd_skill(self, name: str) -> None:
        entry = registry.get_skill(name)
        if entry is None:
            print(f"No skill named '{name}'.")
            return

        ok, why = entry.available()
        print(bold(f"\n{entry.name}"))
        print(f"  {entry.description}")
        print(f"  category : {entry.category}")
        print(f"  risk     : {DANGER_LABELS[entry.danger]}")
        print(f"  available: {'yes' if ok else 'no — ' + why}")

        props = entry.parameters.get("properties", {})
        required = set(entry.parameters.get("required", []))
        if props:
            print("  parameters:")
            for pname, schema in props.items():
                flag = "required" if pname in required else "optional"
                desc = schema.get("description", "")
                print(f"    {pname} ({schema.get('type','string')}, {flag}) {desc}")
        else:
            print("  parameters: none")
        print()

    def _cmd_run(self, arg: str) -> None:
        parts = arg.split()
        if not parts:
            print("Usage: /run <skill> [key=value …]")
            return

        name, rest = parts[0], parts[1:]
        params: dict[str, Any] = {}
        for token in rest:
            if "=" not in token:
                print(f"Ignoring '{token}' — arguments must be key=value.")
                continue
            key, _, value = token.partition("=")
            params[key] = value

        from .safety import confirmer_scope

        with confirmer_scope(terminal_confirmer):
            result = registry.dispatch(name, params, source="cli-direct")
        print(result)

    def _cmd_voice(self, arg: str) -> None:
        """Hand the microphone to the assistant until the user stops it."""
        from . import voice

        mode = (arg or "").strip().lower()

        if mode == "status":
            print(voice.status())
            return

        if mode == "test":
            print(voice.self_test(observer=self._voice_observer))
            return

        if mode == "live":
            ok, why = voice.live_available()
            if not ok:
                print(yellow(why))
                return
            print(dim("  Gemini Live — speak freely. Ctrl-C to leave.\n"))
            voice.run_live(observer=self._voice_observer)
            print(dim("\n  Left voice mode.\n"))
            return

        ready, problems = voice.readiness()
        if not ready:
            print(red("  Voice mode is not ready:"))
            for problem in problems:
                for line in problem.splitlines():
                    print(yellow(f"    {line}"))
            return

        # Share the REPL's conversation so voice and text are one thread.
        session = voice.VoiceSession(
            agent=self.agent,
            observer=self._voice_observer,
        )

        print(dim("  Voice mode. Speak when you see ● listening."))
        print(dim("  Say 'stop listening' or press Ctrl-C to leave.\n"))

        try:
            session.run(greeting="Voice mode on. I'm listening.")
        except KeyboardInterrupt:
            session.stop()
        finally:
            from .platform import tts

            tts.stop()
        print(dim("\n  Left voice mode.\n"))

    def _voice_observer(self, kind: str, text: str) -> None:
        if kind == "listening":
            print(green("  ● listening…"))
        elif kind == "heard":
            print(bold(cyan(f"  ‹ {text}")))
        elif kind == "tool":
            print(dim(f"  · {text}"))
        elif kind == "answer":
            print(f"\n{text}\n")
        elif kind == "speaking":
            pass  # already printed as the answer
        elif kind == "error":
            print(red(f"  ! {text}"))
        elif kind == "info":
            print(dim(f"  {text}"))

    def _cmd_say(self, arg: str) -> None:
        from .platform import tts

        if not arg.strip():
            print("Usage: /say <text>")
            return
        print(dim(f"  {tts.speak(arg)}"))

    def _cmd_audit(self, arg: str) -> None:
        try:
            limit = int(arg) if arg.strip() else 20
        except ValueError:
            limit = 20
        print(registry.dispatch("read_audit_log", {"limit": limit}, source="cli-direct"))

    def _handle_command(self, line: str) -> bool:
        """Return False to exit the REPL."""
        command, _, arg = line[1:].partition(" ")
        command = command.lower()
        arg = arg.strip()

        if command in ("quit", "exit", "q"):
            return False
        if command in ("help", "h", "?"):
            print(HELP)
        elif command == "skills":
            self._cmd_skills(arg)
        elif command == "skill":
            self._cmd_skill(arg)
        elif command == "run":
            self._cmd_run(arg)
        elif command in ("voice", "v", "listen"):
            self._cmd_voice(arg)
        elif command == "say":
            self._cmd_say(arg)
        elif command == "env":
            print(registry.dispatch("environment_report", source="cli-direct"))
        elif command == "audit":
            self._cmd_audit(arg)
        elif command == "reset":
            self.agent.reset()
            print(dim("Conversation cleared."))
        else:
            print(f"Unknown command '/{command}'. Try /help.")
        return True

    # -- loop ------------------------------------------------------------

    def run(self) -> int:
        config.ensure_dirs()
        env = detect.environment()

        print(cyan(BANNER))
        print(f"  {env.summary()}")
        print(f"  {len(registry.all_skills())} skills · {client().status()}")

        missing = detect.missing_report()
        if missing:
            print(yellow(f"  {len(missing)} capability(ies) degraded — /env for details"))
        print(dim("  /help for commands, /quit to exit\n"))

        audit.record("cli.start", skills=len(registry.all_skills()))

        try:
            import readline  # noqa: F401  (enables history and line editing)
        except ImportError:
            pass

        while True:
            try:
                line = input(bold(cyan("› "))).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue

            if line.startswith("/"):
                if not self._handle_command(line):
                    break
                continue

            try:
                turn = self.agent.ask(line)
            except KeyboardInterrupt:
                print(dim("\n  interrupted"))
                continue

            if turn.reply:
                print(f"\n{turn.reply}\n")
            elif turn.error:
                print(red(f"\n{turn.error}\n"))

        audit.record("cli.stop")
        print(dim("Goodbye."))
        return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brahma",
        description="Brahma Echo — Linux system assistant.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("repl", help="interactive assistant (default)")

    ask = sub.add_parser("ask", help="run one request and exit")
    ask.add_argument("text", nargs="+", help="what to ask")
    ask.add_argument("--yes", action="store_true",
                     help="auto-approve destructive actions (dangerous)")

    run = sub.add_parser("run", help="run one skill directly")
    run.add_argument("skill", help="skill name")
    run.add_argument("params", nargs="*", help="key=value arguments")
    run.add_argument("--yes", action="store_true", help="auto-approve")

    skills = sub.add_parser("skills", help="list skills")
    skills.add_argument("category", nargs="?", default="")
    skills.add_argument("--json", action="store_true", help="machine-readable output")

    voice_cmd = sub.add_parser("voice", help="hands-free voice conversation")
    voice_cmd.add_argument(
        "mode", nargs="?", default="local",
        choices=["local", "live", "status", "test"],
        help="local (default), live (Gemini Live), status, or test",
    )
    voice_cmd.add_argument("--no-speak", action="store_true",
                           help="transcribe and act, but stay silent")

    sub.add_parser("env", help="environment and capability report")
    sub.add_parser("doctor", help="check setup and report problems")

    audit_cmd = sub.add_parser("audit", help="show the audit trail")
    audit_cmd.add_argument("-n", "--limit", type=int, default=30)
    audit_cmd.add_argument("-e", "--event", default="", help="filter by event prefix")

    daemon = sub.add_parser("daemon", help="run as a background service")
    daemon.add_argument("--socket", default="", help="override the socket path")

    send = sub.add_parser("send", help="send a request to a running daemon")
    send.add_argument("text", nargs="+")
    send.add_argument("--socket", default="")

    serve = sub.add_parser("serve", help="run the web UI on localhost")
    serve.add_argument("--host", default="127.0.0.1",
                       help="loopback only; remote access is not supported yet")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--no-open", action="store_true",
                       help="do not open a browser tab")

    return parser


def _auto_yes(action: str, detail: str, danger: Danger) -> bool:
    print(yellow(f"  [auto-approved: {action}]"))
    return True


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config.ensure_dirs()

    command = args.command or "repl"

    if command == "repl":
        return Repl().run()

    if command == "ask":
        from .agent import Agent

        confirmer = _auto_yes if args.yes else terminal_confirmer
        agent = Agent(source="cli-ask", confirmer=confirmer)
        turn = agent.ask(" ".join(args.text))
        if turn.reply:
            print(turn.reply)
        if turn.error:
            print(red(turn.error), file=sys.stderr)
            return 1
        return 0

    if command == "run":
        from .safety import confirmer_scope

        params: dict[str, Any] = {}
        for token in args.params:
            key, _, value = token.partition("=")
            if key:
                params[key] = value

        confirmer = _auto_yes if args.yes else terminal_confirmer
        with confirmer_scope(confirmer):
            print(registry.dispatch(args.skill, params, source="cli-run"))
        return 0

    if command == "skills":
        if args.json:
            print(json.dumps(registry.tool_declarations(include_unavailable=True), indent=2))
        else:
            Repl()._cmd_skills(args.category)
        return 0

    if command == "voice":
        from . import voice

        if args.mode == "status":
            print(voice.status())
            return 0

        if args.mode == "test":
            print(voice.self_test(observer=Repl()._voice_observer))
            return 0

        repl = Repl()
        if args.mode == "live":
            ok, why = voice.live_available()
            if not ok:
                print(yellow(why), file=sys.stderr)
                return 1
            print(dim("Gemini Live — speak freely. Ctrl-C to stop.\n"))
            voice.run_live(observer=repl._voice_observer)
            return 0

        ready, problems = voice.readiness()
        if not ready:
            for problem in problems:
                print(red(problem), file=sys.stderr)
            return 1

        settings = voice.VoiceConfig.from_settings()
        if args.no_speak:
            settings.speak_replies = False

        session = voice.VoiceSession(
            agent=repl.agent, settings=settings, observer=repl._voice_observer
        )
        print(dim("Voice mode. Say 'stop listening' or press Ctrl-C to stop.\n"))
        try:
            session.run(greeting="Voice mode on. I'm listening.")
        except KeyboardInterrupt:
            session.stop()
        return 0

    if command == "env":
        print(registry.dispatch("environment_report", source="cli"))
        return 0

    if command == "doctor":
        return doctor()

    if command == "audit":
        entries = audit.tail(limit=args.limit, event_prefix=args.event)
        if not entries:
            print(f"No audit entries. Log: {config.AUDIT_LOG}")
            return 0
        for entry in entries:
            print(json.dumps(entry, ensure_ascii=False))
        return 0

    if command == "daemon":
        from .daemon import serve

        return serve(socket_path=args.socket or None)

    if command == "send":
        from .daemon import send_request

        reply = send_request(" ".join(args.text), socket_path=args.socket or None)
        print(reply)
        return 0

    if command == "serve":
        from .server import serve as serve_web

        return serve_web(
            host=args.host, port=args.port, open_browser=not args.no_open
        )

    parser.print_help()
    return 1


def doctor() -> int:
    """Check the install and report anything that needs fixing."""
    problems = 0
    env = detect.environment()

    print(bold("Environment"))
    print(f"  {env.summary()}")

    print(bold("\nLLM"))
    status = client().status()
    if "No LLM provider" in status:
        print(red(f"  {status}"))
        problems += 1
    else:
        print(green(f"  {status}"))

    from . import cline as cline_provider
    from .skills.llm_admin import _gemini_report

    for block in (_gemini_report(), cline_provider.doctor()):
        for line in block.splitlines():
            colour = red if ("NOT FOUND" in line or "NOT SET" in line) else dim
            print(colour(f"  {line}"))

    print(bold("\nSkills"))
    skills = registry.all_skills()
    unavailable = []
    for entry in skills.values():
        ok, why = entry.available()
        if not ok:
            unavailable.append((entry.name, why))
    print(f"  {len(skills)} registered, {len(skills) - len(unavailable)} available")
    for name, why in unavailable:
        print(yellow(f"    {name}: {why}"))

    print(bold("\nCapabilities"))
    missing = detect.missing_report()
    if not missing:
        print(green("  All capabilities present."))
    else:
        problems += len(missing)
        for capability, tool, package in missing:
            print(yellow(f"  {capability:<18} needs {tool:<22} → sudo pacman -S {package}"))

    print(bold("\nWeb UI"))
    try:
        from .server import WEB_DIST

        if WEB_DIST.is_dir() and (WEB_DIST / "index.html").exists():
            print(green(f"  Frontend built  {WEB_DIST}"))
        else:
            print(yellow("  Frontend not built — run: cd web && npm install && npm run build"))
            print(dim("  (`brahma serve` still works; it serves the API only.)"))
    except ImportError:
        print(yellow("  fastapi/uvicorn missing — `brahma serve` will not start."))
        problems += 1

    print(bold("\nPaths"))
    for label, path in (
        ("config", config.CONFIG_DIR),
        ("data", config.DATA_DIR),
        ("state", config.STATE_DIR),
        ("audit log", config.AUDIT_LOG),
    ):
        exists = "ok" if path.exists() else "missing"
        print(f"  {label:<10} {path}  [{exists}]")

    print()
    if problems:
        print(yellow(f"{problems} issue(s) to look at."))
    else:
        print(green("Everything checks out."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
