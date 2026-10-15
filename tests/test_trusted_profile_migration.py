"""New trusted-profile migration preserves S1 data and safe rollback limits."""

import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, MetaData, Table, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.core.config import BACKEND_ROOT, get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import CareerProfile, CareerProfileEntry, ProfileCandidate, ResumeDocument, User
from offerpilot_api.db.session import build_engine


HEAD_REVISION = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini"))).get_current_head()


class TrustedProfileMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="offerpilot-profile-migration-")
        database_path = Path(self.temporary_directory.name) / "migration.db"
        self.database_url = f"sqlite:///{database_path.as_posix()}"
        self.environment = patch.dict(os.environ, {
            "OFFERPILOT_DATABASE_URL": self.database_url, "OFFERPILOT_ENVIRONMENT": "development",
        })
        self.environment.start()
        get_settings.cache_clear()
        self.config = Config(str(BACKEND_ROOT / "alembic.ini"), output_buffer=io.StringIO())
        self.engine = build_engine(self.database_url)

    def tearDown(self) -> None:
        self.engine.dispose()
        get_settings.cache_clear()
        self.environment.stop()
        self.temporary_directory.cleanup()

    def upgrade(self, revision: str = "head") -> None:
        command.upgrade(self.config, revision)

    def owner(self):
        with Session(self.engine) as session:
            owner = User(email_normalized="synthetic-migration@example.com", password_hash="synthetic-hash")
            session.add(owner)
            session.commit()
            return owner.id

    @staticmethod
    def snapshot(connection, tables):
        return {name: [dict(row) for row in connection.execute(select(table).order_by(table.c.id)).mappings()]
                for name, table in tables.items()}

    def legacy_0003(self):
        self.upgrade("0003_resume_file_lifecycle")
        metadata = MetaData()
        tables = {name: Table(name, metadata, autoload_with=self.engine)
                  for name in ("users", "resume_documents", "profiles", "profile_facts")}
        owner_id = uuid4()
        created_at = datetime(2026, 10, 11, 1, 2, 3, tzinfo=timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(tables["users"].insert(), {
                "id": owner_id.hex, "email_normalized": "synthetic-legacy@example.com",
                "password_hash": "synthetic-hash", "is_active": True, "created_at": created_at,
            })
            for index in range(2):
                resume_id, profile_id, fact_id = uuid4(), uuid4(), uuid4()
                connection.execute(tables["resume_documents"].insert(), {
                    "id": resume_id.hex, "user_id": owner_id.hex if index == 0 else None,
                    "file_name": f"旧合成资料-{index}.pdf", "file_path": f"private/legacy-{index}.pdf",
                    "file_type": "application/pdf", "raw_text": f"合成学生 {index} Python",
                    "object_key": f"{uuid4().hex}.pdf" if index == 0 else None,
                    "byte_size": 120 if index == 0 else None, "sha256": "a" * 64 if index == 0 else None,
                    "processing_status": "parsed", "lifecycle_status": "active", "created_at": created_at,
                })
                connection.execute(tables["profiles"].insert(), {
                    "id": profile_id.hex, "resume_id": resume_id.hex, "name": "旧合成学生",
                    "phone": None, "email": None, "education": [{"school": "旧合成学校"}],
                    "skills": ["Python"], "projects": [{"name": "旧合成项目"}],
                    "internships": [], "created_at": created_at,
                })
                connection.execute(tables["profile_facts"].insert(), {
                    "id": fact_id.hex, "profile_id": profile_id.hex, "field_name": "skills[0]",
                    "value": "Python", "confidence": 1.0, "source_text": "Python",
                    "created_at": created_at,
                })
            original = self.snapshot(connection, tables)
        return tables, original

    def test_0003_upgrade_preserves_owned_unowned_file_metadata_and_unconfirmed_legacy_rows(self) -> None:
        tables, original = self.legacy_0003()
        self.upgrade()
        with self.engine.connect() as connection:
            self.assertEqual(self.snapshot(connection, tables), original)
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), HEAD_REVISION)
            self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])
            # A historical model claim, including confidence=1, is never a
            # confirmed career fact or an implicitly imported candidate.
            for table_name in set(Base.metadata.tables) - set(tables) - {
                "registration_invitations", "auth_sessions", "auth_throttles", "resume_file_operations",
            }:
                self.assertEqual(connection.scalar(select(Base.metadata.tables[table_name]).limit(1)), None)

    def test_new_table_checks_and_foreign_keys_match_models(self) -> None:
        self.upgrade()
        inspector = inspect(self.engine)
        new_names = {name for name in Base.metadata.tables if name.startswith("career_") or name == "profile_candidates"}
        self.assertEqual(len(new_names), 3)
        for name in new_names:
            with self.subTest(table=name):
                expected = {constraint.name: " ".join(str(constraint.sqltext).split())
                            for constraint in Base.metadata.tables[name].constraints if isinstance(constraint, CheckConstraint)}
                actual = {item["name"]: " ".join(item["sqltext"].split())
                          for item in inspector.get_check_constraints(name)}
                self.assertEqual(actual, expected)
                self.assertTrue(inspector.get_foreign_keys(name))
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("PRAGMA foreign_keys")), 1)
            self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_one_formal_profile_per_owner_and_revision_constraints_are_database_enforced(self) -> None:
        self.upgrade()
        owner_id = self.owner()
        with Session(self.engine) as session:
            session.add(CareerProfile(user_id=owner_id))
            revision_owner = User(email_normalized="revision-migration@example.com", password_hash="synthetic-hash")
            session.add(revision_owner)
            session.commit()
            revision_owner_id = revision_owner.id
        for values in ({"user_id": owner_id}, {"user_id": uuid4()}, {"user_id": None},
                       {"user_id": revision_owner_id, "revision": -1}):
            with self.subTest(values=values), Session(self.engine) as session:
                session.add(CareerProfile(**values))
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()

    def test_empty_new_tables_can_downgrade_to_0003_without_touching_existing_file_data(self) -> None:
        tables, original = self.legacy_0003()
        self.upgrade()
        command.downgrade(self.config, "0003_resume_file_lifecycle")
        with self.engine.connect() as connection:
            self.assertEqual(self.snapshot(connection, tables), original)
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), "0003_resume_file_lifecycle")
            self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])
        self.assertNotIn("career_profiles", inspect(self.engine).get_table_names())
        self.assertIn("resume_file_operations", inspect(self.engine).get_table_names())

    def test_entry_and_candidate_states_and_batch_uniqueness_are_database_enforced(self) -> None:
        self.upgrade()
        owner_id = self.owner()
        with Session(self.engine) as session:
            profile = CareerProfile(user_id=owner_id)
            document = ResumeDocument(user_id=owner_id, file_name="synthetic.pdf", file_path="private/synthetic.pdf",
                                      file_type="application/pdf", raw_text="Synthetic Python")
            session.add_all([profile, document])
            session.commit()
            profile_id, resume_id = profile.id, document.id
        entry_values = {"profile_id": profile_id, "category": "skill", "value": {"name": "Python"},
                        "position": 0, "source_kind": "manual"}
        candidate_values = {
            "profile_id": profile_id, "resume_id": resume_id, "batch_key": "synthetic-batch", "item_index": 0,
            "batch_digest": "a" * 64, "category": "skill", "value": {"name": "Python"},
            "evidence": [], "evidence_status": "unsupported", "schema_version": "1.0",
            "extraction_version": "synthetic-v1",
        }
        with Session(self.engine) as session:
            candidate = ProfileCandidate(**candidate_values)
            session.add(candidate)
            session.commit()
            self.assertEqual(candidate.status, "pending")
        invalid_entries = [{"category": "invented"}, {"source_kind": "auto_confirmed"},
                           {"position": -1}, {"profile_id": uuid4()}, {"resume_id": uuid4()}]
        invalid_candidates = [
            {}, {"category": "invented"}, {"status": "auto_confirmed"},
            {"evidence_status": "certain"}, {"item_index": -1}, {"resume_id": uuid4()},
            {"profile_id": uuid4()}, {"entry_id": uuid4()},
        ]
        for changed in invalid_entries:
            with self.subTest(entry=changed), Session(self.engine) as session:
                session.add(CareerProfileEntry(**{**entry_values, **changed}))
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()
        for changed in invalid_candidates:
            # Each constraint uses its own batch key; only the explicit {}
            # case intentionally hits the existing idempotency tuple.
            values = {**candidate_values, "batch_key": uuid4().hex, **changed} if changed else candidate_values
            with self.subTest(candidate=changed), Session(self.engine) as session:
                session.add(ProfileCandidate(**values))
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()

    def test_database_foreign_keys_preserve_formal_entry_and_remove_resume_candidates_on_hard_delete(self) -> None:
        self.upgrade()
        owner_id = self.owner()
        with Session(self.engine) as session:
            profile = CareerProfile(user_id=owner_id)
            document = ResumeDocument(user_id=owner_id, file_name="synthetic.pdf", file_path="private/synthetic.pdf",
                                      file_type="application/pdf", raw_text="Synthetic Python")
            session.add_all([profile, document])
            session.flush()
            entry = CareerProfileEntry(profile_id=profile.id, category="skill", value={"name": "Python"},
                                       position=0, source_kind="resume_confirmed", resume_id=document.id,
                                       field_sources={"name": "resume"}, evidence=[])
            session.add(entry)
            session.flush()
            candidate = ProfileCandidate(
                profile_id=profile.id, resume_id=document.id, batch_key="synthetic-delete", item_index=0,
                batch_digest="b" * 64, category="skill", value={"name": "Python"}, evidence=[],
                evidence_status="supported", status="confirmed", schema_version="1.0",
                extraction_version="synthetic-v1", entry_id=entry.id,
            )
            session.add(candidate)
            session.commit()
            profile_id, resume_id, entry_id, candidate_id = profile.id, document.id, entry.id, candidate.id
        with self.engine.begin() as connection:
            connection.execute(delete(ResumeDocument).where(ResumeDocument.id == resume_id))
        with Session(self.engine) as session:
            self.assertIsNone(session.get(ProfileCandidate, candidate_id))
            self.assertIsNone(session.get(CareerProfileEntry, entry_id).resume_id)
            self.assertIsNotNone(session.get(CareerProfile, profile_id))
        with self.engine.begin() as connection:
            connection.execute(delete(CareerProfile).where(CareerProfile.id == profile_id))
        with Session(self.engine) as session:
            self.assertIsNone(session.get(CareerProfileEntry, entry_id))
            self.assertIsNotNone(session.get(User, owner_id))

    def test_downgrade_with_saved_profile_is_refused_and_preserves_revision_and_owner(self) -> None:
        self.upgrade()
        owner_id = self.owner()
        with Session(self.engine) as session:
            profile = CareerProfile(user_id=owner_id, revision=3)
            session.add(profile)
            session.commit()
            profile_id = profile.id
        with self.assertRaises(RuntimeError):
            command.downgrade(self.config, "0003_resume_file_lifecycle")
        with Session(self.engine) as session:
            stored = session.get(CareerProfile, profile_id)
            self.assertEqual(stored.user_id, owner_id)
            self.assertEqual(stored.revision, 3)
            self.assertEqual(session.scalar(text("SELECT version_num FROM alembic_version")), "0004_trusted_career_profile")

    def test_offline_downgrade_cannot_skip_live_data_safety_check(self) -> None:
        with self.assertRaises(RuntimeError):
            command.downgrade(self.config, f"{HEAD_REVISION}:0003_resume_file_lifecycle", sql=True)


if __name__ == "__main__":
    unittest.main()
