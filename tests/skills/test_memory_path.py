"""Memory loads only from config.MEMORY_FILE.

The in-repo `memory/long_term.json` fallback came from the Windows tree
(deleted in #127); reading it could silently surface stale data.
"""

import json

from kravein import config
from kravein.skills import memory


def test_missing_memory_file_is_empty_even_if_repo_legacy_exists(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    (base / "memory").mkdir(parents=True)
    (base / "memory" / "long_term.json").write_text(
        json.dumps({"facts": {"stale": {"value": "x"}}}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "BASE_DIR", base)
    monkeypatch.setattr(config, "MEMORY_FILE", tmp_path / "state" / "memory.json")

    data = memory._load()

    assert data == {c: {} for c in memory.CATEGORIES}
