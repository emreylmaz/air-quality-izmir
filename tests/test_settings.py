"""Smoke test — settings load from environment without real secrets."""
from __future__ import annotations

import os

from src.config.settings import Settings


def test_settings_defaults_do_not_contain_real_secrets() -> None:
    """Defaults must be placeholders only."""
    settings = Settings()
    assert settings.app_env == "local"
    assert "local_dev_pw" in settings.database_url.get_secret_value()
    assert settings.openweather_api_key.get_secret_value() == ""


def test_settings_reads_env_overrides(monkeypatch) -> None:  # noqa: ANN001
    """Pydantic-settings overrides from env vars."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka-prod:9092")

    # Bypass lru_cache by instantiating directly
    settings = Settings()
    assert settings.app_env == "production"
    assert settings.kafka_bootstrap_servers == "kafka-prod:9092"


def test_secrets_not_visible_in_repr() -> None:
    """SecretStr must mask value in repr output."""
    os.environ["OPENWEATHER_API_KEY"] = "leaky_secret_do_not_print"
    settings = Settings()
    assert "leaky_secret_do_not_print" not in repr(settings)
    del os.environ["OPENWEATHER_API_KEY"]
