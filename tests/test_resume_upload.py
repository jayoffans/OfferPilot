"""API-level checks for PDF upload, text extraction, and rejection paths."""

import tempfile
import unittest
from pathlib import Path
from uuid import UUID

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import ResumeDocument
from offerpilot_api.db.session import build_engine, get_db
from offerpilot_api.main import create_app
from offerpilot_api.identity.services import create_invitation


def make_pdf(text: str) -> bytes:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
        return pdf.tobytes()


class ResumeUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        database_url = f"sqlite:///{(self.root / 'test.db').as_posix()}"
        self.engine = build_engine(database_url)
        Base.metadata.create_all(self.engine)
        settings = Settings(
            database_url=database_url,
            upload_dir=self.root / "uploads",
            session_cookie_secure=False,
        )
        app = create_app()

        def test_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_settings] = lambda: settings
        self.client = TestClient(app)
        with Session(self.engine) as session:
            invitation = create_invitation(session, email="upload-test@example.com")
        registration = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "upload-test@example.com",
                "password": "Synthetic-upload-password-2026",
                "invite_token": invitation,
            },
        )
        self.assertEqual(registration.status_code, 201)
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "upload-test@example.com", "password": "Synthetic-upload-password-2026"},
        )
        self.assertEqual(login.status_code, 200)
        self.user_id = UUID(login.json()["user"]["id"])
        self.client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_pdf_text_is_saved_with_document(self) -> None:
        response = self.client.post(
            "/resume/upload",
            files={"file": ("resume.pdf", make_pdf("Python internship"), "application/pdf")},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["file_name"], "resume.pdf")
        self.assertEqual(response.json()["file_type"], "application/pdf")
        self.assertNotIn("raw_text", response.json())
        self.assertNotIn("file_path", response.json())
        with Session(self.engine) as session:
            document = session.scalar(select(ResumeDocument))
            self.assertIsNotNone(document)
            self.assertEqual(str(document.id), response.json()["id"])
            self.assertEqual(document.user_id, self.user_id)
            self.assertIn("Python internship", document.raw_text)
            self.assertEqual(document.file_path, "")
            self.assertTrue((self.root / "uploads" / document.object_key).is_file())
            self.assertEqual(document.processing_status, "parsed")

    def test_non_pdf_extension_is_rejected_without_persistence(self) -> None:
        response = self.client.post(
            "/resume/upload",
            files={"file": ("resume.txt", b"plain text", "text/plain")},
        )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(self._document_count(), 0)
        self.assertFalse((self.root / "uploads").exists())

    def test_pdf_extension_with_invalid_content_is_rejected(self) -> None:
        response = self.client.post(
            "/resume/upload",
            files={"file": ("resume.pdf", b"not a pdf", "application/pdf")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self._document_count(), 0)
        self.assertEqual(list((self.root / "uploads").iterdir()), [])

    def test_pdf_without_selectable_text_is_rejected(self) -> None:
        with pymupdf.open() as pdf:
            pdf.new_page()
            blank_pdf = pdf.tobytes()
        response = self.client.post(
            "/resume/upload",
            files={"file": ("blank.pdf", blank_pdf, "application/pdf")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self._document_count(), 0)
        self.assertEqual(list((self.root / "uploads").iterdir()), [])

    def test_upload_is_unavailable_outside_development(self) -> None:
        self.client.app.dependency_overrides[get_settings] = lambda: Settings(
            upload_dir=self.root / "uploads",
            session_cookie_secure=False,
        ).model_copy(update={"environment": "production"})
        response = self.client.post(
            "/resume/upload",
            files={"file": ("resume.pdf", make_pdf("Python internship"), "application/pdf")},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self._document_count(), 0)

    def _document_count(self) -> int:
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(ResumeDocument))


if __name__ == "__main__":
    unittest.main()
