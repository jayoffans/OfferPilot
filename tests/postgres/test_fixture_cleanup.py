"""Fixture cleanup must wait for its app-role connections without extra grants."""

import os
import unittest

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from offerpilot_api.db.session import build_engine
from tests.postgres.support import isolated_database


class PostgreSQLFixtureCleanupTests(unittest.TestCase):
    def test_cleanup_terminates_own_open_connection_and_drops_only_created_database(self):
        with isolated_database("cleanup") as db:
            name = db.name
            leaked_engine = build_engine(db.database_url)
            self.addCleanup(leaked_engine.dispose)
            connection = leaked_engine.connect()
            self.addCleanup(connection.close)
            self.assertEqual(connection.scalar(text("SELECT current_database()")), name)
            migration_engine = build_engine(db.migration_url)
            self.addCleanup(migration_engine.dispose)
            migration_connection = migration_engine.connect()
            self.addCleanup(migration_connection.close)
            self.assertEqual(migration_connection.scalar(text("SELECT current_database()")), name)

        # The deliberately retained checkout no longer has a live backend.
        with self.assertRaises(DBAPIError):
            connection.execute(text("SELECT 1"))
        with self.assertRaises(DBAPIError):
            migration_connection.execute(text("SELECT 1"))
        manager = build_engine(os.environ["OFFERPILOT_POSTGRES_ADMIN_URL"])
        self.addCleanup(manager.dispose)
        with manager.connect() as check:
            self.assertEqual(check.scalar(text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                                          {"name": name}), 0)
