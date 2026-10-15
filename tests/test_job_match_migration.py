"""0008 append-only upgrade, input preservation and safe downgrade boundaries."""

import unittest
from uuid import uuid4

from alembic import command
from sqlalchemy import CheckConstraint, MetaData, UniqueConstraint, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import test_job_migrations as harness
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import CareerProfile, JobMatch, JobMatchReceipt


PREVIOUS = "0007_private_jobs"
CURRENT = "0008_trusted_job_matches"


class JobMatchMigrationTests(unittest.TestCase):
    setUp = harness.JobMigrationTests.setUp
    tearDown = harness.JobMigrationTests.tearDown
    upgrade = harness.JobMigrationTests.upgrade
    legacy_0004 = harness.JobMigrationTests.legacy_0004
    legacy_0006 = harness.JobMigrationTests.legacy_0006
    seed_job = harness.JobMigrationTests.seed_job
    snapshot = staticmethod(harness.JobMigrationTests.snapshot)

    def legacy_0007(self):
        _, _, owner = self.legacy_0006()
        self.upgrade(PREVIOUS)
        job_id, *_ = self.seed_job(owner)
        metadata = MetaData()
        metadata.reflect(bind=self.engine)
        tables = {name: table for name, table in metadata.tables.items() if name != "alembic_version"}
        with self.engine.connect() as db:
            original = self.snapshot(db, tables)
        return owner, job_id, tables, original

    def test_upgrade_preserves_all_0007_profile_candidates_jobs_evidence_and_revisions(self):
        owner, job_id, tables, original = self.legacy_0007()
        self.upgrade()
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), CURRENT)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
            self.assertEqual(db.scalar(text("SELECT preferences FROM career_profiles")), "{}")
            self.assertEqual(db.scalar(text("SELECT count(*) FROM job_matches")), 0)
            self.assertEqual(db.scalar(text("SELECT count(*) FROM job_match_receipts")), 0)

    def test_empty_upgrade_and_downgrade_preserve_old_fk_parent_rows(self):
        _, _, tables, original = self.legacy_0007()
        self.upgrade()
        command.downgrade(self.config, PREVIOUS)
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
        self.assertFalse({"job_matches", "job_match_receipts"} & set(inspect(self.engine).get_table_names()))
        self.assertNotIn("preferences", {row["name"] for row in inspect(self.engine).get_columns("career_profiles")})
        self.upgrade()

    def test_empty_database_matches_orm_checks_fk_indexes_unique_and_defaults(self):
        self.upgrade()
        inspector = inspect(self.engine)
        for name in ("job_matches", "job_match_receipts"):
            table = Base.metadata.tables[name]
            self.assertEqual({row["name"] for row in inspector.get_columns(name)}, set(table.columns.keys()))
            self.assertEqual({row["name"]: " ".join(row["sqltext"].split()) for row in inspector.get_check_constraints(name)},
                {row.name: " ".join(str(row.sqltext).split()) for row in table.constraints if isinstance(row, CheckConstraint)})
            self.assertEqual({tuple(row["column_names"]) for row in inspector.get_unique_constraints(name)},
                {tuple(column.name for column in row.columns) for row in table.constraints if isinstance(row, UniqueConstraint)})
            self.assertEqual({row["name"] for row in inspector.get_indexes(name)}, {row.name for row in table.indexes})
            actual = {(tuple(row["constrained_columns"]), row["referred_table"], row["options"].get("ondelete")) for row in inspector.get_foreign_keys(name)}
            expected = {((fk.parent.name,), fk.column.table.name, fk.ondelete) for fk in table.foreign_keys}
            self.assertEqual(actual, expected)

    def seed_match(self, owner, saved_job_id, **changes):
        values = {"user_id": owner, "job_id": saved_job_id, "profile_revision": 8, "job_revision": 1,
                  "jd_version": 1, "rule_version": "synthetic-rules-1", "schema_version": "1.0", "evaluation": {"synthetic": True}}
        with Session(self.engine) as db:
            result = JobMatch(**{**values, **changes})
            db.add(result)
            db.commit()
            return result.id

    def test_match_data_refuses_loss_without_modifying_old_data(self):
        owner, job_id, tables, original = self.legacy_0007()
        self.upgrade()
        result_id = self.seed_match(owner, job_id)
        with self.assertRaisesRegex(RuntimeError, "Trusted match data exists"):
            command.downgrade(self.config, PREVIOUS)
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), CURRENT)
            self.assertEqual(self.snapshot(db, tables), original)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(JobMatch, result_id))

    def test_confirmed_preferences_refuse_destructive_downgrade(self):
        owner, _, _, _ = self.legacy_0007()
        self.upgrade()
        with Session(self.engine) as db:
            profile = db.scalar(select(CareerProfile).where(CareerProfile.user_id == owner))
            profile.preferences = {"locations": ["上海"]}
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "Confirmed preferences exist"):
            command.downgrade(self.config, PREVIOUS)
        self.assertIn("job_matches", inspect(self.engine).get_table_names())

    def test_database_blocks_zero_versions_missing_owner_and_duplicate_basis(self):
        owner, job_id, _, _ = self.legacy_0007()
        self.upgrade()
        self.seed_match(owner, job_id)
        for changes in ({"profile_revision": 0}, {"job_revision": 0}, {"jd_version": 0},
                        {"user_id": uuid4()}, {"job_id": uuid4()}, {}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError):
                self.seed_match(owner, job_id, **changes)

    def test_receipt_fk_and_owner_idempotency_uniqueness(self):
        owner, job_id, _, _ = self.legacy_0007()
        self.upgrade()
        result_id = self.seed_match(owner, job_id)
        values = {"user_id": owner, "job_id": job_id, "match_id": result_id,
                  "idempotency_key": "once", "request_digest": "a" * 64}
        with Session(self.engine) as db:
            db.add(JobMatchReceipt(**values))
            db.commit()
        for changes in ({}, {"user_id": uuid4()}, {"match_id": uuid4()}, {"job_id": uuid4()}):
            with self.subTest(changes=changes), Session(self.engine) as db:
                db.add(JobMatchReceipt(**{**values, **changes}))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()

    def test_offline_downgrade_cannot_bypass_data_safety(self):
        with self.assertRaisesRegex(RuntimeError, "online data safety check"):
            command.downgrade(self.config, f"{CURRENT}:{PREVIOUS}", sql=True)


if __name__ == "__main__":
    unittest.main()
