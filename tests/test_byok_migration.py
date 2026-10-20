"""0005 existing runs/candidate FK links survive 0006 without changing facts."""
import unittest
from uuid import uuid4
from alembic import command
from sqlalchemy import MetaData, Table, text
import test_extraction_migration as harness


class BYOKMigrationTests(unittest.TestCase):
    setUp = harness.ExtractionMigrationTests.setUp
    tearDown = harness.ExtractionMigrationTests.tearDown
    upgrade = harness.ExtractionMigrationTests.upgrade
    legacy_0004 = harness.ExtractionMigrationTests.legacy_0004
    snapshot = staticmethod(harness.ExtractionMigrationTests.snapshot)

    def test_existing_mock_runs_candidate_links_and_values_survive_upgrade_and_safe_downgrade(self):
        tables, original, owner, resume, candidate = self.legacy_0004()
        self.upgrade("0005_resume_extraction_runs")
        runs = Table("resume_extraction_runs", MetaData(), autoload_with=self.engine)
        rid = uuid4()
        with self.engine.begin() as db:
            db.execute(runs.insert(), {"id": rid.hex, "user_id": owner.hex, "resume_id": resume.hex,
                "idempotency_key": "existing-mock", "source_version": "a" * 64, "max_attempts": 2,
                "max_calls": 4, "timeout_seconds": 30, "schema_version": "2.0", "extraction_version": "old-v2",
                "prompt_version": "resume_extract_v2", "provider_version": "mock"})
            db.execute(text("UPDATE profile_candidates SET extraction_run_id=:rid WHERE id=:cid"), {"rid": rid.hex, "cid": candidate.hex})
            before = dict(db.execute(runs.select()).mappings().one())
        self.upgrade()
        with self.engine.connect() as db:
            self.assertEqual(self.snapshot(db, tables), original)
            self.assertEqual(db.scalar(text("SELECT extraction_run_id FROM profile_candidates WHERE id=:cid"), {"cid": candidate.hex}), rid.hex)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])
            after = dict(db.execute(runs.select()).mappings().one())
            self.assertEqual(after, before)
        command.downgrade(self.config, "0005_resume_extraction_runs")
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(text("SELECT extraction_run_id FROM profile_candidates WHERE id=:cid"), {"cid": candidate.hex}), rid.hex)
            self.assertEqual(dict(db.execute(runs.select()).mappings().one()), before)
            self.assertEqual(db.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_real_run_history_refuses_downgrade(self):
        _, _, owner, resume, _ = self.legacy_0004()
        self.upgrade()
        with self.engine.begin() as db:
            db.execute(text("""INSERT INTO resume_extraction_runs
                (id,user_id,resume_id,idempotency_key,source_version,mode,test_result,max_attempts,max_calls,
                timeout_seconds,schema_version,extraction_version,prompt_version,provider_version,session_id,
                key_reference,authorization_id,sent_text_sha256,consent_version,model)
                VALUES (:id,:owner,:resume,'paid',:hash,'deepseek',0,1,1,60,'2.0','real-v2','p2','deepseek',
                :ref,:ref,:ref,:hash,'consent-v1','deepseek-flash')"""), {"id": uuid4().hex, "owner": owner.hex, "resume": resume.hex, "hash": "a" * 64, "ref": uuid4().hex})
        with self.assertRaisesRegex(RuntimeError, "DeepSeek data exists"):
            command.downgrade(self.config, "0005_resume_extraction_runs")
        with self.engine.connect() as db:
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), "0006_deepseek_byok")


if __name__ == "__main__":
    unittest.main()
