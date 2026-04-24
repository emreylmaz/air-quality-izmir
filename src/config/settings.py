"""Application settings loaded from environment variables via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All values sourced from environment."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    app_env: Literal["local", "preview", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # PostgreSQL
    database_url: SecretStr = Field(
        default=SecretStr("postgresql://app:local_dev_pw@localhost:5432/air_quality")
    )

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_raw: str = "air-quality-raw"
    kafka_topic_weather: str = "weather-raw"
    kafka_topic_dlq: str = "air-quality-dlq"
    kafka_client_id: str = "aqi-ingestion"

    # OpenWeatherMap
    openweather_api_key: SecretStr = Field(default=SecretStr(""))
    openweather_base_url: str = "https://api.openweathermap.org"

    # Scheduler
    ingestion_interval_minutes: int = 60

    # Spark
    spark_master: str = "local[*]"
    spark_checkpoint_dir: str = "/opt/spark-checkpoints/air-quality"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
