from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRACEGRADE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    application_name: str = "TraceGrade API"
    application_version: str = "0.1.0"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    admin_key: str = Field(default="change-me", min_length=8)
    api_key_hash_secret: str = Field(
        default="development-only-api-key-hash-secret",
        min_length=32,
    )
    database_url: str = "postgresql+asyncpg://tracegrade:tracegrade@localhost:5432/tracegrade"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "tracegrade-experiments"
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
