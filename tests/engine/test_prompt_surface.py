"""Prompt-surface snapshot (#234, plan §7 / Tier 0): the stock system prompt,
each profile's prompt text and each profile's ``tools[]`` must not change in
bytes unnoticed. Tool schemas are the largest, most cached part of every
request; this test is what stops schema bloat from creeping back.

When a change is intended, regenerate the fixtures and commit them:

    KRAVEIN_UPDATE_SNAPSHOT=1 .venv/bin/python -m pytest tests/engine/test_prompt_surface.py

Token estimates per profile print with ``-s``. Tools are declared with
``include_unavailable=True`` so the snapshot does not depend on which desktop
tools this machine has, and only ``kravein.*`` skills count (tests register
their own ``zz_*`` skills in the shared registry).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from kravein import registry
from kravein.agent import SYSTEM_PROMPT
from kravein.agents import list_profiles, load_profile

FIXTURES = Path(__file__).parent / "fixtures" / "prompt_surface"
UPDATE = os.environ.get("KRAVEIN_UPDATE_SNAPSHOT") == "1"


def _ours() -> set[str]:
    registry.load_skills()
    # MCP (#243) and plugin (#245) tools come and go: never part of the snapshot.
    return {name for name, entry in registry.all_skills().items()
            if getattr(entry.func, "__module__", "").startswith("kravein.")
            and not registry.is_addon(entry)}


def _tools(names=None) -> str:
    ours = _ours()
    decls = registry.tool_declarations(
        include_unavailable=True, names=None if names is None else frozenset(names) & ours)
    decls = [d for d in decls if d["name"] in ours]
    return json.dumps(decls, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def surface() -> dict[str, str]:
    files = {"system_prompt.txt": SYSTEM_PROMPT, "tools-default.json": _tools()}
    for name in list_profiles():
        profile = load_profile(name)
        files[f"profile-{name}.txt"] = f"{profile.purpose}\n\n{profile.instructions}\n"
        files[f"tools-{name}.json"] = _tools(registry.resolve_tools(profile.tools))
    return files


def test_prompt_surface_matches_the_snapshot():
    current = surface()
    if UPDATE:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        for stale in FIXTURES.iterdir():
            if stale.name not in current:
                stale.unlink()
        for name, text in current.items():
            (FIXTURES / name).write_text(text, encoding="utf-8")

    def wire(name: str) -> int:
        # Measured as sent (compact JSON), not as the indented fixture.
        return len(json.dumps(json.loads(current[name]), separators=(",", ":")))

    print("\nPrompt surface (estimated tokens = chars / 4):")
    for name in list_profiles():
        prompt = len(SYSTEM_PROMPT) + len(current[f"profile-{name}.txt"])
        tools = wire(f"tools-{name}.json")
        print(f"  {name:12} prompt ~{prompt // 4:>6}  tools ~{tools // 4:>6}  "
              f"total ~{(prompt + tools) // 4:>6}")
    print(f"  {'(no profile)':12} prompt ~{len(SYSTEM_PROMPT) // 4:>6}  "
          f"tools ~{wire('tools-default.json') // 4:>6}")

    stored = {p.name: p.read_text(encoding="utf-8") for p in FIXTURES.iterdir()} if FIXTURES.exists() else {}
    changed = sorted(n for n in current if stored.get(n) != current[n])
    extra = sorted(set(stored) - set(current))
    assert not changed and not extra, (
        f"prompt surface changed: {changed or ''} {('stale: ' + str(extra)) if extra else ''}\n"
        "If intended, run: KRAVEIN_UPDATE_SNAPSHOT=1 .venv/bin/python -m pytest "
        "tests/engine/test_prompt_surface.py  and commit tests/engine/fixtures/prompt_surface/"
    )


def test_the_snapshot_ignores_test_registered_skills():
    from kravein.registry import skill
    from kravein.safety import Danger

    before = surface()

    @skill(name="zz_snapshot_probe", category="settings", danger=Danger.SAFE)
    def zz_snapshot_probe() -> str:
        """A test-only skill."""
        return "x"

    try:
        assert surface() == before
    finally:
        registry._registry.pop("zz_snapshot_probe", None)


def test_the_dead_confirm_line_is_gone():
    assert "confirm=true" not in SYSTEM_PROMPT
    assert "Never try to work around a confirmation." in SYSTEM_PROMPT
