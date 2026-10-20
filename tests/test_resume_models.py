"""Persistence coverage for the Phase 1-1 resume intelligence schema."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from offerpilot_api.core.config import get_settings
from offerpilot_api.db.init import initialize_database
from offerpilot_api.db.models import Profile, ProfileFact, ResumeDocument


class ResumeModelTests(unittest.TestCase):
    def test_migration_and_relationship_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "offerpilot-test.db"
            database_url = f"sqlite:///{database_path.as_posix()}"

            with patch.dict(os.environ, {"OFFERPILOT_DATABASE_URL": database_url}):
                get_settings.cache_clear()
                try:
                    initialize_database()
                finally:
                    get_settings.cache_clear()

            from offerpilot_api.db.session import build_engine

            test_engine = build_engine(database_url)
            try:
                table_names = set(inspect(test_engine).get_table_names())
                self.assertTrue({"resume_documents", "profiles", "profile_facts"} <= table_names)

                resume = ResumeDocument(
                    file_name="sample.pdf",
                    file_path="uploads/sample.pdf",
                    file_type="application/pdf",
                    raw_text="Python internship project",
                )
                profile = Profile(
                    name="Test Student",
                    email="student@example.com",
                    education=[{"school": "Example University"}],
                    skills=["Python"],
                    projects=[],
                    internships=[],
                )
                profile.facts.append(
                    ProfileFact(
                        field_name="skills",
                        value="Python",
                        confidence=0.95,
                        source_text="Python internship project",
                    )
                )
                resume.profile = profile

                with Session(test_engine) as session:
                    session.add(resume)
                    session.commit()
                    resume_id = resume.id

                with Session(test_engine) as session:
                    saved_resume = session.get(ResumeDocument, resume_id)
                    self.assertIsNotNone(saved_resume)
                    self.assertEqual(saved_resume.profile.skills, ["Python"])
                    self.assertEqual(saved_resume.profile.facts[0].value, "Python")
                    self.assertEqual(saved_resume.profile.facts[0].profile_id, saved_resume.profile.id)
            finally:
                test_engine.dispose()


if __name__ == "__main__":
    unittest.main()
