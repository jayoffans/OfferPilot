"""Isolated migration coverage for identity and legacy resume ownership."""

import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, Table, create_engine, delete, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.core.config import BACKEND_ROOT, get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import AuthSession, AuthThrottle, Profile, RegistrationInvitation, ResumeDocument, User


class IdentityMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "identity-test.db"
        self.database_url = f"sqlite:///{database_path.as_posix()}"
        self.environment = patch.dict(
            os.environ,
            {"OFFERPILOT_DATABASE_URL": self.database_url, "OFFERPILOT_ENVIRONMENT": "development"},
        )
        self.environment.start()
        get_settings.cache_clear()
        self.config = Config(str(BACKEND_ROOT / "alembic.ini"))
        self.engine = create_engine(self.database_url)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

    def tearDown(self) -> None:
        self.engine.dispose()
        get_settings.cache_clear()
        self.environment.stop()
        self.temporary_directory.cleanup()

    def upgrade(self, revision: str = "head") -> None:
        command.upgrade(self.config, revision)

    def test_empty_database_upgrades_without_metadata_drift(self) -> None:
        self.upgrade()

        expected = {
            "users", "registration_invitations", "auth_sessions", "auth_throttles",
            "resume_documents", "profiles", "profile_facts",
        }
        self.assertTrue(expected <= set(inspect(self.engine).get_table_names()))
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("PRAGMA foreign_keys")), 1)
            self.assertEqual(connection.scalar(text("SELECT version_num FROM alembic_version")), "0006_deepseek_byok")
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            # SQLAlchemy does not reflect options on SQLite inline REFERENCES
            # added with ALTER TABLE. Supplement that one constraint from the
            # actual database pragma instead of masking a metadata difference.
            original_reflect_foreign_keys = self.engine.dialect.get_foreign_keys

            def reflect_foreign_keys(connection, table_name, schema=None, **kwargs):
                foreign_keys = original_reflect_foreign_keys(connection, table_name, schema=schema, **kwargs)
                if table_name == "resume_documents":
                    actual = connection.execute(text("PRAGMA foreign_key_list(resume_documents)")).mappings().all()
                    owner = next(row for row in actual if row["from"] == "user_id")
                    self.assertEqual(owner["on_delete"], "RESTRICT")
                    for foreign_key in foreign_keys:
                        if foreign_key["constrained_columns"] == ["user_id"]:
                            foreign_key["options"] = {**foreign_key["options"], "ondelete": owner["on_delete"]}
                if table_name == "profile_candidates":
                    actual = connection.execute(text("PRAGMA foreign_key_list(profile_candidates)")).mappings().all()
                    run_key = next(row for row in actual if row["from"] == "extraction_run_id")
                    self.assertEqual(run_key["on_delete"], "SET NULL")
                    for foreign_key in foreign_keys:
                        if foreign_key["constrained_columns"] == ["extraction_run_id"]:
                            foreign_key["options"] = {**foreign_key["options"], "ondelete": run_key["on_delete"]}
                return foreign_keys

            with patch.object(self.engine.dialect, "get_foreign_keys", reflect_foreign_keys):
                self.assertEqual(compare_metadata(context, Base.metadata), [])

    def test_upgrade_preserves_legacy_resume_profile_facts_and_null_owner(self) -> None:
        self.upgrade("0001_resume_intelligence")
        metadata = MetaData()
        tables = {
            name: Table(name, metadata, autoload_with=self.engine)
            for name in ("resume_documents", "profiles", "profile_facts")
        }
        resume_id, profile_id, fact_id = (uuid.uuid4() for _ in range(3))
        created_at = datetime(2026, 9, 25, 1, 2, 3, tzinfo=timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(tables["resume_documents"].insert(), {
                "id": resume_id.hex, "file_name": "旧简历.pdf", "file_path": "uploads/legacy.pdf",
                "file_type": "application/pdf", "raw_text": "合成学生掌握 Python", "created_at": created_at,
            })
            connection.execute(tables["profiles"].insert(), {
                "id": profile_id.hex, "resume_id": resume_id.hex, "name": "合成学生", "phone": None,
                "email": "legacy@example.com", "education": [{"school": "合成大学"}],
                "skills": ["Python"], "projects": [{"name": "合成项目"}],
                "internships": [], "created_at": created_at,
            })
            connection.execute(tables["profile_facts"].insert(), {
                "id": fact_id.hex, "profile_id": profile_id.hex, "field_name": "skills",
                "value": ["Python"], "confidence": 0.87, "source_text": "掌握 Python",
                "created_at": created_at,
            })
            original = {
                name: dict(connection.execute(select(table)).mappings().one())
                for name, table in tables.items()
            }

        self.upgrade()

        with self.engine.connect() as connection:
            upgraded = {
                name: dict(connection.execute(select(table)).mappings().one())
                for name, table in tables.items()
            }
            self.assertEqual(upgraded, original)
            self.assertIsNone(connection.scalar(text("SELECT user_id FROM resume_documents")))
            self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])
        with Session(self.engine) as session:
            session.add(User(email_normalized="new@example.com", password_hash="synthetic-password-hash"))
            session.commit()
            resume = session.get(ResumeDocument, resume_id)
            self.assertIsNone(resume.user_id)
            self.assertEqual(resume.profile.id, profile_id)
            self.assertEqual(resume.profile.facts[0].id, fact_id)
            self.assertEqual(resume.profile.facts[0].source_text, "掌握 Python")

    def test_user_deletion_cascades_sessions_but_restricts_owned_resumes(self) -> None:
        self.upgrade()
        now = datetime.now(timezone.utc)
        session_owner, resume_owner = uuid.uuid4(), uuid.uuid4()
        with Session(self.engine) as session:
            session.add_all([
                User(id=session_owner, email_normalized="session@example.com", password_hash="hash"),
                User(id=resume_owner, email_normalized="resume@example.com", password_hash="hash"),
            ])
            session.flush()
            session.add_all([
                AuthSession(user_id=session_owner, token_hash="a" * 64, expires_at=now + timedelta(days=1)),
                AuthSession(user_id=resume_owner, token_hash="b" * 64, expires_at=now + timedelta(days=1)),
                ResumeDocument(user_id=resume_owner, file_name="owned.pdf", file_path="uploads/owned.pdf", file_type="application/pdf"),
            ])
            session.commit()

        with self.engine.begin() as connection:
            connection.execute(delete(User).where(User.id == session_owner))
            self.assertIsNone(connection.scalar(select(AuthSession.id).where(AuthSession.user_id == session_owner)))
        with self.engine.begin() as connection:
            with self.assertRaises(IntegrityError):
                connection.execute(delete(User).where(User.id == resume_owner))
        with Session(self.engine) as session:
            self.assertIsNotNone(session.get(User, resume_owner))
            self.assertIsNotNone(session.scalar(select(AuthSession.id).where(AuthSession.user_id == resume_owner)))
            self.assertIsNotNone(session.scalar(select(ResumeDocument.id).where(ResumeDocument.user_id == resume_owner)))

    def test_unique_emails_and_tokens_and_nonnegative_throttle_are_enforced(self) -> None:
        self.upgrade()
        now = datetime.now(timezone.utc)
        user_id = uuid.uuid4()
        with Session(self.engine) as session:
            session.add(User(id=user_id, email_normalized="student@example.com", password_hash="hash"))
            session.flush()
            session.add_all([
                RegistrationInvitation(token_hash="c" * 64, email_normalized="invite@example.com", expires_at=now + timedelta(days=1)),
                AuthSession(user_id=user_id, token_hash="d" * 64, expires_at=now + timedelta(days=1)),
            ])
            session.commit()
            self.assertTrue(session.get(User, user_id).is_active)

        invalid_records = [
            User(email_normalized="student@example.com", password_hash="another-hash"),
            RegistrationInvitation(token_hash="c" * 64, email_normalized="other@example.com", expires_at=now + timedelta(days=1)),
            AuthSession(user_id=user_id, token_hash="d" * 64, expires_at=now + timedelta(days=1)),
            AuthSession(user_id=uuid.uuid4(), token_hash="e" * 64, expires_at=now + timedelta(days=1)),
            AuthThrottle(key_hash="f" * 64, window_started_at=now, attempt_count=-1),
        ]
        for record in invalid_records:
            with self.subTest(model=type(record).__name__), Session(self.engine) as session:
                session.add(record)
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()

    def test_downgrade_refuses_to_destroy_identity_or_ownership(self) -> None:
        self.upgrade()
        with Session(self.engine) as db:
            user = User(email_normalized="protected@example.com", password_hash="synthetic-hash")
            db.add(user)
            db.flush()
            resume = ResumeDocument(user_id=user.id, file_name="owned.pdf", file_path="private/owned.pdf", file_type="application/pdf")
            db.add(resume)
            db.commit()
            owner_id, resume_id = user.id, resume.id
        with self.assertRaisesRegex(RuntimeError, "Identity data exists"):
            command.downgrade(self.config, "0001_resume_intelligence")
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(User, owner_id))
            # 0003 can safely downgrade before 0002 rejects removal of identity
            # data. Query the retained 0002 column without loading newer fields.
            self.assertEqual(db.scalar(select(ResumeDocument.user_id).where(ResumeDocument.id == resume_id)), owner_id)
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), "0002_identity_ownership")

    def test_downgrade_preserves_resume_children_without_table_rebuild(self) -> None:
        self.upgrade()
        resume_id = uuid.uuid4()
        with Session(self.engine) as session:
            resume = ResumeDocument(
                id=resume_id, file_name="legacy.pdf", file_path="uploads/legacy.pdf", file_type="application/pdf"
            )
            resume.profile = Profile(name="Synthetic", education=[], skills=[], projects=[], internships=[])
            session.add(resume)
            session.commit()

        command.downgrade(self.config, "0001_resume_intelligence")

        inspector = inspect(self.engine)
        self.assertNotIn("users", inspector.get_table_names())
        self.assertNotIn("user_id", {column["name"] for column in inspector.get_columns("resume_documents")})
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM resume_documents")), 1)
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM profiles")), 1)
            self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])


if __name__ == "__main__":
    unittest.main()
