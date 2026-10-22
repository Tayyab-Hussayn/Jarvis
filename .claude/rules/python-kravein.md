---
paths:
  - "kravein/**/*.py"
  - "tests/**/*.py"
---

# Kravein Python conventions

- Python 3.14 venv: `.venv/bin/python`. Fast syntax check: `.venv/bin/python -m compileall -q kravein/`.
- A skill is a plain `@skill` function; signature, type hints and the docstring `Args:` block *are* the tool schema. Parameters `_source`, `_confirm`, `_context` are injected and hidden from the model.
- Skills never shell out; they use `kravein/platform/`. `platform/shell.py` is the only place subprocesses start.
- `server.py` does not use `from __future__ import annotations`.
- Tests stub all three providers (`_llm.cline`, `.gemini`, `.openrouter`; stub `.name` must be a real provider name) and monkeypatch `config.STATE_DIR` when touching breaker state.
