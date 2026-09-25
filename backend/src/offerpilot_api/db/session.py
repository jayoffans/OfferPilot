"""Database engine and request-scoped SQLAlchemy sessions."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from offerpilot_api.core.config import BACKEND_ROOT, get_settings


def prepare_database_url(database_url: str) -> URL:
    """Create a local SQLite parent directory and anchor relative paths to backend/."""

    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return url

    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = (BACKEND_ROOT / database_path).resolve()

    database_path.parent.mkdir(parents=True, exist_ok=True)
    return url.set(database=str(database_path))


def build_engine(database_url: str) -> Engine:
    """Build an engine and enforce foreign keys for SQLite connections."""

    url = prepare_database_url(database_url)
    connect_args = {"check_same_thread": False} if url.get_backend_name() == "sqlite" else {}
    new_engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(new_engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return new_engine


engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield one SQLAlchemy session per request and always close it."""

    with SessionLocal() as session:
        yield session
