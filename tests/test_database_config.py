"""Safe configuration, explicit production database and bounded pools."""
import unittest
from pydantic import ValidationError
from offerpilot_api.core.config import Settings
from offerpilot_api.db.session import build_engine, prepare_database_url


class DatabaseConfigurationTests(unittest.TestCase):
    def test_development_keeps_lightweight_sqlite(self):
        self.assertTrue(Settings(_env_file=None).database_url.startswith("sqlite:"))

    def test_production_rejects_implicit_or_explicit_sqlite(self):
        for changes in ({}, {"database_url": "sqlite:///:memory:"}):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, environment="production", **changes)

    def test_postgres_url_uses_pinned_psycopg(self):
        config = Settings(_env_file=None, environment="production",
                          database_url="postgresql://app:synthetic@localhost/offerpilot")
        self.assertTrue(config.database_url.startswith("postgresql+psycopg:"))
        self.assertEqual(prepare_database_url(config.database_url).drivername, "postgresql+psycopg")

    def test_superuser_and_incomplete_production_config_fail_closed(self):
        for url in ("postgresql://postgres:synthetic@localhost/db", "postgresql://app@localhost/db"):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, environment="production", database_url=url)

    def test_validation_does_not_echo_secrets(self):
        secret = "synthetic-private-value"
        with self.assertRaises(ValidationError) as caught:
            Settings(_env_file=None, database_url=f"mysql://app:{secret}@localhost/db")
        self.assertNotIn(secret, str(caught.exception))

    def test_pool_is_bounded_and_sql_parameters_hidden(self):
        engine = build_engine("postgresql://app:synthetic@127.0.0.1/test")
        self.addCleanup(engine.dispose)
        self.assertEqual(engine.pool.size(), 5)
        self.assertEqual(engine.pool._max_overflow, 5)
        self.assertTrue(engine.hide_parameters)
        self.assertFalse(engine.echo)

    def test_invalid_pool_configuration_is_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, database_pool_size=0)
