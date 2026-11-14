"""Plug-and-play plugins (#245, v3 phase 1c; owner rulings D10, D9).

A plugin is a folder in ``~/.local/share/kravein/plugins/<name>/``::

    plugin.json         name, version, description, author, secrets, tools{...}
    tools/*.py          ordinary @skill functions (a "code plugin")
    agents/<x>/SKILL.md worker profiles (the existing profile format)
    mcp.json            MCP servers: added as PENDING, approved one by one (#243)
    skills/<x>/SKILL.md procedures: listed only until phase 7

The owner enables it once (``kravein plugin enable <name>``), which pins a
hash of every file. Nothing else can enable a plugin or write its files, and
a changed file disables it until the owner enables it again. No hooks (D9).

- ``manifest``: read plugin.json, walk and hash the files.
- ``state``: ``~/.config/kravein/plugins.json``, the owner's enabled list.
- ``loader``: load enabled, intact plugins into the registry and profiles.
- ``admin``: the CLI's actions, ``list`` and ``doctor`` text.
"""
