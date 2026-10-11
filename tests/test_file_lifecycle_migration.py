"""File lifecycle migrations preserve legacy data and enforce cleanup boundaries."""

import io
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, MetaData, Table, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.core.config import BACKEND_ROOT, get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import Profile, ProfileFact, ResumeDocument, ResumeFileOperation, User
from offerpilot_api.db.session import build_engine


class FileLifecycleMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "file-lifecycle-test.db"
        self.database_url = f"sqlite:///{database_path.as_posix()}"
        self.environment = patch.dict(os.environ, {
            "OFFERPILOT_DATABASE_URL": self.database_url,
            "OFFERPILOT_ENVIRONMENT": "development",
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

    def user(self) -> uuid.UUID:
        with Session(self.engine) as session:
            user = User(email_normalized=f"{uuid.uuid4().hex}@example.com", password_hash="synthetic-hash")
            session.add(user)
            session.commit()
            return user.id

    @staticmethod
    def resume(**values) -> ResumeDocument:
        return ResumeDocument(
            file_name="synthetic.pdf", file_path="private/synthetic.pdf",
            file_type="application/pdf", raw_text="Synthetic private resume", **values,
        )

    def seed_0002(self):
        self.upgrade("0002_identity_ownership")
        metadata = MetaData()
        tables = {
            name: Table(name, metadata, autoload_with=self.engine)
            for name in ("users", "resume_documents", "profiles", "profile_facts")
        }
        owner_id = uuid.uuid4()
        resume_ids = [uuid.uuid4(), uuid.uuid4()]
        created_at = datetime(2026, 10, 11, 1, 2, 3, tzinfo=timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(tables["users"].insert(), {
                "id": owner_id.hex, "email_normalized": "legacy-owner@example.com",
                "password_hash": "synthetic-legacy-hash", "is_active": True, "created_at": created_at,
            })
            for index, resume_id in enumerate(resume_ids):
                profile_id, fact_id = uuid.uuid4(), uuid.uuid4()
                connection.execute(tables["resume_documents"].insert(), {
                    "id": resume_id.hex, "user_id": owner_id.hex if index == 0 else None,
                    "file_name": f"旧简历-{index}.pdf", "file_path": f"uploads/legacy-{index}.pdf",
                    "file_type": "application/pdf", "raw_text": f"合成学生 {index} 掌握 Python",
                    "created_at": created_at,
                })
                connection.execute(tables["profiles"].insert(), {
                    "id": profile_id.hex, "resume_id": resume_id.hex, "name": f"合成学生 {index}",
                    "phone": None, "email": f"legacy-{index}@example.com",
                    "education": [{"school": "合成大学"}], "skills": ["Python"],
                    "projects": [{"name": "合成项目"}], "internships": [], "created_at": created_at,
                })
                connection.execute(tables["profile_facts"].insert(), {
                    "id": fact_id.hex, "profile_id": profile_id.hex, "field_name": "skills",
                    "value": ["Python"], "confidence": 0.8, "source_text": "掌握 Python",
                    "created_at": created_at,
                })
            original = self.snapshot(connection, tables)
        return tables, original, owner_id, resume_ids

    @staticmethod
    def snapshot(connection, tables):
        return {
            name: [dict(row) for row in connection.execute(select(table).order_by(table.c.id)).mappings()]
            for name, table in tables.items()
        }

    def assert_metadata_matches(self) -> None:
        with self.engine.connect() as connection:
            original_reflection = self.engine.dialect.get_foreign_keys

            def reflect_foreign_keys(connection, table_name, schema=None, **kwargs):
                keys = original_reflection(connection, table_name, schema=schema, **kwargs)
                if table_name == "resume_documents":
                    # As in the identity migration test, reflect actual inline
                    # FK options from SQLite, which SQLAlchemy omits.
                    actual = connection.execute(text("PRAGMA foreign_key_list(resume_documents)")).mappings().all()
                    owner = next(row for row in actual if row["from"] == "user_id")
                    self.assertEqual(owner["on_delete"], "RESTRICT")
                    for key in keys:
                        if key["constrained_columns"] == ["user_id"]:
                            key["options"] = {**key["options"], "ondelete": owner["on_delete"]}
                return keys

            context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
            with patch.object(self.engine.dialect, "get_foreign_keys", reflect_foreign_keys):
                self.assertEqual(compare_metadata(context, Base.metadata), [])
            # Alembic's comparison does not include CHECK constraints. Inspect
            # them separately so enum and nonnegative safeguards cannot drift.
            for table_name in ("resume_documents", "resume_file_operations"):
                checks = {item["name"]: " ".join(item["sqltext"].split())
                          for item in inspect(connection).get_check_constraints(table_name)}
                expected = {constraint.name: " ".join(str(constraint.sqltext).split())
                            for constraint in Base.metadata.tables[table_name].constraints
                            if isinstance(constraint, CheckConstraint)}
                self.assertEqual(checks, expected)

    def test_empty_database_upgrades_with_matching_metadata_and_foreign_keys(self) -> None:
        self.upgrade()
        self.assert_metadata_matches()
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("PRAGMA foreign_keys")), 1)
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), "0003_resume_file_lifecycle")
            self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])
        keys = inspect(self.engine).get_foreign_keys("resume_file_operations")
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["referred_table"], "users")
        self.assertEqual(keys[0]["options"]["ondelete"], "RESTRICT")

    def test_upgrade_preserves_owned_and_unowned_legacy_data_and_relationships(self) -> None:
        tables, original, owner_id, resume_ids = self.seed_0002()
        self.upgrade()
        with self.engine.connect() as connection:
            self.assertEqual(self.snapshot(connection, tables), original)
            self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])
        with Session(self.engine) as session:
            for index, resume_id in enumerate(resume_ids):
                document = session.get(ResumeDocument, resume_id)
                self.assertEqual(document.user_id, owner_id if index == 0 else None)
                self.assertEqual(document.profile.skills, ["Python"])
                self.assertEqual(document.profile.facts[0].source_text, "掌握 Python")
                self.assertEqual(document.processing_status, "parsed")
                self.assertEqual(document.lifecycle_status, "active")
                self.assertIsNone(document.object_key)
                self.assertIsNone(document.byte_size)
                self.assertIsNone(document.sha256)
            self.assertEqual(session.scalars(select(ResumeFileOperation)).all(), [])
        self.assert_metadata_matches()

    def test_legacy_orm_fixtures_and_raw_inserts_receive_safe_defaults(self) -> None:
        self.upgrade()
        with Session(self.engine) as session:
            document = self.resume()
            session.add(document)
            session.commit()
            self.assertEqual(document.processing_status, "parsed")
            self.assertEqual(document.lifecycle_status, "active")
            self.assertIsNone(document.object_key)
            self.assertIsNone(document.byte_size)
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO resume_documents (id, file_name, file_path, file_type) "
                "VALUES (:id, 'synthetic.pdf', 'private/synthetic.pdf', 'application/pdf')"
            ), {"id": uuid.uuid4().hex})
        with Session(self.engine) as session:
            documents = session.scalars(select(ResumeDocument)).all()
            self.assertEqual(len(documents), 2)
            self.assertTrue(all(item.processing_status == "parsed" and item.lifecycle_status == "active" for item in documents))

    def test_duplicate_object_keys_invalid_sizes_and_states_are_rejected(self) -> None:
        self.upgrade()
        with Session(self.engine) as session:
            session.add(self.resume(object_key="managed-key", byte_size=0))
            session.commit()
        for values in (
            {"object_key": "managed-key"}, {"byte_size": -1},
            {"processing_status": "not-a-state"}, {"lifecycle_status": "not-a-state"},
        ):
            with self.subTest(values=values), Session(self.engine) as session:
                session.add(self.resume(**values))
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()
        with Session(self.engine) as session:
            session.add_all([
                self.resume(processing_status="uploading"), self.resume(processing_status="uploaded"),
                self.resume(processing_status="failed", lifecycle_status="delete_failed"),
                self.resume(lifecycle_status="deleting"),
            ])
            session.commit()
            self.assertEqual(len(session.scalars(select(ResumeDocument)).all()), 5)

    def test_operation_constraints_owner_restrict_and_missing_resume_tombstone(self) -> None:
        self.upgrade()
        owner_id = self.user()
        tombstone_id = uuid.uuid4()
        with Session(self.engine) as session:
            operation = ResumeFileOperation(resume_id=tombstone_id, user_id=owner_id, reason="delete", status="deleted")
            session.add(operation)
            session.commit()
            self.assertEqual(operation.attempt_count, 0)
            self.assertIsNotNone(operation.created_at)
            self.assertIsNotNone(operation.updated_at)
            self.assertIsNone(session.get(ResumeDocument, tombstone_id))
        invalid = [
            {"reason": "other"}, {"status": "other"}, {"attempt_count": -1},
            {"user_id": uuid.uuid4()}, {"user_id": None}, {"resume_id": tombstone_id},
        ]
        for changed in invalid:
            with self.subTest(field=list(changed)), Session(self.engine) as session:
                values = {"resume_id": uuid.uuid4(), "user_id": owner_id, "reason": "upload", "status": "uploading"}
                session.add(ResumeFileOperation(**{**values, **changed}))
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()
        with self.engine.begin() as connection:
            with self.assertRaises(IntegrityError):
                connection.execute(delete(User).where(User.id == owner_id))
        with Session(self.engine) as session:
            self.assertIsNotNone(session.get(User, owner_id))
            self.assertIsNotNone(session.get(ResumeFileOperation, tombstone_id))

    def test_hard_delete_cascades_derived_data_retains_operation_and_blocks_late_inserts(self) -> None:
        self.upgrade()
        owner_id = self.user()
        with Session(self.engine) as session:
            document = self.resume(user_id=owner_id)
            document.profile = Profile(name="Synthetic")
            document.profile.facts.append(ProfileFact(field_name="skills", value="Python", confidence=0.9, source_text="Synthetic Python"))
            session.add(document)
            session.flush()
            resume_id, profile_id = document.id, document.profile.id
            session.add(ResumeFileOperation(resume_id=resume_id, user_id=owner_id, reason="delete", status="deleting"))
            session.commit()
        with self.engine.begin() as connection:
            connection.execute(delete(ResumeDocument).where(ResumeDocument.id == resume_id))
        with Session(self.engine) as session:
            self.assertIsNone(session.get(ResumeDocument, resume_id))
            self.assertIsNone(session.get(Profile, profile_id))
            self.assertEqual(session.scalars(select(ProfileFact)).all(), [])
            self.assertIsNotNone(session.get(ResumeFileOperation, resume_id))
        late_records = [
            Profile(resume_id=resume_id, name="Late synthetic result"),
            ProfileFact(profile_id=profile_id, field_name="skills", value="Python", confidence=0.9, source_text="Late synthetic evidence"),
        ]
        for record in late_records:
            with self.subTest(model=type(record).__name__), Session(self.engine) as session:
                session.add(record)
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()

    def test_clean_downgrade_preserves_0002_rows_and_child_relationships(self) -> None:
        tables, original, _, _ = self.seed_0002()
        self.upgrade()
        command.downgrade(self.config, "0002_identity_ownership")
        with self.engine.connect() as connection:
            self.assertEqual(self.snapshot(connection, tables), original)
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), "0002_identity_ownership")
            self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])
        self.assertNotIn("resume_file_operations", inspect(self.engine).get_table_names())
        columns = {item["name"] for item in inspect(self.engine).get_columns("resume_documents")}
        self.assertFalse(columns & {"object_key", "byte_size", "sha256", "processing_status", "lifecycle_status"})

    def test_downgrade_refuses_even_deleted_operations_without_dropping_data(self) -> None:
        self.upgrade()
        owner_id, resume_id = self.user(), uuid.uuid4()
        with Session(self.engine) as session:
            session.add(ResumeFileOperation(resume_id=resume_id, user_id=owner_id, reason="delete", status="deleted"))
            session.commit()
        with self.assertRaisesRegex(RuntimeError, "File lifecycle data exists"):
            command.downgrade(self.config, "0002_identity_ownership")
        with Session(self.engine) as session:
            self.assertIsNotNone(session.get(ResumeFileOperation, resume_id))
            self.assertIsNotNone(session.get(User, owner_id))
            self.assertEqual(session.scalar(text("SELECT version_num FROM alembic_version")), "0003_resume_file_lifecycle")
        self.assert_metadata_matches()

    def test_downgrade_refuses_managed_metadata_and_nondefault_states(self) -> None:
        self.upgrade()
        with Session(self.engine) as session:
            document = self.resume()
            session.add(document)
            session.commit()
            resume_id = document.id
        for values in (
            {"object_key": "managed-key"}, {"byte_size": 0}, {"sha256": "a" * 64},
            {"processing_status": "uploaded"}, {"processing_status": "failed"},
            {"lifecycle_status": "deleting"}, {"lifecycle_status": "delete_failed"},
        ):
            with self.subTest(values=values):
                with Session(self.engine) as session:
                    document = session.get(ResumeDocument, resume_id)
                    for field, value in values.items():
                        setattr(document, field, value)
                    session.commit()
                with self.assertRaisesRegex(RuntimeError, "File lifecycle data exists"):
                    command.downgrade(self.config, "0002_identity_ownership")
                with Session(self.engine) as session:
                    document = session.get(ResumeDocument, resume_id)
                    for field, value in values.items():
                        self.assertEqual(getattr(document, field), value)
                    self.assertEqual(session.scalar(text("SELECT version_num FROM alembic_version")), "0003_resume_file_lifecycle")
                    document.object_key = document.byte_size = document.sha256 = None
                    document.processing_status, document.lifecycle_status = "parsed", "active"
                    session.commit()

    def test_offline_downgrade_is_refused_without_a_data_safety_check(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "online data safety check"):
            command.downgrade(self.config, "0003_resume_file_lifecycle:0002_identity_ownership", sql=True)


if __name__ == "__main__":
    unittest.main()
