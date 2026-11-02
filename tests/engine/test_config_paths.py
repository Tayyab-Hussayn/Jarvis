"""config.py reads only the XDG layout; the old in-repo and pre-rename paths are gone."""

from kravein import config


def test_app_dirs_are_plain_kravein():
    for d in (config.CONFIG_DIR, config.DATA_DIR, config.STATE_DIR, config.CACHE_DIR):
        assert d.name == config.APP_NAME == "kravein"


def test_no_legacy_fallbacks_remain():
    assert not hasattr(config, "LEGACY_CONFIG_DIR")
    assert not hasattr(config, "LEGACY_APP_NAME")
    assert not hasattr(config, "_app_dir")


def test_api_keys_ignore_repo_config_dir(tmp_path, monkeypatch):
    # A stray <repo>/config/api_keys.json must no longer be merged in.
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "xdg")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "api_keys.json").write_text('{"gemini_api_key": "from-repo"}')
    for var in ("KRAVEIN_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert config.api_keys().get("gemini_api_key", "") == ""
