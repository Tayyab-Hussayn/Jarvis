"""Skills tests never touch the owner's real learned-app registry (#200).

A launch that is verified writes ``apps.json``; without this, every test that
launches a fake app would teach the real ~/.local/share/kravein/ its names.
"""

from __future__ import annotations

import pytest

from kravein import config
from kravein.platform import apps


@pytest.fixture(autouse=True)
def _private_data_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path_factory.mktemp("kravein-data"))
    # Verification polls for a window; a test that fakes a launch must not wait
    # the full production timeout for one that will never appear.
    monkeypatch.setattr(apps, "_VERIFY_TIMEOUT", 0.3)
    yield


# `from conftest import _FakeBackend, ...` is how the skills tests reach the
# shared fakes in tests/conftest.py. Adding this file makes `conftest` resolve
# here, so re-export everything from the parent one.
import importlib.util as _ilu  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_kravein_root_conftest", _Path(__file__).resolve().parent.parent / "conftest.py")
_root = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_root)
globals().update({k: v for k, v in vars(_root).items() if not k.startswith("__")})
