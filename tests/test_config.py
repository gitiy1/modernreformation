import os
from pathlib import Path

from modernreformation_sync.config import load_config, load_env_file


def test_load_config_expands_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SITE_BASE_URL", "https://example.test/")
    monkeypatch.setenv("TRANSLATION_ENABLED", "false")
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
site:
  base_url: "${SITE_BASE_URL}"
translation:
  enabled: "${TRANSLATION_ENABLED:-true}"
readeck:
  enabled: "${READECK_ENABLED:-false}"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.site.base_url == "https://example.test"
    assert config.translation.enabled is False
    assert config.readeck.enabled is False


def test_load_env_file_sets_missing_values_only(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
    env_path = tmp_path / ".env"
    env_path.write_text(
        """
# local debug secrets
OPENAI_API_KEY=from-file
READECK_TOKEN="debug-token"
""",
        encoding="utf-8",
    )

    load_env_file(env_path)

    assert os.environ["OPENAI_API_KEY"] == "from-shell"
    assert os.environ["READECK_TOKEN"] == "debug-token"


def test_translation_api_keys_accept_comma_separated_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEYS", "key-a,key-b\nkey-c")
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
translation:
  api_keys: "${OPENAI_API_KEYS}"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.translation.api_keys == ["key-a", "key-b", "key-c"]
