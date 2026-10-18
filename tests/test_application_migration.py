"""0009 preservation, real FK/constraints and non-destructive rollback checks."""

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, MetaData, UniqueConstraint, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import test_job_match_migration as harness
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import Application, ApplicationEvent, ApplicationReceipt, CareerProfile, JobMatch, JobMatchReceipt, SavedJob


PREVIOUS = "0008_trusted_job_matches"
CURRENT = "0009_application_tracker"
TABLES = {"applications", "application_events", "application_receipts"}


class ApplicationMigrationTests(unittest.TestCase):
    setUp = harness.JobMatchMigrationTests.setUp
    tearDown = harness.JobMatchMigrationTests.tearDown
    upgrade = harness.JobMatchMigrationTests.upgrade
    legacy_0004 = harness.JobMatchMigrationTests.legacy_0004
    legacy_0006 = harness.JobMatchMigrationTests.legacy_0006
    legacy_0007 = harness.JobMatchMigrationTests.legacy_0007
    seed_job = harness.JobMatchMigrationTests.seed_job
    seed_match = harness.JobMatchMigrationTests.seed_match
    snapshot = staticmethod(harness.JobMatchMigrationTests.snapshot)

    def legacy_0008(self):
        owner, job_id, _, _ = self.legacy_0007()
        self.upgrade(PREVIOUS)
        match_id = self.seed_match(owner, job_id)
        with Session(self.engine) as db:
            db.scalar(select(CareerProfile).where(CareerProfile.user_id == owner)).preferences = {"locations": ["上海"], "weekly_days": 3}
            db.add(JobMatchReceipt(user_id=owner, job_id=job_id, match_id=match_id,
                                   idempotency_key="legacy-match", request_digest="b" * 64))
            db.commit()
        metadata = MetaData()
        metadata.reflect(bind=self.engine)
        tables = {name: table for name, table in metadata.tables.items() if name != "alembic_version"}
        with self.engine.connect() as db:
            original = self.snapshot(db, tables)
        return owner, job_id, match_id, tables, original

    def seed_application(self, owner, saved_job_id, **changes):
        values = {"user_id": owner, "job_id": saved_job_id, "job_title": "合成申请岗位", "company": "合成公司",
                  "location": "上海", "attempt_number": 1, "status": "preparing", "active_slot": 1,
                  "revision": 1, "notes": "合成申请的私有备注"}
        with Session(self.engine) as db:
            application = Application(**{**values, **changes})
            db.add(application)
            db.commit()
            return application.id

    def test_migration_is_single_forward_revision_from_0008(self):
        scripts = ScriptDirectory.from_config(self.config)
        self.assertEqual(scripts.get_revision("0010_account_lifecycle").down_revision, CURRENT)
        self.assertEqual(scripts.get_revision(CURRENT).down_revision, PREVIOUS)

    def test_upgrade_preserves_every_0008_value_owner_revision_evidence_match_and_preference(self):
        _, _, _, tables, original = self.legacy_0008()
        self.upgrade()
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), ScriptDirectory.from_config(self.config).get_current_head())
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
            for name in TABLES:
                self.assertEqual(db.scalar(select(text("count(*)")).select_from(Base.metadata.tables[name])), 0)

    def test_empty_upgrade_matches_orm_columns_nullability_checks_indexes_uniqueness_and_fks(self):
        self.upgrade()
        inspector = inspect(self.engine)
        for name in TABLES:
            with self.subTest(table=name):
                model = Base.metadata.tables[name]
                self.assertEqual({row["name"] for row in inspector.get_columns(name)}, set(model.columns.keys()))
                self.assertEqual({row["name"]: row["nullable"] for row in inspector.get_columns(name)},
                                 {column.name: column.nullable for column in model.columns})
                self.assertEqual({row["name"]: " ".join(row["sqltext"].split()) for row in inspector.get_check_constraints(name)},
                                 {row.name: " ".join(str(row.sqltext).split()) for row in model.constraints if isinstance(row, CheckConstraint)})
                self.assertEqual({(row["name"], tuple(row["column_names"])) for row in inspector.get_unique_constraints(name)},
                                 {(row.name, tuple(column.name for column in row.columns)) for row in model.constraints if isinstance(row, UniqueConstraint)})
                self.assertEqual({row["name"] for row in inspector.get_indexes(name)}, {row.name for row in model.indexes})
                actual_fks = {(tuple(row["constrained_columns"]), row["referred_table"], tuple(row["referred_columns"]), row["options"].get("ondelete")) for row in inspector.get_foreign_keys(name)}
                model_fks = {((fk.parent.name,), fk.column.table.name, (fk.column.name,), fk.ondelete) for fk in model.foreign_keys}
                self.assertEqual(actual_fks, model_fks)
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(text("PRAGMA foreign_keys")), 1)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_empty_downgrade_and_reupgrade_preserve_0008_history_and_preferences(self):
        _, _, _, tables, original = self.legacy_0008()
        self.upgrade()
        command.downgrade(self.config, PREVIOUS)
        self.assertFalse(TABLES & set(inspect(self.engine).get_table_names()))
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), PREVIOUS)
        self.upgrade()
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_application_history_refuses_destructive_downgrade_and_preserves_prior_data(self):
        owner, job_id, match_id, tables, original = self.legacy_0008()
        self.upgrade()
        application_id = self.seed_application(owner, job_id, match_id=match_id)
        with self.assertRaisesRegex(RuntimeError, "Application data or retry receipts exist"):
            command.downgrade(self.config, PREVIOUS)
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), CURRENT)
            self.assertEqual(self.snapshot(db, tables), original)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(Application, application_id))

    def test_digest_only_tombstone_alone_prevents_unsafe_downgrade(self):
        owner, _, _, tables, original = self.legacy_0008()
        self.upgrade()
        with Session(self.engine) as db:
            db.add(ApplicationReceipt(user_id=owner, application_id=None, idempotency_key="deleted-history",
                                      request_digest="a" * 64, operation="delete"))
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "Application data or retry receipts exist"):
            command.downgrade(self.config, PREVIOUS)
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), CURRENT)
            self.assertEqual(self.snapshot(db, tables), original)
        self.assertEqual(set(Base.metadata.tables["application_receipts"].columns.keys()),
                         {"id", "user_id", "application_id", "idempotency_key", "request_digest", "operation", "created_at"})

    def test_invalid_status_versions_active_slots_actions_and_foreign_ids_fail_at_database(self):
        owner, job_id, _, _, _ = self.legacy_0008()
        self.upgrade()
        invalid = [{"status": "accepted"}, {"revision": 0}, {"attempt_number": 0},
                   {"status": "preparing", "active_slot": None}, {"status": "rejected", "active_slot": 1},
                   {"active_slot": 2}, {"user_id": uuid4()}, {"job_id": uuid4()}, {"match_id": uuid4()},
                   {"action_title": "不完整行动"}, {"action_due_time": "09:00"},
                   {"action_title": "非法状态", "action_notes": "", "action_status": "sent"}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError):
                self.seed_application(owner, job_id, **changes)
        with Session(self.engine) as db:
            self.assertEqual(db.scalars(select(Application)).all(), [])

    def test_database_permits_terminal_attempts_but_blocks_duplicate_active_slot_and_attempt(self):
        owner, job_id, _, _, _ = self.legacy_0008()
        self.upgrade()
        self.seed_application(owner, job_id, status="rejected", active_slot=None)
        self.seed_application(owner, job_id, attempt_number=2, status="withdrawn", active_slot=None)
        self.seed_application(owner, job_id, attempt_number=3)
        for changes in ({"attempt_number": 4}, {"attempt_number": 1, "status": "closed", "active_slot": None}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError):
                self.seed_application(owner, job_id, **changes)
        with Session(self.engine) as db:
            self.assertEqual(len(db.scalars(select(Application)).all()), 3)

    def test_job_fk_blocks_history_loss_and_match_fk_can_clear_without_deleting_application(self):
        owner, job_id, match_id, _, _ = self.legacy_0008()
        self.upgrade()
        application_id = self.seed_application(owner, job_id, match_id=match_id)
        with Session(self.engine) as db:
            db.delete(db.get(SavedJob, job_id))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            db.delete(db.get(JobMatch, match_id))
            db.commit()
            db.expire_all()
            application = db.get(Application, application_id)
            self.assertIsNotNone(application)
            self.assertIsNone(application.match_id)
            self.assertEqual(application.notes, "合成申请的私有备注")

    def test_event_fk_constraints_uniqueness_and_application_delete_clear_private_history(self):
        owner, job_id, _, _, _ = self.legacy_0008()
        self.upgrade()
        application_id = self.seed_application(owner, job_id)
        now = datetime.now(timezone.utc)
        event_values = {"user_id": owner, "application_id": application_id, "revision": 1, "type": "application_created",
                        "changes": {"before": None, "after": "合成私有事件"}, "occurred_at": now, "recorded_at": now}
        with Session(self.engine) as db:
            db.add(ApplicationEvent(**event_values))
            db.add(ApplicationReceipt(user_id=owner, application_id=application_id, idempotency_key="history-create", request_digest="c" * 64, operation="create"))
            db.commit()
        for changes in ({}, {"revision": 0}, {"type": "automatic_application"}, {"user_id": uuid4()}, {"application_id": uuid4()}):
            with self.subTest(changes=changes), Session(self.engine) as db:
                db.add(ApplicationEvent(**{**event_values, **changes}))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()
        with Session(self.engine) as db:
            db.delete(db.get(Application, application_id))
            db.commit()
            self.assertEqual(db.scalars(select(ApplicationEvent)).all(), [])
            receipt = db.scalar(select(ApplicationReceipt))
            self.assertIsNotNone(receipt)
            self.assertIsNone(receipt.application_id)
            self.assertEqual(receipt.request_digest, "c" * 64)
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_receipt_owner_request_unique_operation_and_fk_are_enforced(self):
        owner, job_id, _, _, _ = self.legacy_0008()
        self.upgrade()
        application_id = self.seed_application(owner, job_id)
        values = {"user_id": owner, "application_id": application_id, "idempotency_key": "unique-request", "request_digest": "a" * 64, "operation": "update"}
        with Session(self.engine) as db:
            db.add(ApplicationReceipt(**values))
            db.commit()
        for changes in ({}, {"user_id": uuid4()}, {"application_id": uuid4(), "idempotency_key": "missing-app"}, {"operation": "send", "idempotency_key": "bad-op"}):
            with self.subTest(changes=changes), Session(self.engine) as db:
                db.add(ApplicationReceipt(**{**values, **changes}))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()

    def test_offline_downgrade_cannot_bypass_history_safety(self):
        with self.assertRaisesRegex(RuntimeError, "online data safety check"):
            command.downgrade(self.config, f"{CURRENT}:{PREVIOUS}", sql=True)


if __name__ == "__main__":
    unittest.main()
