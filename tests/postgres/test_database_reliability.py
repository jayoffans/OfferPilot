"""Bounded PostgreSQL failures, least privilege and sanitized HTTP errors."""

import io
import logging
import socket
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import offerpilot_api.main as main_module
from offerpilot_api.core.config import get_settings
from offerpilot_api.db.models import User
from offerpilot_api.db.session import build_engine, get_db, verify_runtime_database_role
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, new_token
from tests.postgres.support import PostgresTestCase


class DatabaseReliabilityPostgresTests(PostgresTestCase):
    def configured_engine(self, **changes):
        settings = self.settings.model_copy(update=changes)
        engine = build_engine(self.database_url, settings=settings)
        self.addCleanup(engine.dispose)
        return engine

    def client(self, database):
        app = main_module.create_app()
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_settings] = lambda: self.settings
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set(SESSION_COOKIE_NAME, new_token())
        self.addCleanup(client.close)
        return client

    def assert_safe_unavailable(self, response, *secrets):
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "storage_unavailable")
        for value in (*secrets, "Traceback", "SELECT", "postgresql+psycopg", "OperationalError"):
            self.assertNotIn(value, response.text)

    def test_bounded_connection_pool_exhaustion_returns_safe_503(self):
        engine = self.configured_engine(database_pool_size=1, database_max_overflow=0, database_pool_timeout_seconds=1)

        def database():
            with Session(engine) as db:
                yield db

        with engine.connect():
            started = time.monotonic()
            self.assert_safe_unavailable(self.client(database).get("/api/v1/me"))
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.8)
            self.assertLess(elapsed, 4)
        with engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT 1")), 1)
        self.assertEqual(engine.pool.size(), 1)
        self.assertEqual(engine.pool._max_overflow, 0)

    def test_statement_timeout_rolls_back_and_connection_remains_usable(self):
        engine = self.configured_engine(database_statement_timeout_ms=100, database_lock_timeout_ms=100)
        with engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SHOW statement_timeout")), "100ms")
            self.assertEqual(connection.scalar(text("SHOW lock_timeout")), "100ms")
            self.assertEqual(connection.scalar(text("SHOW TimeZone")), "UTC")
            self.assertEqual(connection.scalar(text("SHOW idle_in_transaction_session_timeout")), "30s")
            started = time.monotonic()
            with self.assertRaises(DBAPIError) as caught:
                connection.execute(text("SELECT pg_sleep(2)"))
            self.assertEqual(caught.exception.orig.sqlstate, "57014")
            self.assertLess(time.monotonic() - started, 1.5)
            connection.rollback()
            self.assertEqual(connection.scalar(text("SELECT 1")), 1)

    def test_lock_timeout_does_not_partially_update_or_poison_pool(self):
        engine = self.configured_engine(database_statement_timeout_ms=2000, database_lock_timeout_ms=100)
        with self.session_factory() as db:
            user = User(email_normalized="lock-timeout@example.com", password_hash="synthetic-no-login")
            db.add(user)
            db.commit()
            user_id = user.id
        with self.session_factory() as holder:
            holder.execute(select(User.id).where(User.id == user_id).with_for_update())
            with engine.connect() as connection:
                started = time.monotonic()
                with self.assertRaises(DBAPIError) as caught:
                    connection.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": user_id})
                self.assertEqual(caught.exception.orig.sqlstate, "55P03")
                self.assertLess(time.monotonic() - started, 1.5)
                connection.rollback()
                self.assertTrue(connection.scalar(select(User.is_active).where(User.id == user_id)))
        with self.session_factory() as db:
            self.assertTrue(db.get(User, user_id).is_active)

    def test_unavailable_local_database_returns_503_without_credentials(self):
        # Keep the chosen port reserved but not listening. No running server or
        # another test database is interrupted to exercise this failure path.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("127.0.0.1", 0))
            url = make_url(self.database_url).set(port=reserved.getsockname()[1], password="synthetic-secret-never-disclosed")
            engine = build_engine(url.render_as_string(hide_password=False),
                                  settings=self.settings.model_copy(update={"database_connect_timeout_seconds": 1}))
            self.addCleanup(engine.dispose)

            def database():
                with Session(engine) as db:
                    yield db

            started = time.monotonic()
            response = self.client(database).get("/api/v1/me")
            self.assert_safe_unavailable(response, "synthetic-secret-never-disclosed", url.username)
            self.assertLess(time.monotonic() - started, 5)
            with self.assertRaisesRegex(RuntimeError, "数据库安全检查失败") as caught:
                verify_runtime_database_role(engine)
            self.assertNotIn("synthetic-secret-never-disclosed", str(caught.exception))

    def test_database_error_with_sensitive_server_detail_is_not_logged_or_returned(self):
        marker = "SYNTHETIC_PRIVATE_SQL_PARAMETER"

        def database():
            with self.session_factory() as db:
                # PostgreSQL itself includes this invalid value in its error
                # detail. The HTTP boundary must still return only a safe code.
                db.execute(text("SELECT CAST(:value AS integer)"), {"value": marker})
                yield db

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger()
        logger.addHandler(handler)
        try:
            response = self.client(database).get("/api/v1/me")
        finally:
            logger.removeHandler(handler)
        self.assert_safe_unavailable(response, marker)
        self.assertNotIn(marker, stream.getvalue())
        self.assertTrue(self.engine.hide_parameters)
        self.assertFalse(self.engine.echo)
        self.assertNotIn(make_url(self.database_url).password, repr(self.settings))

    def test_application_can_read_but_cannot_change_migration_revision(self):
        with self.engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            self.assertTrue(revision)
            with self.assertRaises(DBAPIError) as caught:
                connection.execute(text("UPDATE alembic_version SET version_num = version_num"))
            self.assertEqual(caught.exception.orig.sqlstate, "42501")
            connection.rollback()
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), revision)

    def test_production_startup_accepts_app_and_rejects_custom_privileged_role(self):
        verify_runtime_database_role(self.engine)
        migrator = build_engine(self.migration_url)
        self.addCleanup(migrator.dispose)
        self.assertNotEqual(make_url(self.migration_url).username, "postgres")
        with self.assertRaisesRegex(RuntimeError, "数据库安全检查失败"):
            verify_runtime_database_role(migrator)
        production = self.settings.model_copy(update={"environment": "production", "mock_extraction_enabled": False})
        with patch.object(main_module, "get_settings", return_value=production), patch.object(main_module, "engine", self.engine):
            with TestClient(main_module.create_app()) as client:
                self.assertEqual(client.get("/health").status_code, 200)
        with patch.object(main_module, "get_settings", return_value=production), patch.object(main_module, "engine", migrator):
            with self.assertRaisesRegex(RuntimeError, "数据库安全检查失败"):
                with TestClient(main_module.create_app()):
                    self.fail("A migration role must never start the production app")

    def test_runtime_role_check_rejects_schema_create_and_object_ownership(self):
        migrator = build_engine(self.migration_url)
        self.addCleanup(migrator.dispose)
        role = make_url(self.database_url).username
        quoted_role = migrator.dialect.identifier_preparer.quote_identifier(role)
        with migrator.begin() as connection:
            connection.execute(text(f"GRANT CREATE ON SCHEMA public TO {quoted_role}"))
        with self.assertRaisesRegex(RuntimeError, "数据库安全检查失败"):
            verify_runtime_database_role(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE synthetic_owned_probe (id integer)"))
        with migrator.begin() as connection:
            connection.execute(text(f"REVOKE CREATE ON SCHEMA public FROM {quoted_role}"))
        with self.assertRaisesRegex(RuntimeError, "数据库安全检查失败"):
            verify_runtime_database_role(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE synthetic_owned_probe"))
        verify_runtime_database_role(self.engine)
