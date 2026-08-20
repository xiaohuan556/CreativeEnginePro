from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_options(url: str) -> dict:
    return {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}


settings = get_settings()
engine = create_engine(settings.database_url, **_engine_options(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def create_schema() -> None:
    from . import models  # noqa: F401
    # API and worker may start together. Serialize DDL on PostgreSQL so an
    # upgrade cannot race the same compatibility migration from two processes.
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(742019381)"))
        Base.metadata.create_all(bind=connection)
        # `create_all` intentionally does not alter existing tables. Keep this
        # compatibility migration tiny and idempotent for the first preview DB.
        columns = {column["name"] for column in inspect(connection).get_columns("assets")}
        if "in_library" not in columns:
            connection.execute(text("ALTER TABLE assets ADD COLUMN in_library BOOLEAN NOT NULL DEFAULT FALSE"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_assets_in_library ON assets (in_library)"))
        usage_columns = {column["name"] for column in inspect(connection).get_columns("usage_limits")}
        if "daily_asset_mb" not in usage_columns:
            connection.execute(text("ALTER TABLE usage_limits ADD COLUMN daily_asset_mb INTEGER NOT NULL DEFAULT 2048"))
        if "storage_mb" not in usage_columns:
            connection.execute(text("ALTER TABLE usage_limits ADD COLUMN storage_mb INTEGER NOT NULL DEFAULT 20480"))
