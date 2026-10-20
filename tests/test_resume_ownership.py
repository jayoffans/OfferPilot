"""Ownership checks through real invitation registration and cookie sessions."""

import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from offerpilot_api.account_lifecycle.ledger import DeletionLedger
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import Profile, ProfileFact, ResumeDocument
from offerpilot_api.db.session import build_engine, get_db
from offerpilot_api.identity.services import create_invitation
from offerpilot_api.main import create_app
from offerpilot_api.resume_ingestion.access import (
    ResumeAccessError, get_owned_resume, get_owned_profile,
    list_owned_profile_facts, list_owned_resumes, rename_owned_resume,
)


def make_pdf() -> bytes:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Synthetic resume: Python")
        return pdf.tobytes()


class ResumeOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        database_url = f"sqlite:///{(self.root / 'ownership.db').as_posix()}"
        self.engine = build_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.settings = Settings(
            database_url=database_url,
            upload_dir=self.root / "uploads",
            deletion_ledger_dir=self.root / "deletion-ledger",
            session_cookie_secure=False,
        )
        DeletionLedger(self.settings.deletion_ledger_dir).initialize()
        app = create_app()

        def test_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.anonymous = TestClient(app)
        self.owner, self.owner_id = self._registered_client(app, "owner@example.com")
        self.other, self.other_id = self._registered_client(app, "other@example.com")
        self.owned_id, self.owned_profile_id, self.owned_fact_id = self._seed_resume(
            self.owner_id, "owned.pdf"
        )
        self.other_resume_id, _, _ = self._seed_resume(self.other_id, "other.pdf")
        self.legacy_id, _, _ = self._seed_resume(None, "legacy.pdf")

    def tearDown(self) -> None:
        self.anonymous.close()
        self.owner.close()
        self.other.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _registered_client(self, app, email: str) -> tuple[TestClient, UUID]:
        client = TestClient(app)
        with Session(self.engine) as session:
            invitation = create_invitation(session, email=email)
        registration = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "Synthetic-ownership-password-2026",
                "invite_token": invitation,
            },
        )
        self.assertEqual(registration.status_code, 201)
        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Synthetic-ownership-password-2026"},
        )
        self.assertEqual(login.status_code, 200)
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        return client, UUID(login.json()["user"]["id"])

    def _seed_resume(self, user_id: UUID | None, file_name: str) -> tuple[UUID, UUID, UUID]:
        with Session(self.engine) as session:
            document = ResumeDocument(
                user_id=user_id,
                file_name=file_name,
                file_path=f"private/{file_name}",
                file_type="application/pdf",
                raw_text="PRIVATE_SYNTHETIC_TEXT",
            )
            profile = Profile(resume_document=document, name="Synthetic student")
            fact = ProfileFact(
                profile=profile,
                field_name="skills[0]",
                value="Python",
                confidence=0.9,
                source_text="Synthetic resume: Python",
            )
            session.add(document)
            session.commit()
            return document.id, profile.id, fact.id

    def _read_paths(self, resume_id: UUID) -> list[str]:
        base = f"/api/v1/resumes/{resume_id}"
        return [base, f"{base}/profile", f"{base}/profile/facts"]

    def test_service_rejects_missing_or_unresolved_owner_before_legacy_access(self) -> None:
        with Session(self.engine) as db:
            for owner in (None, str(self.owner_id)):
                calls = [
                    lambda: list_owned_resumes(db, user_id=owner),
                    lambda: get_owned_resume(db, resume_id=self.legacy_id, user_id=owner),
                    lambda: get_owned_profile(db, resume_id=self.legacy_id, user_id=owner),
                    lambda: list_owned_profile_facts(db, resume_id=self.legacy_id, user_id=owner),
                    lambda: rename_owned_resume(db, resume_id=self.legacy_id, user_id=owner, file_name="wrong.pdf"),
                ]
                for call in calls:
                    with self.assertRaises(ResumeAccessError) as caught:
                        call()
                    self.assertEqual(caught.exception.status_code, 401)
            self.assertIsNone(db.get(ResumeDocument, self.legacy_id).user_id)
            self.assertEqual(db.get(ResumeDocument, self.legacy_id).file_name, "legacy.pdf")

    def test_anonymous_requests_cannot_read_write_or_upload(self) -> None:
        paths = ["/api/v1/resumes", *self._read_paths(self.owned_id)]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.anonymous.get(path).status_code, 401)
        self.assertEqual(
            self.anonymous.patch(
                f"/api/v1/resumes/{self.owned_id}", json={"file_name": "renamed.pdf"}
            ).status_code,
            401,
        )
        for path in ("/resume/upload", "/api/v1/resumes"):
            with self.subTest(path=path):
                response = self.anonymous.post(
                    path, files={"file": ("resume.pdf", make_pdf(), "application/pdf")}
                )
                self.assertEqual(response.status_code, 401)
        self.assertFalse(self.settings.upload_dir.exists())

    def test_list_and_detail_expose_only_owned_public_metadata(self) -> None:
        response = self.owner.get("/api/v1/resumes")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [str(self.owned_id)])
        forged_filter = self.owner.get("/api/v1/resumes", params={"user_id": str(self.other_id)})
        self.assertEqual(forged_filter.status_code, 200)
        self.assertEqual([item["id"] for item in forged_filter.json()], [str(self.owned_id)])
        expected_keys = {"id", "file_name", "file_type", "created_at"}
        self.assertEqual(set(response.json()[0]), expected_keys)
        detail = self.owner.get(f"/api/v1/resumes/{self.owned_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(set(detail.json()), expected_keys)
        self.assertNotIn("PRIVATE_SYNTHETIC_TEXT", detail.text)
        self.assertNotIn("private/", detail.text)

    def test_owned_profile_and_facts_are_reachable_through_parent_chain(self) -> None:
        profile = self.owner.get(f"/api/v1/resumes/{self.owned_id}/profile")
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["id"], str(self.owned_profile_id))
        facts = self.owner.get(f"/api/v1/resumes/{self.owned_id}/profile/facts")
        self.assertEqual(facts.status_code, 200)
        self.assertEqual([fact["id"] for fact in facts.json()], [str(self.owned_fact_id)])
        self.assertEqual(facts.json()[0]["value"], "Python")
        for response in (profile, facts):
            self.assertNotIn("file_path", response.text)
            self.assertNotIn("raw_text", response.text)
            self.assertNotIn("PRIVATE_SYNTHETIC_TEXT", response.text)

    def test_cross_user_null_owner_and_missing_reads_share_404(self) -> None:
        missing_id = uuid4()
        for resume_id in (self.other_resume_id, self.legacy_id, missing_id):
            for path in self._read_paths(resume_id):
                with self.subTest(path=path):
                    response = self.owner.get(path)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {"detail": "Resume resource not found"})
        for path in self._read_paths(self.owned_id):
            with self.subTest(path=path):
                self.assertEqual(self.other.get(path).status_code, 404)

    def test_missing_owned_profile_is_404_and_empty_owned_facts_are_empty_list(self) -> None:
        with Session(self.engine) as session:
            document = ResumeDocument(
                user_id=self.owner_id,
                file_name="no-profile.pdf",
                file_path="private/no-profile.pdf",
                file_type="application/pdf",
            )
            session.add(document)
            session.commit()
            no_profile_id = document.id
            owned_fact = session.get(ProfileFact, self.owned_fact_id)
            session.delete(owned_fact)
            session.commit()
        for suffix in ("/profile", "/profile/facts"):
            self.assertEqual(
                self.owner.get(f"/api/v1/resumes/{no_profile_id}{suffix}").status_code, 404
            )
        facts = self.owner.get(f"/api/v1/resumes/{self.owned_id}/profile/facts")
        self.assertEqual(facts.status_code, 200)
        self.assertEqual(facts.json(), [])

    def test_upload_ignores_client_user_id_and_uses_authenticated_owner(self) -> None:
        response = self.owner.post(
            "/api/v1/resumes",
            data={"user_id": str(self.other_id)},
            files={"file": ("resume.pdf", make_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 201)
        with Session(self.engine) as session:
            document = session.get(ResumeDocument, UUID(response.json()["id"]))
            self.assertEqual(document.user_id, self.owner_id)
        self.assertEqual(
            self.other.get(f"/api/v1/resumes/{response.json()['id']}").status_code, 404
        )

    def test_rename_changes_display_name_and_keeps_owner_and_storage_path(self) -> None:
        response = self.owner.patch(
            f"/api/v1/resumes/{self.owned_id}",
            json={"file_name": "C:\\fakepath\\updated.pdf"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["file_name"], "updated.pdf")
        with Session(self.engine) as session:
            document = session.get(ResumeDocument, self.owned_id)
            self.assertEqual(document.user_id, self.owner_id)
            self.assertEqual(document.file_path, "private/owned.pdf")
            self.assertEqual(document.raw_text, "PRIVATE_SYNTHETIC_TEXT")

    def test_rename_rejects_cross_user_null_owner_and_missing_without_writes(self) -> None:
        for resume_id in (self.other_resume_id, self.legacy_id, uuid4()):
            with self.subTest(resume_id=resume_id):
                response = self.owner.patch(
                    f"/api/v1/resumes/{resume_id}", json={"file_name": "changed.pdf"}
                )
                self.assertEqual(response.status_code, 404)
        with Session(self.engine) as session:
            names = set(session.scalars(select(ResumeDocument.file_name)))
            self.assertEqual(names, {"owned.pdf", "other.pdf", "legacy.pdf"})

    def test_rename_rejects_forged_owner_and_invalid_filename(self) -> None:
        payloads = [
            {"file_name": "updated.pdf", "user_id": str(self.other_id)},
            {"file_name": "not-pdf.txt"},
            {"file_name": ""},
            {"file_name": "line\nbreak.pdf"},
            {"file_name": "x" * 256 + ".pdf"},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.owner.patch(f"/api/v1/resumes/{self.owned_id}", json=payload)
                self.assertEqual(response.status_code, 422)
        with Session(self.engine) as session:
            document = session.get(ResumeDocument, self.owned_id)
            self.assertEqual(document.file_name, "owned.pdf")
            self.assertEqual(document.user_id, self.owner_id)

    def test_csrf_is_required_for_both_upload_paths_and_metadata_writes(self) -> None:
        csrf_token = self.owner.headers.pop("X-CSRF-Token")
        for path in ("/resume/upload", "/api/v1/resumes"):
            with self.subTest(path=path):
                response = self.owner.post(
                    path, files={"file": ("resume.pdf", make_pdf(), "application/pdf")}
                )
                self.assertEqual(response.status_code, 403)
        response = self.owner.patch(
            f"/api/v1/resumes/{self.owned_id}", json={"file_name": "updated.pdf"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.settings.upload_dir.exists())
        self.owner.headers["X-CSRF-Token"] = csrf_token
        self.assertEqual(
            self.owner.patch(
                f"/api/v1/resumes/{self.owned_id}", json={"file_name": "updated.pdf"}
            ).status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
