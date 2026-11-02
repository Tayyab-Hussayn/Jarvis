"""Find the executable a name really means, and prove a launch ran.

Two failures this exists for (owner, niri laptop, 2026-11-02):

* "Open antigravity CLI in my terminal": the agent typed a guessed command
  (`antigravity cli`), the shell said "unknown command", and the agent reported
  success. The real executable sat in ~/.local/bin under another name. So a name
  is resolved to an exact executable *before* anything is typed, and when it
  cannot be resolved exactly the caller gets candidates, never a guess.
* Nothing checked the effect afterwards. Typing into a terminal is
  fire-and-forget, so the only evidence available is the process tree: did a
  process with that executable appear under the terminal?

The learned registry (``appreg``) is consulted first and written only after a
verified launch.
"""

from __future__ import annotations

import difflib
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import appreg, desktop

# Words people add after a program name that the executable does not carry.
_FILLER = ("cli", "command", "tool", "app", "application", "program", "terminal")

# Shell builtins/keywords: they are not executables, and flagging them would
# refuse `cd /tmp && ls`.
_BUILTINS = frozenset("""
. : [ [[ (( { } ! alias bg bind break builtin caller case cd command compgen complete
continue declare dirs disown do done echo elif else enable esac eval exec exit export
false fc fg fi for function getopts hash history if in jobs kill let local logout popd
printf pushd pwd read readonly return select set shift shopt source suspend test then
time times trap true type typeset ulimit umask unalias unset until wait while
""".split())

# Prefix commands whose next word is the real program.
_PREFIXES = frozenset({"sudo", "env", "exec", "nohup", "nice", "command", "builtin", "time",
                       "setsid", "stdbuf", "timeout"})


@dataclass
class Resolution:
    """Outcome of ``resolve_command``."""

    path: str = ""                      # the exact executable; "" if none/ambiguous
    candidates: list[str] = field(default_factory=list)   # display strings
    source: str = ""                    # registry | path | desktop

    @property
    def ok(self) -> bool:
        return bool(self.path)

    def explain(self, query: str) -> str:
        if self.ok:
            return f"'{query}' is {self.path}."
        if self.candidates:
            return (f"No executable is called exactly '{query}'. Closest: "
                    f"{', '.join(self.candidates)}. Say which one - I will not guess.")
        return (f"No executable matching '{query}' was found on PATH, in ~/.local/bin, "
                "/opt/*/bin, the desktop entries or the learned registry.")


def search_dirs() -> list[str]:
    """Where executables live: PATH first (what the shell would use), then the
    places a desktop session's PATH often lacks but the owner's login shell has."""
    dirs: list[str] = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    home = Path.home()
    extra = [str(home / ".local" / "bin"), str(home / "bin")]
    try:
        extra += sorted(str(p / "bin") for p in Path("/opt").iterdir() if p.is_dir())
    except OSError:
        pass
    seen: set[str] = set()
    out = []
    for d in dirs + extra:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _executables() -> dict[str, str]:
    """name -> absolute path. First directory wins, like the shell."""
    found: dict[str, str] = {}
    for d in search_dirs():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            full = os.path.join(d, name)
            if name not in found and os.path.isfile(full) and os.access(full, os.X_OK):
                found[name] = full
    return found


def _exec_of_entry(entry) -> str:
    """The absolute executable a desktop entry starts, or ''."""
    cmd = entry.command()
    if not cmd:
        return ""
    first = cmd[0]
    # `env FOO=1 prog` and flatpak-style wrappers: the Exec's first word is
    # not the program, so only trust a plain executable.
    if "/" in first:
        return first if os.path.isfile(first) and os.access(first, os.X_OK) else ""
    for d in search_dirs():
        full = os.path.join(d, first)
        if os.path.isfile(full) and os.access(full, os.X_OK):
            return full
    return ""


def _exact(nq: str, exes: dict[str, str], entries) -> Resolution | None:
    """Exact normalised hit on an executable name, then on a desktop entry."""
    hits = {p for n, p in exes.items() if appreg.norm(n) == nq}
    if len(hits) == 1:
        return Resolution(path=hits.pop(), source="path")
    if len(hits) > 1:
        return Resolution(candidates=sorted(hits))
    via_desktop = {
        _exec_of_entry(e) for e in entries
        if nq in (appreg.norm(e.name), appreg.norm(e.id.removesuffix(".desktop")))
    } - {""}
    if len(via_desktop) == 1:
        return Resolution(path=via_desktop.pop(), source="desktop")
    if len(via_desktop) > 1:
        return Resolution(candidates=sorted(via_desktop))
    return None


def resolve_command(name: str) -> Resolution:
    """Resolve a spoken or typed program name to one exact executable.

    Order: learned registry, exact normalised executable name (PATH,
    ~/.local/bin, /opt/*/bin), exact desktop entry name -> its Exec program,
    then the same with filler words ("cli", "app") dropped. Anything fuzzier
    is returned as *candidates only*: ``path`` stays empty.
    """
    from . import apps

    text = (name or "").strip()
    if not text:
        return Resolution()
    if "/" in text or text.startswith("~"):
        p = os.path.expanduser(text)
        ok = os.path.isfile(p) and os.access(p, os.X_OK)
        return Resolution(path=p, source="path") if ok else Resolution()

    nq = appreg.norm(text)
    rec = appreg.lookup(text)
    if rec and rec.get("kind") == "cli":
        return Resolution(path=rec["id"], source="registry")
    if rec and rec.get("kind") == "desktop":
        entry = next((e for e in apps.index() if e.id == rec["id"]), None)
        exe = _exec_of_entry(entry) if entry else ""
        if exe:
            return Resolution(path=exe, source="registry")

    exes = _executables()
    entries = apps.index()
    hit = _exact(nq, exes, entries)
    if hit is None:
        words = text.lower().split()
        dropped: list[str] = []
        while hit is None and len(words) > 1 and words[-1] in _FILLER:
            dropped.append(words.pop())
            hit = _exact(appreg.norm(" ".join(words)), exes, entries)
        if hit is not None and hit.ok and set(dropped) & {"cli", "command"}:
            # "antigravity cli" is not "antigravity": on the owner's laptop the
            # exact name is the Electron GUI binary while the CLI is `agy`,
            # which shares no name with it. Resolving to the GUI and typing it
            # into a terminal is the same guess in disguise, so hand back the
            # near match plus what ~/.local/bin holds and let the caller pick.
            local = sorted(
                f"{n} ({p})" for n, p in exes.items()
                if p.startswith(str(Path.home() / ".local" / "bin"))
            )[:8]
            return Resolution(candidates=[f"{os.path.basename(hit.path)} ({hit.path}) "
                                          "- same name without 'cli', may be the GUI app"]
                              + local)
    if hit is not None:
        return hit

    return Resolution(candidates=_closest(nq, exes, entries))


def _closest(nq: str, exes: dict[str, str], entries, limit: int = 5) -> list[str]:
    """Near misses for the error message. Never used to pick one."""
    if len(nq) < 2:
        return []
    scored: dict[str, float] = {}
    for n, p in exes.items():
        nn = appreg.norm(n)
        if not nn:
            continue
        if nn.startswith(nq) or (len(nq) >= 3 and nq in nn) or (len(nn) >= 3 and nn in nq):
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, nq, nn).ratio()
        if score >= 0.7:
            scored[f"{n} ({p})"] = score
    for e in entries:
        exe = _exec_of_entry(e)
        ne = appreg.norm(e.name)
        if not exe or not ne:
            continue
        if ne.startswith(nq) or (len(nq) >= 3 and nq in ne):
            scored.setdefault(f"{e.name} -> {exe}", 0.85)
    ranked = sorted(scored, key=lambda k: (-scored[k], k))
    return ranked[:limit]


# ---------------------------------------------------------------------------
# Checking a typed command line before it reaches a shell
# ---------------------------------------------------------------------------

def _segments(command: str) -> list[list[str]]:
    """Split a command line into pipeline/&&/; segments of words."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    try:
        for tok in lexer:
            if tok and set(tok) <= set("&|;()<>"):
                if tok in ("&&", "||", ";", "|", "&", "|&", ";;"):
                    segments.append([])
                continue
            segments[-1].append(tok)
    except ValueError:
        return []   # unbalanced quotes: let the shell say so, we cannot parse it
    return [s for s in segments if s]


def _program_word(words: list[str], allow_path: bool = False) -> str:
    """The word that names the program in one segment, or '' if not checkable."""
    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):      # FOO=1 prog
            i += 1
            continue
        if w in _PREFIXES:
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
            if w == "timeout" and i < len(words):          # timeout 5 prog
                i += 1
            continue
        break
    if i >= len(words):
        return ""
    w = words[i]
    if w in _BUILTINS or any(c in w for c in "$`*?~{}\\") or ("/" in w and not allow_path):
        return ""
    return w


def last_program(command: str) -> str:
    """The program that ends up in the foreground: last segment's program word.

    `cd ~/x && agy` -> `agy`. Empty for builtins and anything unparseable.
    """
    for words in reversed(_segments(command)):
        return os.path.basename(_program_word(words, allow_path=True))
    return ""


def check_command_line(command: str) -> tuple[str, str]:
    """Verify every program in a command line exists *before* it is run.

    Returns ``(command, problem)``. ``problem`` is empty when all programs
    resolve; otherwise it names the unknown program and the closest candidates,
    and nothing should be run. When the first program exists only outside the
    process PATH (e.g. ~/.local/bin under a service), the command is rewritten
    to the absolute path so it runs the same way whatever shell receives it.
    """
    import shutil

    for index, words in enumerate(_segments(command)):
        prog = _program_word(words)
        if not prog:
            continue
        if shutil.which(prog):
            continue
        res = resolve_command(prog)
        if res.ok and appreg.norm(os.path.basename(res.path)) == appreg.norm(prog):
            if index == 0 and command.lstrip().startswith(prog):
                command = command.replace(prog, shlex.quote(res.path), 1)
            continue
        if res.ok:
            return command, (
                f"'{prog}' is not an executable, but it looks like {res.path}. "
                f"Nothing was run. Use `{res.path}` if that is what is meant."
            )
        return command, res.explain(prog) + " Nothing was run. (A shell alias or function will not be found this way.)"
    return command, ""


# ---------------------------------------------------------------------------
# Evidence that a launch really ran
# ---------------------------------------------------------------------------

def _children(pid: int) -> list[int]:
    return desktop._children(pid)


def descendants(pid: int, limit: int = 400) -> set[int]:
    """Every process below ``pid`` (children, grandchildren, ...)."""
    seen: set[int] = set()
    todo = [pid]
    while todo and len(seen) < limit:
        for kid in _children(todo.pop()):
            if kid not in seen:
                seen.add(kid)
                todo.append(kid)
    return seen


def _proc_names(pid: int) -> set[str]:
    """Normalised names a process goes by: comm, argv[0], and argv[1] for
    interpreted scripts (`node /home/x/.local/bin/agy` shows as node)."""
    names: set[str] = set()
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return names
    names.add(appreg.norm(comm))
    argv = [a.decode("utf-8", "replace") for a in raw if a]
    for a in argv[:3]:
        if not a.startswith("-"):
            names.add(appreg.norm(os.path.basename(a)))
    return names


def _matches(pid: int, want: set[str]) -> bool:
    for n in _proc_names(pid):
        if n in want:
            return True
        # comm is cut at 15 characters.
        if len(n) >= 15 and any(w.startswith(n) for w in want):
            return True
    return False


def process_snapshot(pid: int) -> set[int]:
    return descendants(pid) if pid else set()


def wait_for_process(root_pid: int, executable: str, before: set[int],
                     timeout: float = 3.0) -> str:
    """Wait for a *new* process named ``executable`` below ``root_pid``.

    Returns a short evidence string ("pid 4242 agy") or ''. New means not in
    ``before``, so a program that was already running does not count as the
    one just typed. Silence is not failure here: a command that exits at once
    (``ls``) leaves no process, so callers must report "unverified", not "ran".
    """
    if not root_pid:
        return ""
    want = {appreg.norm(os.path.basename(executable))}
    deadline = time.monotonic() + timeout
    while True:
        for pid in descendants(root_pid) - before:
            if _matches(pid, want):
                return f"pid {pid} ({os.path.basename(executable)})"
        if time.monotonic() >= deadline:
            return ""
        time.sleep(0.2)


def process_running(name: str) -> str:
    """Any process on the machine called ``name`` (for apps with no window)."""
    want = {appreg.norm(os.path.basename(name))}
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return ""
    for pid in pids:
        if _matches(pid, want):
            return f"pid {pid}"
    return ""
