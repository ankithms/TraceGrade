from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACEGRADE_", extra="ignore")

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "tracegrade-experiments"
    database_url: str = "postgresql+asyncpg://tracegrade:tracegrade@localhost:5432/tracegrade"


@lru_cache
def get_settings() -> Settings:
    return Settings()
