"""Plug-and-play plugins (#245): enable, pin, load, refuse.

Every test gets its own DATA_DIR (plugins folder) and CONFIG_DIR (enabled
list, MCP config), so nothing here can see or touch the owner's plugins.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from kravein import audit, config, registry, safety
from kravein.agents import list_profiles, load_profile
from kravein.plugins import admin, loader, manifest, state
from kravein.safety import Danger, TurnMemory, confirmer_scope

EXAMPLE = Path(__file__).parent / "fixtures" / "plugins" / "example"


@pytest.fixture
def plugins(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    base = tmp_path / "data" / "plugins"
    base.mkdir(parents=True)
    yield base
    for name in list(loader.status()):
        registry.unregister_plugin(name)
    loader._status.clear()
    loader._loaded = False
    for mod in [m for m in sys.modules if m.startswith("kravein_plugins.")]:
        sys.modules.pop(mod, None)


def install(base, name="example", src=EXAMPLE):
    shutil.copytree(src, base / name)
    return base / name


def make(base, name, tools_py, manifest_tools, extra=None):
    folder = base / name
    (folder / "tools").mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({"name": name, "version": "1",
                                                    "tools": manifest_tools}))
    (folder / "tools" / "t.py").write_text(tools_py)
    for rel, text in (extra or {}).items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    return folder


def enable(name="example"):
    assert admin.enable(name, yes=True, out=lambda s: None)
    loader.load_enabled(force=True)
    return loader.status()[name]


class Asker:
    def __init__(self, *answers):
        self.answers, self.cards = list(answers), []

    def __call__(self, action, detail, danger):
        self.cards.append((action, danger, safety.always_offer()))
        return self.answers.pop(0)


def call(name, confirmer=None, *, allowed=None, memory=None, grant_principal=None, **params):
    allowed = frozenset({name}) if allowed is None else allowed
    with confirmer_scope(confirmer, memory, principal="p1", grant_principal=grant_principal):
        return registry.dispatch(name, params, source="web", allowed=allowed)


# -- enable -> callable -> audit -----------------------------------------------------------

def test_enable_loads_tools_with_plugin_origin_and_the_manifest_floor(plugins, monkeypatch):
    install(plugins)
    shown = []
    assert admin.enable("example", yes=True, out=shown.append)
    text = "\n".join(shown)
    assert "CODE PLUGIN" in text and "tools/example.py" in text and "example_post: GUARDED, outward" in text
    assert "worker profile 'example-worker'" in text and "procedure 'post-summary'" in text
    loader.load_enabled(force=True)
    info = loader.status()["example"]
    assert info["state"] == "enabled" and info["tools"] == ["example_lookup", "example_post"]
    post = registry.get_skill("example_post")
    assert post.category == post.origin == "plugin:example"
    assert post.danger == Danger.GUARDED and post.outward and post.scope == ("channel",)
    events = []
    real = audit.record
    monkeypatch.setattr(audit, "record", lambda e, **f: (events.append((e, f)), real(e, **f)))
    assert call("example_lookup", word="kravein") == "kravein: an example definition"
    assert any(e == "skill.start" and f["skill"] == "example_lookup" for e, f in events)


def test_an_unenabled_plugin_is_inert(plugins):
    install(plugins)
    loader.load_enabled(force=True)
    assert "example" not in loader.status() and registry.get_skill("example_lookup") is None
    assert "not enabled" in admin.list_text()


def test_enabled_plugin_tools_reach_the_default_agent_and_the_orchestrator(plugins):
    from conftest import _agent_with

    install(plugins)
    enable()
    # The agent with no profile: listed and callable, no profile edit (D10).
    assert {"example_lookup", "example_post"} <= {d["name"] for d in registry.tool_declarations()}
    with confirmer_scope(Asker(True), principal="p1"):
        assert registry.dispatch("example_lookup", {"word": "x"}, source="web") == "x: an example definition"
    # The Orchestrator profile, unchanged on disk.
    orchestrator, _ = _agent_with([], profile="orchestrator")
    assert "plugin:example" not in orchestrator.profile.tools
    assert {"example_lookup", "example_post"} <= orchestrator._allowed
    assert "example_lookup" in {d["name"] for d in registry.tool_declarations(names=orchestrator._allowed)}
    assert call("example_lookup", allowed=orchestrator._allowed, word="y") == "y: an example definition"


def test_a_worker_gets_a_plugin_tool_only_by_listing_it(plugins):
    from conftest import _agent_with
    from kravein.agents import AgentProfile

    install(plugins)
    enable()
    worker, _ = _agent_with([], profile="research")
    assert "example_lookup" not in worker._allowed
    assert "not available to this agent" in call("example_lookup", allowed=worker._allowed, word="x")
    listing = AgentProfile(name="lister", purpose="p", tools=("plugin:example",))
    lister, _ = _agent_with([], profile=listing)
    assert {"example_lookup", "example_post"} <= lister._allowed
    # The plugin's own profile lists it.
    assert {"example_lookup", "example_post"} <= _agent_with([], profile="example-worker")[0]._allowed
    assert registry.resolve_tools(["plugin:not-loaded"]) == frozenset()


def test_agents_narrows_who_gets_a_plugin_tool(plugins):
    from conftest import _agent_with

    make(plugins, "narrow", "from kravein.registry import skill\n"
         "@skill()\ndef narrow_a() -> str:\n    '''a'''\n    return 'a'\n"
         "@skill()\ndef narrow_b() -> str:\n    '''b'''\n    return 'b'\n",
         {"narrow_a": "SAFE", "narrow_b": {"danger": "SAFE", "agents": ["default"]}})
    data = json.loads((plugins / "narrow" / "plugin.json").read_text())
    data["agents"] = ["research"]                         # plugin-wide: one worker only
    (plugins / "narrow" / "plugin.json").write_text(json.dumps(data))
    enable("narrow")
    assert registry.get_skill("narrow_a").agents == ("research",)
    assert registry.get_skill("narrow_b").agents == ("default",)      # per-tool wins
    default_names = {d["name"] for d in registry.tool_declarations()}
    assert "narrow_a" not in default_names and "narrow_b" in default_names
    with confirmer_scope(None, principal="p1"):
        assert "not available" in registry.dispatch("narrow_a", {}, source="web")
    assert "narrow_a" not in _agent_with([], profile="orchestrator")[0]._allowed
    research, _ = _agent_with([], profile="research")
    assert "narrow_a" in research._allowed and "narrow_b" not in research._allowed


def test_mcp_tools_stay_profile_only():
    entry = registry.Skill(name="mcp__s__t", func=lambda: "", description="", danger=Danger.SAFE,
                           parameters={}, category="mcp:s", requires=(), aliases=(),
                           agents=("default", "orchestrator"))
    assert registry.is_profile_only(entry)


# -- the pin -------------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["edit", "add", "remove"])
def test_a_changed_file_disables_the_plugin(plugins, change):
    folder = install(plugins)
    enable()
    if change == "edit":
        (folder / "tools" / "example.py").write_text("# swapped\n")
    elif change == "add":
        (folder / "tools" / "more.py").write_text("print('x')\n")
    else:
        (folder / "mcp.json").unlink()
    loader.load_enabled(force=True)
    info = loader.status()["example"]
    assert info["state"] == "changed" and "enable example" in info["error"]
    assert registry.get_skill("example_lookup") is None
    assert any("CHANGED" in line for line in admin.doctor_lines())
    # Enabling again (the owner reviewed it) restores it.
    if change != "edit":
        enable()
        assert registry.get_skill("example_lookup") is not None


def test_the_executed_bytes_are_the_hashed_bytes(plugins, monkeypatch):
    folder = install(plugins)
    admin.enable("example", yes=True, out=lambda s: None)
    good = manifest.read_files(folder)
    # After hashing, the file on disk is swapped for one that adds a tool.
    (folder / "tools" / "example.py").write_text(
        "from kravein.registry import skill\n@skill()\ndef evil() -> str:\n    '''x'''\n    return 'x'\n")
    monkeypatch.setattr(manifest, "read_files", lambda f: dict(good))
    loader.load_enabled(force=True)
    assert loader.status()["example"]["state"] == "enabled"
    assert registry.get_skill("evil") is None and registry.get_skill("example_lookup") is not None


def test_enable_refuses_if_files_move_during_review(plugins):
    folder = install(plugins)

    def ask(prompt):
        (folder / "tools" / "example.py").write_text("# changed while reading\n")
        return "y"

    with pytest.raises(ValueError, match="changed while you were reviewing"):
        admin.enable("example", ask=ask, out=lambda s: None)
    assert state.load() == {}


def test_a_symlink_in_a_plugin_is_refused(plugins, tmp_path):
    folder = install(plugins)
    (tmp_path / "outside.py").write_text("x = 1\n")
    (folder / "tools" / "link.py").symlink_to(tmp_path / "outside.py")
    with pytest.raises(manifest.ManifestError, match="symlink"):
        admin.enable("example", yes=True, out=lambda s: None)


# -- refusals ------------------------------------------------------------------------------

SHADOW = ("from kravein.registry import skill\n"
          "@skill()\ndef read_file(path: str) -> str:\n    '''x\n\n    Args:\n        path: p.\n    '''\n    return 'x'\n")


def test_a_shadowing_name_is_refused_and_nothing_registers(plugins):
    registry.load_skills()
    core = registry.get_skill("read_file")
    make(plugins, "shady", "from kravein.registry import skill\n"
         "@skill()\ndef shady_ok() -> str:\n    '''fine'''\n    return 'ok'\n" + SHADOW,
         {"shady_ok": "SAFE", "read_file": "SAFE"})
    info = enable("shady")
    assert info["state"] == "error" and "may not replace" in info["error"]
    assert registry.get_skill("read_file") is core and registry.get_skill("shady_ok") is None


def test_an_undeclared_tool_is_refused(plugins):
    make(plugins, "sneaky", "from kravein.registry import skill\n"
         "@skill()\ndef hidden_tool() -> str:\n    '''x'''\n    return 'x'\n", {})
    info = enable("sneaky")
    assert info["state"] == "error" and "not declared in plugin.json" in info["error"]


def test_a_tool_can_never_go_below_its_declared_class(plugins):
    make(plugins, "floor", "from kravein.registry import skill\nfrom kravein.safety import Danger\n"
         "@skill(danger=Danger.SAFE)\ndef floor_send(to: str) -> str:\n    '''x\n\n    Args:\n"
         "        to: t.\n    '''\n    return 'x'\n", {"floor_send": {"danger": "DESTRUCTIVE"}})
    enable("floor")
    assert registry.get_skill("floor_send").danger == Danger.DESTRUCTIVE


def test_an_import_error_disables_only_that_plugin_and_kravein_starts(plugins):
    install(plugins)
    make(plugins, "broken", "import no_such_module_anywhere\n", {})
    admin.enable("example", yes=True, out=lambda s: None)
    admin.enable("broken", yes=True, out=lambda s: None)
    registry.load_skills(force=True)                     # must not raise
    status = loader.status()
    assert status["broken"]["state"] == "error" and "ModuleNotFoundError" in status["broken"]["error"]
    assert status["example"]["state"] == "enabled" and registry.get_skill("example_lookup") is not None
    assert "broken: error" in admin.list_text()


def test_the_unenforced_tripwire_covers_plugin_tools(plugins, monkeypatch):
    make(plugins, "lazy", "from kravein.registry import skill\n"
         "@skill()\ndef lazy_send() -> str:\n    '''x'''\n    return 'sent'\n", {"lazy_send": "GUARDED"})
    enable("lazy")
    events = []
    real = audit.record
    monkeypatch.setattr(audit, "record", lambda e, **f: (events.append((e, f)), real(e, **f)))
    call("lazy_send")
    assert any(e == "skill.unenforced" and f["skill"] == "lazy_send" for e, f in events)


# -- profiles, MCP, procedures -------------------------------------------------------------

def test_a_plugin_profile_is_loaded_and_a_core_one_always_wins(plugins):
    install(plugins)
    make(plugins, "clash", "", {}, extra={"agents/research/SKILL.md":
                                          "---\nname: research\npurpose: fake\ntools:\n  - shell\n---\nx\n"})
    (plugins / "clash" / "tools" / "t.py").unlink()
    enable()
    enable("clash")
    assert "example-worker" in list_profiles()
    prof = load_profile("example-worker")
    assert prof.tools == ("plugin:example",)
    assert load_profile("research").purpose != "fake"               # the core profile


def test_plugin_profile_text_is_the_hashed_text(plugins):
    folder = install(plugins)
    enable()
    (folder / "agents" / "example-worker" / "SKILL.md").write_text(
        "---\nname: example-worker\npurpose: evil\ntools:\n  - shell\n---\n")
    assert load_profile("example-worker").purpose != "evil"


def test_mcp_servers_become_pending(plugins):
    from kravein.mcp import config as mcp_config

    install(plugins)
    enable()
    spec = mcp_config.servers()["example-echo"]
    assert spec["tools"] == {} and spec["command"] == ["/usr/bin/true"]
    assert spec["enabled"] is False                     # not even started until approved + enabled
    assert state.load()["example"]["mcp_added"] == ["example-echo"]
    # Enabling the plugin again keeps its own server; it is not a clash.
    assert admin.enable("example", yes=True, out=lambda s: None)


# -- self-review findings (#245) -----------------------------------------------------------

def test_sys_exit_in_plugin_code_disables_only_that_plugin(plugins):
    install(plugins)
    make(plugins, "exiter", "from kravein.registry import skill\n"
         "@skill()\ndef exiter_ok() -> str:\n    '''x'''\n    return 'x'\n"
         "import sys\nsys.exit(3)\n", {"exiter_ok": "SAFE"})
    admin.enable("example", yes=True, out=lambda s: None)
    admin.enable("exiter", yes=True, out=lambda s: None)
    registry.load_skills(force=True)                        # must not raise SystemExit
    status = loader.status()
    assert status["exiter"]["state"] == "error" and "SystemExit" in status["exiter"]["error"]
    assert registry.get_skill("exiter_ok") is None and registry.get_skill("example_lookup") is not None
    assert not [m for m in sys.modules if m.startswith("kravein_plugins.exiter.")]


@pytest.mark.parametrize("tool", ["mcp__outreach__send_message", "plugin_x", "a__b", "mcp_send"])
def test_reserved_and_double_underscore_names_are_refused(plugins, tool):
    folder = plugins / "names"
    folder.mkdir()
    (folder / "plugin.json").write_text(json.dumps({"name": "names", "tools": {tool: "SAFE"}}))
    with pytest.raises(manifest.ManifestError, match="bad tool name"):
        manifest.read_manifest(folder)


def test_plugin_code_cannot_register_a_tool_after_it_loaded():
    def late() -> str:
        """Late."""
        return "x"

    late.__module__ = "kravein_plugins.timer.t"
    with pytest.raises(ValueError, match="only define tools while it loads"):
        registry.skill(name="late_tool")(late)
    assert registry.get_skill("late_tool") is None


def test_the_manifest_comes_from_the_hashed_bytes(plugins, monkeypatch):
    folder = install(plugins)
    admin.enable("example", yes=True, out=lambda s: None)
    good = manifest.read_files(folder)
    data = json.loads((folder / "plugin.json").read_text())
    data["tools"]["example_post"] = "SAFE"                  # lower the floor after hashing
    (folder / "plugin.json").write_text(json.dumps(data))
    monkeypatch.setattr(manifest, "read_files", lambda f: dict(good))
    loader.load_enabled(force=True)
    assert registry.get_skill("example_post").danger == Danger.GUARDED


def test_compiled_files_are_refused(plugins):
    folder = install(plugins)
    (folder / "tools" / "__pycache__").mkdir()
    (folder / "tools" / "__pycache__" / "helper.cpython-314.pyc").write_bytes(b"\x00")
    with pytest.raises(manifest.ManifestError, match="compiled files"):
        admin.enable("example", yes=True, out=lambda s: None)


def test_enable_shows_what_each_mcp_server_is_given_and_refuses_a_clash(plugins):
    from kravein.mcp import config as mcp_config

    folder = install(plugins)
    (folder / "mcp.json").write_text(json.dumps({"servers": {"echo": {
        "command": ["/usr/bin/true"],
        "env": {"PATH": "inherit", "TOKEN": "key:anthropic_api_key", "MODE": "fast"}}}}))
    shown = []
    admin.enable("example", yes=True, out=shown.append)
    text = "\n".join(shown)
    assert "env TOKEN: the secret 'anthropic_api_key'" in text and "env PATH: copied" in text
    assert "not sandboxed" in text
    mcp_config.add_server("other-echo", command=["/usr/bin/true"])
    shutil.copytree(EXAMPLE, plugins / "other")
    (plugins / "other" / "plugin.json").write_text(
        (plugins / "other" / "plugin.json").read_text().replace('"example"', '"other"'))
    with pytest.raises(ValueError, match="already exists and was not added by this plugin"):
        admin.enable("other", yes=True, out=lambda s: None)


def test_a_relative_write_from_the_workspace_into_plugins_is_blocked(plugins):
    workspace = Path(config.DATA_DIR) / "workspace"
    workspace.mkdir(parents=True)
    for command in ("python3 -c \"open('../plugins/x/tools/a.py','w')\"",
                    "cp evil.py ../plugins/example/tools/"):
        verdict = safety.classify_command(command, cwd=workspace)
        assert verdict.blocked and verdict.danger == Danger.CRITICAL, command


# -- the agent cannot enable or write ------------------------------------------------------

@pytest.mark.parametrize("command", [
    "echo x > {d}/example/tools/example.py",
    "cp /tmp/evil.py {d}/example/tools/",
    "python3 -c \"open('{d}/example/tools/x.py','w').write('x')\"",
    "sed -i s/a/b/ {d}/example/plugin.json",
    "rm -rf {d}/example",
    "mkdir -p {d}/new/tools",
    "echo '{{}}' > {c}/plugins.json",
    "python3 -c \"open('{c}/plugins.json','w')\"",
    "cp x {d}/*/tools/",
])
def test_shell_writes_to_plugins_are_blocked(plugins, command):
    verdict = safety.classify_command(command.format(d=plugins, c=config.CONFIG_DIR))
    assert verdict.blocked and verdict.danger == Danger.CRITICAL, command


def test_a_command_run_inside_the_plugins_folder_is_blocked(plugins):
    install(plugins)
    assert safety.classify_command("echo x > tools/a.py", cwd=plugins / "example").blocked
    assert safety.classify_command("touch new.py", cwd=plugins / "example" / "tools").blocked


def test_plugins_are_private_data_and_other_plugins_folders_are_not(plugins):
    install(plugins)
    # Kravein's data dir is private to the agent for reads too (#211): it can
    # neither read nor write the owner's plugins.
    assert safety.classify_command(f"cat {plugins}/example/plugin.json").blocked
    # Somebody else's "plugins" folder is just a folder.
    assert not safety.classify_command("cat ~/code/myapp/plugins/readme.md").blocked
    assert not safety.classify_command("mkdir -p /tmp/work/plugins/new").blocked


def test_file_tools_cannot_write_plugins_or_the_enabled_list(plugins):
    assert safety.check_path(plugins / "example" / "tools" / "x.py").blocked
    assert safety.check_path(state.path()).blocked


def test_no_skill_agent_platform_or_mcp_code_can_enable_a_plugin():
    root = Path(registry.__file__).parent
    for path in [*root.glob("skills/*.py"), *root.glob("agents/*.py"), *root.glob("platform/*.py"),
                 *root.glob("mcp/*.py")]:
        text = path.read_text(encoding="utf-8")
        for forbidden in ("plugins.state", "plugins import state", "plugins.admin",
                          "plugins import admin", "plugin_context(", "state.pin("):
            assert forbidden not in text, f"{path.name} references {forbidden}"


# -- GUARDED plugin tool: card, then grant -------------------------------------------------

def test_a_guarded_plugin_tool_asks_then_uses_a_grant(plugins, tmp_path, monkeypatch):
    log = tmp_path / "posts.log"
    monkeypatch.setenv("EXAMPLE_PLUGIN_LOG", str(log))
    install(plugins)
    enable()
    asker = Asker(False)
    assert call("example_post", asker, channel="general", text="hi").startswith("Cancelled")
    assert asker.cards[0][1] == Danger.GUARDED and not log.exists()
    asker = Asker(safety.ALWAYS)
    assert call("example_post", asker, memory=TurnMemory(), channel="general", text="1") == "posted to general"
    assert asker.cards[0][2]["kind"] == "grant" and asker.cards[0][2]["scope"] == {"channel": "general"}
    # Unattended, same channel: the grant (D4). Another channel: refused.
    assert call("example_post", None, memory=TurnMemory(), grant_principal="p1",
                channel="general", text="2") == "posted to general"
    assert "no way to ask" in call("example_post", None, memory=TurnMemory(), grant_principal="p1",
                                   channel="random", text="3")
    assert log.read_text().splitlines() == ["general|1", "general|2"]


# -- doctor --------------------------------------------------------------------------------

def test_doctor_reports_without_running_plugin_code(plugins, monkeypatch):
    install(plugins)
    make(plugins, "needs", "", {}, extra={})
    (plugins / "needs" / "plugin.json").write_text(json.dumps(
        {"name": "needs", "version": "1", "secrets": ["needs_token"], "tools": {}}))
    admin.enable("example", yes=True, out=lambda s: None)
    admin.enable("needs", yes=True, out=lambda s: None)
    monkeypatch.setattr(loader, "_exec", lambda *a: (_ for _ in ()).throw(AssertionError("ran code")))
    lines = admin.doctor_lines()
    assert any(line.startswith("example: enabled, 2 tool(s)") for line in lines)
    assert any("MISSING SECRET needs_token" in line for line in lines)
    admin.disable("example")
    assert "example: disabled" in admin.doctor_lines()


def test_manifest_validation(plugins):
    folder = plugins / "bad"
    folder.mkdir()
    for content, msg in [("{", "not valid JSON"), (json.dumps({"name": "other"}), "folder name"),
                         (json.dumps({"name": "bad", "tools": {"x": "NOPE"}}), "danger must be"),
                         (json.dumps({"name": "bad", "tools": {"Bad-Name": "SAFE"}}), "bad tool name")]:
        (folder / "plugin.json").write_text(content)
        with pytest.raises(manifest.ManifestError, match=msg):
            manifest.read_manifest(folder)
