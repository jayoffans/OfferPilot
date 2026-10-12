"""0005 only appends recoverable Mock state; preserves every S2-1 value."""

import unittest
from uuid import uuid4

from alembic import command
from sqlalchemy import CheckConstraint, MetaData, Table, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.db.base import Base
from offerpilot_api.db.models import CareerProfile, CareerProfileEntry, ProfileCandidate, ResumeDocument, ResumeExtractionRun, User
import test_trusted_profile_migration as migration_harness


class ExtractionMigrationTests(unittest.TestCase):
    setUp = migration_harness.TrustedProfileMigrationTests.setUp
    tearDown = migration_harness.TrustedProfileMigrationTests.tearDown
    upgrade = migration_harness.TrustedProfileMigrationTests.upgrade
    snapshot = staticmethod(migration_harness.TrustedProfileMigrationTests.snapshot)

    def legacy_0004(self):
        self.upgrade("0004_trusted_career_profile")
        metadata = MetaData()
        tables = {name: Table(name, metadata, autoload_with=self.engine) for name in
                  ("users", "resume_documents", "profiles", "profile_facts", "career_profiles", "career_profile_entries", "profile_candidates")}
        owner_id, resume_id, profile_id, entry_id, candidate_id = (uuid4() for _ in range(5))
        with self.engine.begin() as db:
            db.execute(tables["users"].insert(), {"id": owner_id.hex, "email_normalized": "legacy-extract@example.com", "password_hash": "synthetic"})
            db.execute(tables["resume_documents"].insert(), {"id": resume_id.hex, "user_id": owner_id.hex, "file_name": "旧合成.pdf", "file_path": "private/legacy.pdf", "file_type": "application/pdf", "raw_text": "旧合成简历：Python"})
            db.execute(tables["career_profiles"].insert(), {"id": profile_id.hex, "user_id": owner_id.hex, "revision": 8})
            db.execute(tables["career_profile_entries"].insert(), {"id": entry_id.hex, "profile_id": profile_id.hex, "category": "skill", "value": {"name": "Python", "level": None}, "position": 0, "source_kind": "manual", "field_sources": {"name": "manual"}, "evidence": []})
            db.execute(tables["profile_candidates"].insert(), {"id": candidate_id.hex, "profile_id": profile_id.hex, "resume_id": resume_id.hex, "batch_key": "legacy", "item_index": 0, "batch_digest": "a" * 64, "category": "skill", "value": {"name": "Python", "level": None}, "evidence": [{"field": "name", "source_text": "Python"}], "evidence_status": "supported", "status": "pending", "schema_version": "1.0", "extraction_version": "synthetic-v1"})
            original = self.snapshot(db, tables)
        return tables, original, owner_id, resume_id, candidate_id

    def test_0004_upgrade_preserves_values_and_does_not_invent_locations_or_confirm(self):
        tables, original, _, resume_id, candidate_id = self.legacy_0004()
        self.upgrade()
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), "0005_resume_extraction_runs")
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ResumeDocument, resume_id).text_layout)
            self.assertEqual(db.get(ProfileCandidate, candidate_id).extraction_metadata, {})
            self.assertIsNone(db.get(ProfileCandidate, candidate_id).extraction_run_id)
            self.assertEqual(db.scalars(select(ResumeExtractionRun)).all(), [])

    def test_migration_checks_indexes_and_inline_foreign_key_match_models(self):
        self.upgrade()
        inspector = inspect(self.engine)
        expected = {item.name: " ".join(str(item.sqltext).split()) for item in Base.metadata.tables["resume_extraction_runs"].constraints if isinstance(item, CheckConstraint)}
        actual = {item["name"]: " ".join(item["sqltext"].split()) for item in inspector.get_check_constraints("resume_extraction_runs")}
        self.assertEqual(expected, actual)
        with self.engine.connect() as db:
            keys = db.execute(text("PRAGMA foreign_key_list(profile_candidates)")).mappings().all()
            run_key = next(item for item in keys if item["from"] == "extraction_run_id")
            self.assertEqual((run_key["table"], run_key["on_delete"]), ("resume_extraction_runs", "SET NULL"))
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
        self.assertEqual({item["name"] for item in inspector.get_indexes("resume_extraction_runs")},
                         {"ix_resume_extraction_runs_user_id", "ix_resume_extraction_runs_resume_id", "ix_resume_extraction_runs_status"})

    def test_empty_0005_can_downgrade_preserving_existing_s2_1_data(self):
        tables, original, *_ = self.legacy_0004()
        self.upgrade()
        command.downgrade(self.config, "0004_trusted_career_profile")
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
        self.assertNotIn("resume_extraction_runs", inspect(self.engine).get_table_names())

    def test_new_text_locations_refuse_destructive_downgrade(self):
        _, _, _, resume_id, _ = self.legacy_0004()
        self.upgrade()
        with Session(self.engine) as db:
            db.get(ResumeDocument, resume_id).text_layout = {"layout_version": "1.0", "synthetic": True}
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "Extraction data exists"):
            command.downgrade(self.config, "0004_trusted_career_profile")
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), "0005_resume_extraction_runs")

    def test_run_recovery_history_refuses_destructive_downgrade_and_enforces_bounds(self):
        _, _, owner_id, resume_id, _ = self.legacy_0004()
        self.upgrade()
        values = dict(user_id=owner_id, resume_id=resume_id, idempotency_key="migrate-run", source_version="b" * 64,
                      max_attempts=2, max_calls=4, timeout_seconds=30, schema_version="2.0", extraction_version="v2", prompt_version="p2", provider_version="mock")
        with Session(self.engine) as db:
            run = ResumeExtractionRun(**values)
            db.add(run)
            db.commit()
            run_id = run.id
        with self.assertRaisesRegex(RuntimeError, "Extraction data exists"):
            command.downgrade(self.config, "0004_trusted_career_profile")
        for changed in ({"mode": "deepseek"}, {"test_result": False}, {"status": "running"}, {"max_attempts": 3}, {"max_calls": 5}, {"timeout_seconds": 61}, {"attempt_count": -1}, {"call_count": 5}, {"user_id": uuid4()}):
            with self.subTest(changed=changed), Session(self.engine) as db:
                db.add(ResumeExtractionRun(**{**values, "idempotency_key": uuid4().hex, "source_version": uuid4().hex * 2, **changed}))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(ResumeExtractionRun, run_id))

    def test_offline_downgrade_cannot_discard_recovery_or_source_metadata(self):
        with self.assertRaisesRegex(RuntimeError, "online data safety check"):
            command.downgrade(self.config, "0005_resume_extraction_runs:0004_trusted_career_profile", sql=True)


if __name__ == "__main__":
    unittest.main()
