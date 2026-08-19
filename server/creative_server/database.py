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
    Base.metadata.create_all(engine)
    # `create_all` intentionally does not alter existing tables. Keep this
    # compatibility migration tiny and idempotent so upgrades from the first
    # company preview do not fail when the asset-library flag is introduced.
    columns = {column["name"] for column in inspect(engine).get_columns("assets")}
    if "in_library" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE assets ADD COLUMN in_library BOOLEAN NOT NULL DEFAULT FALSE"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_assets_in_library ON assets (in_library)"))
