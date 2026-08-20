from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CEP_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./creative_engine_server.db"
    public_origin: str = "http://localhost:3000"
    session_cookie: str = "cep_session"
    session_days: int = 7
    login_max_failures: int = 8
    login_lock_minutes: int = 20
    default_daily_tasks: int = 50
    default_daily_credits: int = 5000
    default_concurrent_tasks: int = 2
    bootstrap_admin: str = "admin"
    storage_dir: str = "./creative_engine_media"
    max_upload_mb: int = 500
    worker_poll_seconds: float = 1.0
    task_lease_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()
