"""S3 append-only migrations preserve S1/S2 facts and refuse job data loss."""

import unittest
from uuid import uuid4

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, MetaData, UniqueConstraint, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.db.base import Base
from offerpilot_api.db.models import JobCreateReceipt, JobJDSnapshot, JobRequirement, SavedJob, User
import test_extraction_migration as harness


PREVIOUS_REVISION = "0006_deepseek_byok"


class JobMigrationTests(unittest.TestCase):
    setUp = harness.ExtractionMigrationTests.setUp
    tearDown = harness.ExtractionMigrationTests.tearDown
    upgrade = harness.ExtractionMigrationTests.upgrade
    legacy_0004 = harness.ExtractionMigrationTests.legacy_0004
    @staticmethod
    def snapshot(connection, tables):
        return {name: [dict(row) for row in connection.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
                for name, table in tables.items()}

    def legacy_0006(self):
        _, _, owner, resume, candidate = self.legacy_0004()
        self.upgrade(PREVIOUS_REVISION)
        metadata = MetaData()
        metadata.reflect(bind=self.engine)
        tables = {name: table for name, table in metadata.tables.items() if name != "alembic_version"}
        rid, reference = uuid4().hex, uuid4().hex
        with self.engine.begin() as db:
            # This is a synthetic historical consent/usage row. No key/provider
            # invocation is needed to verify preservation of stored S2 history.
            db.execute(tables["resume_extraction_runs"].insert(), {
                "id": rid, "user_id": owner.hex, "resume_id": resume.hex,
                "idempotency_key": "legacy-consented-history", "source_version": "a" * 64,
                "mode": "deepseek", "test_result": False, "max_attempts": 1,
                "max_calls": 1, "timeout_seconds": 60, "schema_version": "2.0",
                "extraction_version": "legacy-v2", "prompt_version": "resume_extract_v2.1",
                "provider_version": "deepseek-openai-json-v1.1", "model": "deepseek-flash",
                "session_id": reference, "key_reference": reference,
                "authorization_id": reference, "sent_text_sha256": "b" * 64,
                "consent_version": "consent-v1", "billing_status": "usage_reported",
                "prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50,
                "status": "succeeded", "attempt_count": 1, "call_count": 1,
            })
            db.execute(text("UPDATE profile_candidates SET extraction_run_id=:rid WHERE id=:cid"),
                       {"rid": rid, "cid": candidate.hex})
            original = self.snapshot(db, tables)
        return tables, original, owner

    def job_tables(self):
        return {"saved_jobs", "job_jd_snapshots", "job_requirements", "job_create_receipts"}

    def seed_job(self, owner):
        with Session(self.engine) as db:
            job = SavedJob(user_id=owner, title="合成后端实习岗位", company="示例科技", work_type="internship",
                           source_kind="pasted", content_digest="c" * 64)
            db.add(job)
            db.flush()
            snapshot = JobJDSnapshot(job_id=job.id, version=1, jd_text="必须掌握 Python", text_sha256="d" * 64)
            db.add(snapshot)
            db.flush()
            requirement = JobRequirement(job_id=job.id, snapshot_id=snapshot.id, type="technical_skill",
                                         content="必须掌握 Python", priority="must", review_status="pending",
                                         evidence_text="必须掌握 Python", source_start=0, source_end=12,
                                         source_kind="rule", position=0, parser_version="job-rules-v1")
            receipt = JobCreateReceipt(user_id=owner, job_id=job.id, idempotency_key="create-once", request_digest="e" * 64)
            db.add_all([requirement, receipt])
            db.commit()
            return job.id, snapshot.id, requirement.id, receipt.id

    def test_upgrade_preserves_all_s1_s2_values_ownership_evidence_and_consent(self):
        tables, original, _ = self.legacy_0006()
        self.upgrade()
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")),
                             ScriptDirectory.from_config(self.config).get_current_head())
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
            self.assertTrue(self.job_tables())
            for name in self.job_tables():
                self.assertIsNone(db.scalar(select(Base.metadata.tables[name]).limit(1)))
            # Existing candidate evidence is still pending; new jobs do not
            # silently alter the official profile or confirm model output.
            self.assertEqual(db.scalar(text("SELECT revision FROM career_profiles")), 8)
            self.assertEqual(db.scalar(text("SELECT status FROM profile_candidates")), "pending")

    def test_empty_job_tables_can_downgrade_without_touching_s2_history(self):
        tables, original, _ = self.legacy_0006()
        self.upgrade()
        job_names = self.job_tables()
        command.downgrade(self.config, PREVIOUS_REVISION)
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), PREVIOUS_REVISION)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
        self.assertFalse(job_names & set(inspect(self.engine).get_table_names()))
        self.upgrade()
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)

    def test_job_checks_indexes_foreign_keys_and_uniqueness_match_metadata(self):
        self.upgrade()
        inspector = inspect(self.engine)
        self.assertTrue(self.job_tables())
        for name in self.job_tables():
            with self.subTest(table=name):
                model = Base.metadata.tables[name]
                checks = {constraint.name: " ".join(str(constraint.sqltext).split())
                          for constraint in model.constraints if isinstance(constraint, CheckConstraint)}
                stored = {item["name"]: " ".join(item["sqltext"].split())
                          for item in inspector.get_check_constraints(name)}
                self.assertEqual(stored, checks)
                self.assertEqual({item["name"] for item in inspector.get_indexes(name)},
                                 {index.name for index in model.indexes})
                expected_unique = {(constraint.name, tuple(column.name for column in constraint.columns))
                                   for constraint in model.constraints if isinstance(constraint, UniqueConstraint)}
                actual_unique = {(item["name"], tuple(item["column_names"]))
                                 for item in inspector.get_unique_constraints(name)}
                self.assertEqual(actual_unique, expected_unique)
                expected_foreign_keys = {(tuple(column.name for column in fk.columns),
                                          tuple(element.target_fullname for element in fk.elements), fk.ondelete)
                                         for fk in model.foreign_key_constraints}
                actual_foreign_keys = {(tuple(item["constrained_columns"]),
                                        tuple(f"{item['referred_table']}.{column}" for column in item["referred_columns"]),
                                        item["options"].get("ondelete")) for item in inspector.get_foreign_keys(name)}
                self.assertEqual(actual_foreign_keys, expected_foreign_keys)
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(text("PRAGMA foreign_keys")), 1)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_offline_downgrade_refuses_to_skip_job_data_safety_check(self):
        head = ScriptDirectory.from_config(self.config).get_current_head()
        with self.assertRaisesRegex(RuntimeError, "online data safety check"):
            command.downgrade(self.config, f"{head}:{PREVIOUS_REVISION}", sql=True)

    def test_saved_jobs_refuse_destructive_downgrade_and_preserve_old_history(self):
        tables, original, owner = self.legacy_0006()
        self.upgrade()
        job_id, snapshot_id, requirement_id, receipt_id = self.seed_job(owner)
        with self.assertRaisesRegex(RuntimeError, "[Jj]ob data exists"):
            command.downgrade(self.config, PREVIOUS_REVISION)
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")),
                             ScriptDirectory.from_config(self.config).get_current_head())
        with Session(self.engine) as db:
            self.assertEqual(db.get(SavedJob, job_id).user_id, owner)
            self.assertEqual(db.get(JobJDSnapshot, snapshot_id).jd_text, "必须掌握 Python")
            self.assertEqual(db.get(JobRequirement, requirement_id).review_status, "pending")
            self.assertEqual(db.get(JobCreateReceipt, receipt_id).job_id, job_id)

    def test_delete_cascades_snapshots_and_requirements_but_retains_minimal_replay_tombstone(self):
        tables, original, owner = self.legacy_0006()
        self.upgrade()
        job_id, snapshot_id, requirement_id, receipt_id = self.seed_job(owner)
        with Session(self.engine) as db:
            other = SavedJob(user_id=owner, title="另一合成岗位", work_type="unknown", source_kind="manual", content_digest="f" * 64)
            db.add(other)
            db.commit()
            other_id = other.id
        with self.engine.begin() as db:
            db.execute(delete(SavedJob).where(SavedJob.id == job_id))
        with Session(self.engine) as db:
            self.assertIsNone(db.get(SavedJob, job_id))
            self.assertIsNone(db.get(JobJDSnapshot, snapshot_id))
            self.assertIsNone(db.get(JobRequirement, requirement_id))
            self.assertIsNone(db.get(JobCreateReceipt, receipt_id).job_id)
            self.assertIsNotNone(db.get(SavedJob, other_id))
            self.assertIsNotNone(db.get(User, owner))
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_tombstone_alone_prevents_destructive_downgrade(self):
        _, _, owner = self.legacy_0006()
        self.upgrade()
        job_id, *_ = self.seed_job(owner)
        with self.engine.begin() as db:
            db.execute(delete(SavedJob).where(SavedJob.id == job_id))
        with self.assertRaisesRegex(RuntimeError, "[Jj]ob data exists"):
            command.downgrade(self.config, PREVIOUS_REVISION)
        with Session(self.engine) as db:
            receipts = db.scalars(select(JobCreateReceipt)).all()
            self.assertEqual(len(receipts), 1)
            self.assertIsNone(receipts[0].job_id)

    def test_database_enforces_job_owner_versions_requirement_states_and_complete_evidence(self):
        _, _, owner = self.legacy_0006()
        self.upgrade()
        job_id, snapshot_id, *_ = self.seed_job(owner)
        job_values = dict(user_id=owner, title="合成约束岗位", work_type="unknown", source_kind="manual", content_digest="f" * 64)
        for changed in ({"user_id": uuid4()}, {"user_id": None}, {"revision": 0}, {"jd_version": 0},
                        {"work_type": "invented"}, {"source_kind": "fetched"}, {"content_digest": "c" * 64}):
            with self.subTest(job=changed), Session(self.engine) as db:
                db.add(SavedJob(**{**job_values, **changed}))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()
        requirement_values = dict(job_id=job_id, snapshot_id=snapshot_id, type="technical_skill", content="Python",
                                  priority="must", review_status="pending", source_kind="manual", position=0)
        for changed in ({"job_id": uuid4()}, {"snapshot_id": uuid4()}, {"type": "invented"},
                        {"priority": "certain"}, {"review_status": "automatic"}, {"source_kind": "ai_confirmed"},
                        {"position": -1}, {"revision": 0},
                        {"evidence_text": "Python"},
                        {"evidence_text": "Python", "source_start": 0},
                        {"evidence_text": "Python", "source_end": 6},
                        {"source_start": 0, "source_end": 6},
                        {"evidence_text": "Python", "source_start": -1, "source_end": 6},
                        {"evidence_text": "Python", "source_start": 6, "source_end": 6}):
            with self.subTest(requirement=changed), Session(self.engine) as db:
                db.add(JobRequirement(**{**requirement_values, **changed}))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()
        for changed in ({"version": 0}, {"version": 1}, {"job_id": uuid4(), "version": 2}):
            with self.subTest(snapshot=changed), Session(self.engine) as db:
                db.add(JobJDSnapshot(**{"job_id": job_id, "version": 2, "jd_text": "合成正文", "text_sha256": "a" * 64, **changed}))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()


if __name__ == "__main__":
    unittest.main()
