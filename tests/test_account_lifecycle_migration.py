"""0010 appends only lifecycle tables and refuses loss of erasure receipts."""
import unittest
from uuid import uuid4
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, MetaData, inspect, select, text
from sqlalchemy.orm import Session
import test_trusted_profile_migration as harness
from offerpilot_api.db.models import AccountDeletion, AccountExport, Base, ResumeDocument


class AccountLifecycleMigrationTests(unittest.TestCase):
    setUp = harness.TrustedProfileMigrationTests.setUp
    tearDown = harness.TrustedProfileMigrationTests.tearDown
    upgrade = harness.TrustedProfileMigrationTests.upgrade
    owner = harness.TrustedProfileMigrationTests.owner
    @staticmethod
    def snapshot(connection, tables):
        return {name: [dict(row) for row in connection.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
                for name, table in tables.items()}

    def test_0009_history_is_preserved_and_new_tables_match_models(self):
        self.upgrade("0009_application_tracker")
        owner = self.owner()
        with Session(self.engine) as db:
            db.add_all([ResumeDocument(user_id=owner, file_name="合成旧数据.pdf", file_type="application/pdf", file_path="", raw_text="保留"),
                        ResumeDocument(file_name="legacy.pdf", file_type="application/pdf", file_path="unowned")])
            db.commit()
        metadata = MetaData()
        metadata.reflect(self.engine)
        tables = {name: table for name, table in metadata.tables.items() if name != "alembic_version"}
        with self.engine.connect() as conn:
            before = self.snapshot(conn, tables)
        self.upgrade()
        with self.engine.connect() as conn:
            self.assertEqual(before, self.snapshot(conn, tables))
            self.assertEqual(conn.execute(text("PRAGMA foreign_key_check")).all(), [])
            self.assertEqual(conn.scalar(text("SELECT version_num FROM alembic_version")), "0010_account_lifecycle")
        inspector = inspect(self.engine)
        for name in ("account_exports", "account_deletions"):
            model = Base.metadata.tables[name]
            self.assertEqual({row["name"]: row["nullable"] for row in inspector.get_columns(name)},
                             {column.name: column.nullable for column in model.columns})
            self.assertEqual({row["name"]: " ".join(row["sqltext"].split()) for row in inspector.get_check_constraints(name)},
                             {row.name: " ".join(str(row.sqltext).split()) for row in model.constraints if isinstance(row, CheckConstraint)})
            self.assertEqual({row["name"] for row in inspector.get_indexes(name)}, {row.name for row in model.indexes})

    def test_deletion_receipt_blocks_downgrade_even_after_user_removed(self):
        self.upgrade()
        with Session(self.engine) as db:
            db.add(AccountDeletion(user_id=uuid4(), idempotency_digest="a" * 64, status="completed"))
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "Lifecycle receipts"):
            command.downgrade(self.config, "0009_application_tracker")

    def test_empty_downgrade_and_forward_upgrade_are_safe(self):
        self.upgrade()
        command.downgrade(self.config, "0009_application_tracker")
        self.assertNotIn("account_deletions", inspect(self.engine).get_table_names())
        self.upgrade()
        self.assertEqual(ScriptDirectory.from_config(self.config).get_current_head(), "0010_account_lifecycle")

    def test_storage_configuration_cannot_include_registry_in_restorable_objects(self):
        from pathlib import Path
        from pydantic import ValidationError
        from offerpilot_api.core.config import Settings
        root = Path(self.temporary_directory.name)
        for values in ({"upload_dir": root / "objects", "deletion_ledger_dir": root / "objects" / "ledger"},
                       {"upload_dir": root / "objects", "export_dir": root / "objects"},
                       {"upload_dir": root / "objects", "deletion_ledger_dir": root}):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, database_url=self.database_url, **values)
