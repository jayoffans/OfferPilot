"""Create only fresh S5 databases; never migrate or clear a supplied database."""
import os
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.session import build_engine
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))


def migrate(url, revision="head"):
    previous = os.environ.get("OFFERPILOT_DATABASE_URL")
    os.environ["OFFERPILOT_DATABASE_URL"] = str(url)
    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(ROOT / "backend/alembic.ini")), revision)
    finally:
        if previous is None:
            os.environ.pop("OFFERPILOT_DATABASE_URL", None)
        else:
            os.environ["OFFERPILOT_DATABASE_URL"] = previous
        get_settings.cache_clear()


@contextmanager
def isolated_database(name_prefix="test", *, migrate_head=True):
    admin = os.environ.get("OFFERPILOT_POSTGRES_ADMIN_URL")
    app = os.environ.get("OFFERPILOT_POSTGRES_APP_URL")
    if not admin or not app:
        raise unittest.SkipTest("Real PostgreSQL S5 URLs not configured")
    admin_url, app_url = make_url(admin), make_url(app)
    if any(u.get_backend_name() != "postgresql" or not (u.database or "").startswith("offerpilot_s5_")
           or u.host not in {"127.0.0.1", "localhost"} for u in (admin_url, app_url)):
        raise ValueError("Only explicit local S5 PostgreSQL test URLs are accepted")
    if (admin_url.host != app_url.host or admin_url.port != app_url.port or admin_url.username == app_url.username
        or any(url.query or not url.password or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", url.username or "")
               for url in (admin_url, app_url))):
        raise ValueError("S5 requires separate local roles on one port without URL query overrides")
    if not re.fullmatch(r"[a-z_]{1,15}", name_prefix):
        raise ValueError("Invalid test database prefix")
    name = f"offerpilot_s5_{name_prefix}_{uuid4().hex[:16]}"
    manager = build_engine(admin).execution_options(isolation_level="AUTOCOMMIT")
    migration_url = admin_url.set(database=name).render_as_string(hide_password=False)
    database_url = app_url.set(database=name).render_as_string(hide_password=False)
    engine = None
    created = False
    try:
        with manager.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
            conn.execute(text(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC'))
            conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO "{app_url.username}"'))
        if migrate_head:
            migrate(migration_url)
        migration_engine = build_engine(migration_url)
        with migration_engine.begin() as conn:
            conn.execute(text('REVOKE CREATE ON SCHEMA public FROM PUBLIC'))
            conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{app_url.username}"'))
            conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{app_url.username}"'))
            conn.execute(text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_url.username}"'))
            if migrate_head:
                conn.execute(text(f'REVOKE INSERT, UPDATE, DELETE ON TABLE alembic_version FROM "{app_url.username}"'))
        migration_engine.dispose()
        engine = build_engine(database_url)
        class Database:
            pass
        db = Database()
        db.name, db.database_url, db.migration_url = name, database_url, migration_url
        db.engine = engine
        db.session_factory = sessionmaker(bind=engine, autoflush=False)
        yield db
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if created:
                # Block reconnects from late test workers before terminating
                # only this fixture's own application-role backends.
                with manager.connect() as conn:
                    conn.execute(text(f'REVOKE CONNECT ON DATABASE "{name}" FROM "{app_url.username}"'))
                # The migration role intentionally cannot terminate another
                # role's sessions. Close only this app role's connections to
                # the freshly created random database through its own login.
                cleanup_engine = build_engine(app)
                try:
                    with cleanup_engine.begin() as conn:
                        conn.execute(text("""SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity
                            WHERE datname = :database_name AND usename = current_user
                              AND pid <> pg_backend_pid()"""), {"database_name": name})
                finally:
                    cleanup_engine.dispose()
                with manager.connect() as conn:
                    # History-upgrade tests can retain a migration-role pool
                    # until TestCase cleanup. The creator may terminate its
                    # own role's connections without pg_signal_backend too.
                    conn.execute(text("""SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity
                        WHERE datname = :database_name AND usename = current_user
                          AND pid <> pg_backend_pid()"""), {"database_name": name})
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        finally:
            manager.dispose()


class PostgresTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="offerpilot-s5-")
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        db = self.enterContext(isolated_database())
        self.engine, self.session_factory = db.engine, db.session_factory
        self.database_url, self.migration_url = db.database_url, db.migration_url
        self.settings = Settings(_env_file=None, database_url=self.database_url,
                                 upload_dir=self.root / "uploads", mock_extraction_enabled=True,
                                 trusted_origins=["http://testserver"],
                                 auth_login_attempt_limit=100, auth_ip_attempt_limit=1000,
                                 auth_registration_attempt_limit=100)
        # This fixture has just created a new empty database/private directory.
        # Explicit initialization must never be copied into a restore path.
        from offerpilot_api.account_lifecycle.ledger import DeletionLedger
        DeletionLedger(self.settings.deletion_ledger_dir).initialize()
        self.storage = LocalResumeStorage(self.settings.upload_dir)
