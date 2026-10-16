"""Database engine and request-scoped SQLAlchemy sessions."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot_api.core.config import BACKEND_ROOT, Settings, get_settings


def prepare_database_url(database_url: str) -> URL:
    """Create a local SQLite parent directory and anchor relative paths to backend/."""

    url = make_url(database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername not in {"sqlite", "postgresql+psycopg"}:
        raise ValueError("Unsupported database driver")
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return url

    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = (BACKEND_ROOT / database_path).resolve()

    database_path.parent.mkdir(parents=True, exist_ok=True)
    return url.set(database=str(database_path))


def build_engine(database_url: str, *, settings: Settings | None = None) -> Engine:
    """Build an engine and enforce foreign keys for SQLite connections."""

    url = prepare_database_url(database_url)
    options = {}
    connect_args = {"check_same_thread": False}
    if url.get_backend_name() == "postgresql":
        config = settings or get_settings()
        connect_args = {
            "connect_timeout": config.database_connect_timeout_seconds,
            "options": (
                "-c timezone=UTC "
                f"-c statement_timeout={config.database_statement_timeout_ms} "
                f"-c lock_timeout={config.database_lock_timeout_ms} "
                "-c idle_in_transaction_session_timeout=30000"
            ),
        }
        options = dict(pool_size=config.database_pool_size, max_overflow=config.database_max_overflow,
                       pool_timeout=config.database_pool_timeout_seconds, pool_recycle=1800)
    new_engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True,
                               hide_parameters=True, echo=False, **options)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(new_engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return new_engine


def verify_runtime_database_role(runtime_engine: Engine) -> None:
    """Fail production startup if the effective role can administer the DB.

    This read-only catalog check also covers inherited/settable privileged
    roles and object ownership, rather than trusting a login role's name.
    Schema migration uses a separate operator identity outside app startup.
    """
    message = "数据库安全检查失败，请检查连接和应用角色权限"
    if runtime_engine.dialect.name != "postgresql":
        raise RuntimeError(message)
    try:
        with runtime_engine.connect() as connection:
            excessive = connection.scalar(text("""
                SELECT
                    EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE pg_has_role(oid, 'MEMBER')
                          AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
                               OR rolname IN ('pg_read_all_data', 'pg_write_all_data',
                                              'pg_read_server_files', 'pg_write_server_files',
                                              'pg_execute_server_program', 'pg_signal_backend'))
                    )
                    OR has_database_privilege(current_database(), 'CREATE')
                    OR EXISTS (
                        SELECT 1 FROM pg_namespace
                        WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
                          AND has_schema_privilege(oid, 'CREATE')
                    )
                    OR EXISTS (
                        SELECT 1 FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
                        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                          AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
                          AND pg_has_role(c.relowner, 'MEMBER')
                    )
            """))
    except SQLAlchemyError:
        raise RuntimeError(message) from None
    if excessive is not False:
        raise RuntimeError(message)


engine = build_engine(get_settings().database_url, settings=get_settings())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield one SQLAlchemy session per request and always close it."""

    with SessionLocal() as session:
        yield session
