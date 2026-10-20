"""Private PDF lifecycle tests using real identity and real storage.

Only the storage boundary injects I/O failures or scheduling barriers. Business
services, authorization, cookie sessions, CSRF and persistence remain real.
"""

from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.account_lifecycle.ledger import DeletionLedger
from offerpilot_api.api.routes import resume as resume_routes
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import Profile, ProfileFact, ResumeDocument, ResumeFileOperation
from offerpilot_api.db.session import build_engine, get_db
from offerpilot_api.identity.services import create_invitation
from offerpilot_api.main import create_app
from offerpilot_api.resume_ingestion.lifecycle import ResumeLifecycleError, complete_resume_upload, recover_pending_files
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, StorageError


def make_pdf(label: str = "Python") -> bytes:
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), f"Synthetic lifecycle resume: {label}")
        return pdf.tobytes()


class ControlledStorage:
    """Forward real I/O, with explicit failure and thread barriers for tests."""

    def __init__(self, delegate: LocalResumeStorage) -> None:
        self.delegate = delegate
        self.fail_delete = False
        self.fail_read = False
        self.fail_save_after_write = False
        self.block_read = False
        self.block_save = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.delete_count = 0

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def _barrier(self) -> None:
        self.entered.set()
        if not self.release.wait(10):
            raise StorageError("synthetic_io_failure", "Synthetic storage barrier timed out")

    def save(self, *args, **kwargs):
        if self.block_save:
            self._barrier()
        result = self.delegate.save(*args, **kwargs)
        if self.fail_save_after_write:
            raise StorageError("synthetic_io_failure", "Synthetic storage write failure")
        return result

    def read(self, *args, **kwargs):
        if self.block_read:
            self._barrier()
        if self.fail_read:
            raise StorageError("synthetic_io_failure", "Synthetic storage read failure")
        return self.delegate.read(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.delete_count += 1
        if self.fail_delete:
            raise StorageError("synthetic_io_failure", "Synthetic storage delete failure")
        return self.delegate.delete(*args, **kwargs)


class ResumeFileLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="offerpilot-lifecycle-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        database_url = f"sqlite:///{(self.root / 'lifecycle.db').as_posix()}"
        self.engine = build_engine(database_url)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.settings = Settings(
            database_url=database_url,
            upload_dir=self.root / "private-objects",
            deletion_ledger_dir=self.root / "deletion-ledger",
            session_cookie_secure=False,
        )
        DeletionLedger(self.settings.deletion_ledger_dir).initialize()
        self.storage = ControlledStorage(LocalResumeStorage(self.settings.upload_dir))
        self.addCleanup(self.storage.release.set)
        self.app = create_app()

        def test_db():
            with Session(self.engine) as db:
                yield db

        self.app.dependency_overrides[get_db] = test_db
        self.app.dependency_overrides[get_settings] = lambda: self.settings
        self.app.dependency_overrides[resume_routes.get_resume_storage] = lambda: self.storage
        self.anonymous = TestClient(self.app)
        self.addCleanup(self.anonymous.close)
        self.owner, self.owner_id = self._register("lifecycle-owner@example.com")
        self.other, self.other_id = self._register("lifecycle-other@example.com")

    def _register(self, email: str) -> tuple[TestClient, UUID]:
        client = TestClient(self.app)
        self.addCleanup(client.close)
        with Session(self.engine) as db:
            invite_token = create_invitation(db, email=email)
        password = "Synthetic-lifecycle-password-2026"
        registration = client.post("/api/v1/auth/register", json={
            "email": email, "password": password, "invite_token": invite_token,
        })
        self.assertEqual(registration.status_code, 201)
        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        self.assertEqual(login.status_code, 200)
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        return client, UUID(login.json()["user"]["id"])

    def _upload(self, client: TestClient | None = None, *, label: str = "owner") -> tuple[UUID, bytes]:
        content = make_pdf(label)
        response = (client or self.owner).post(
            "/api/v1/resumes", files={"file": (f"{label}.pdf", content, "application/pdf")},
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("file_path", response.text)
        self.assertNotIn("object_key", response.text)
        return UUID(response.json()["id"]), content

    def _endpoint(self, resume_id: UUID, suffix: str = "") -> str:
        return f"/api/v1/resumes/{resume_id}{suffix}"

    def _derived(self, resume_id: UUID) -> tuple[UUID, UUID]:
        with Session(self.engine) as db:
            profile = Profile(resume_id=resume_id, name="Synthetic lifecycle student")
            fact = ProfileFact(
                profile=profile, field_name="skills[0]", value="Python",
                confidence=0.9, source_text="Synthetic Python",
            )
            db.add(profile)
            db.commit()
            return profile.id, fact.id

    def _files(self) -> list[Path]:
        return [path for path in self.settings.upload_dir.rglob("*") if path.is_file() and path.suffix == ".pdf"]

    def _object_files(self) -> list[Path]:
        return [path for path in self.settings.upload_dir.rglob("*") if path.is_file()]

    def _assert_error(self, response, status: int, code: str) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["detail"]["code"], code)
        self.assertNotIn(str(self.root), response.text)
        self.assertNotIn("Synthetic lifecycle", response.text)

    def _assert_hidden(self, resume_id: UUID) -> None:
        for suffix in ("", "/download", "/profile", "/profile/facts"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.owner.get(self._endpoint(resume_id, suffix)).status_code, 404)
        listed = self.owner.get("/api/v1/resumes")
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(str(resume_id), [item["id"] for item in listed.json()])

    def _assert_deleted(self, resume_id: UUID, profile_id: UUID | None = None, fact_id: UUID | None = None) -> None:
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ResumeDocument, resume_id))
            if profile_id is not None:
                self.assertIsNone(db.get(Profile, profile_id))
            if fact_id is not None:
                self.assertIsNone(db.get(ProfileFact, fact_id))
            operation = db.get(ResumeFileOperation, resume_id)
            self.assertIsNotNone(operation)
            self.assertEqual(operation.user_id, self.owner_id)
            self.assertEqual(operation.status, "deleted")
            self.assertIsNone(operation.object_key)
            self.assertIsNone(operation.legacy_path)
            self.assertIsNone(operation.error_code)
        lifecycle = self.owner.get(self._endpoint(resume_id, "/lifecycle"))
        self.assertEqual(lifecycle.status_code, 200)
        self.assertEqual(set(lifecycle.json()), {
            "id", "processing_status", "lifecycle_status", "deletion_status", "error_code", "attempt_count",
        })
        self.assertIsNone(lifecycle.json()["processing_status"])
        self.assertEqual(lifecycle.json()["lifecycle_status"], "deleted")
        self.assertEqual(lifecycle.json()["deletion_status"], "deleted")
        self._assert_hidden(resume_id)

    def _mark_interrupted_upload(self, resume_id: UUID, *, stale: bool) -> None:
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db:
            document = db.get(ResumeDocument, resume_id)
            document.processing_status = "uploading"
            operation = db.get(ResumeFileOperation, resume_id)
            if operation is None:
                operation = ResumeFileOperation(
                    resume_id=resume_id, user_id=self.owner_id,
                    object_key=document.object_key, reason="upload", status="uploading",
                )
                db.add(operation)
            else:
                operation.reason = "upload"
                operation.status = "uploading"
                operation.object_key = document.object_key
            operation.updated_at = now - timedelta(hours=2) if stale else now
            operation.created_at = operation.updated_at
            db.commit()

    def test_upload_uses_private_object_with_exact_bytes_and_metadata(self) -> None:
        resume_id, content = self._upload()
        with Session(self.engine) as db:
            document = db.get(ResumeDocument, resume_id)
            self.assertEqual(document.user_id, self.owner_id)
            self.assertEqual(document.file_path, "")
            self.assertTrue(document.object_key)
            self.assertEqual(document.byte_size, len(content))
            self.assertEqual(document.sha256, hashlib.sha256(content).hexdigest())
            self.assertIn("Synthetic lifecycle resume", document.raw_text)
            self.assertEqual(document.processing_status, "parsed")
            self.assertEqual(document.lifecycle_status, "active")
            self.assertEqual(self.storage.read(document.object_key, claim_token=resume_id.hex), content)
            marker = self.settings.upload_dir / f"{document.object_key}.claim"
            self.assertEqual(marker.read_bytes(), resume_id.hex.encode("ascii"))
        self.assertEqual(len(self._files()), 1)
        lifecycle = self.owner.get(self._endpoint(resume_id, "/lifecycle"))
        self.assertEqual(lifecycle.status_code, 200)
        self.assertEqual(lifecycle.json()["processing_status"], "parsed")
        self.assertEqual(lifecycle.json()["lifecycle_status"], "active")
        self.assertIsNone(lifecycle.json()["deletion_status"])

    def test_download_returns_original_pdf_and_private_response_headers(self) -> None:
        resume_id, content = self._upload()
        response = self.owner.get(self._endpoint(resume_id, "/download"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertNotIn(str(self.settings.upload_dir), response.headers["content-disposition"])

    def test_anonymous_cross_user_null_owner_and_unknown_ids_are_rejected(self) -> None:
        resume_id, content = self._upload()
        with Session(self.engine) as db:
            legacy = ResumeDocument(
                user_id=None, file_name="legacy.pdf", file_path="legacy/private.pdf",
                file_type="application/pdf", raw_text="PRIVATE_LEGACY_TEXT",
            )
            db.add(legacy)
            db.commit()
            legacy_id = legacy.id
        for method, suffix in (("GET", "/download"), ("GET", "/lifecycle"), ("DELETE", "")):
            self.assertEqual(self.anonymous.request(method, self._endpoint(resume_id, suffix)).status_code, 401)
            self.assertEqual(self.other.request(method, self._endpoint(resume_id, suffix)).status_code, 404)
            for hidden_id in (legacy_id, uuid4()):
                with self.subTest(method=method, suffix=suffix):
                    response = self.owner.request(method, self._endpoint(hidden_id, suffix))
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn("PRIVATE_LEGACY_TEXT", response.text)
        self.assertEqual(self.owner.get(self._endpoint(resume_id, "/download")).content, content)

    def test_delete_requires_csrf_and_preserves_file_after_rejection(self) -> None:
        resume_id, content = self._upload()
        csrf = self.owner.headers.pop("X-CSRF-Token")
        response = self.owner.delete(self._endpoint(resume_id))
        self.assertEqual(response.status_code, 403)
        self.owner.headers["X-CSRF-Token"] = csrf
        response = self.owner.delete(self._endpoint(resume_id), headers={"Origin": "https://untrusted.invalid"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.storage.delete_count, 0)
        self.assertEqual(self.owner.get(self._endpoint(resume_id, "/download")).content, content)

    def test_delete_hard_cleans_derived_data_keeps_other_resource_and_is_idempotent(self) -> None:
        resume_id, content = self._upload()
        profile_id, fact_id = self._derived(resume_id)
        other_id, other_content = self._upload(self.other, label="other")
        original = next(path for path in self._files() if path.read_bytes() == content)
        other_original = next(path for path in self._files() if path.read_bytes() == other_content)
        marker = original.with_name(f"{original.name}.claim")
        other_marker = other_original.with_name(f"{other_original.name}.claim")
        self.assertEqual(self.owner.delete(self._endpoint(resume_id)).status_code, 204)
        self._assert_deleted(resume_id, profile_id, fact_id)
        calls = self.storage.delete_count
        self.assertEqual(self.owner.delete(self._endpoint(resume_id)).status_code, 204)
        self.assertEqual(self.storage.delete_count, calls)
        self.assertFalse(original.exists())
        self.assertFalse(marker.exists())
        self.assertEqual(other_original.read_bytes(), other_content)
        self.assertEqual(other_marker.read_bytes(), other_id.hex.encode("ascii"))
        self.assertEqual(self.other.get(self._endpoint(other_id, "/download")).content, other_content)
        for suffix in ("/lifecycle", "/download"):
            self.assertEqual(self.other.get(self._endpoint(resume_id, suffix)).status_code, 404)

    def test_missing_owned_original_is_410_without_exposing_storage_location(self) -> None:
        resume_id, _content = self._upload()
        with Session(self.engine) as db:
            object_key = db.get(ResumeDocument, resume_id).object_key
        self.storage.delete(object_key, claim_token=resume_id.hex)
        response = self.owner.get(self._endpoint(resume_id, "/download"))
        self._assert_error(response, 410, "resume_file_missing")
        self.assertNotIn(object_key, response.text)
        self.assertEqual(self.other.get(self._endpoint(resume_id, "/download")).status_code, 404)
        self.assertEqual(self.owner.delete(self._endpoint(resume_id)).status_code, 204)
        self._assert_deleted(resume_id)

    def test_delete_storage_failure_hides_all_normal_reads_then_retry_finishes(self) -> None:
        resume_id, content = self._upload()
        profile_id, fact_id = self._derived(resume_id)
        self.storage.fail_delete = True
        response = self.owner.delete(self._endpoint(resume_id))
        self._assert_error(response, 503, "resume_delete_failed")
        self._assert_hidden(resume_id)
        lifecycle = self.owner.get(self._endpoint(resume_id, "/lifecycle"))
        self.assertEqual(lifecycle.status_code, 200)
        self.assertEqual(lifecycle.json()["deletion_status"], "delete_failed")
        self.assertGreaterEqual(lifecycle.json()["attempt_count"], 1)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ResumeDocument, resume_id).raw_text)
            self.assertIsNone(db.get(Profile, profile_id))
            self.assertIsNone(db.get(ProfileFact, fact_id))
        self.assertTrue(any(path.read_bytes() == content for path in self._files()))
        self.storage.fail_delete = False
        self.assertEqual(self.owner.delete(self._endpoint(resume_id)).status_code, 204)
        self._assert_deleted(resume_id, profile_id, fact_id)
        self.assertEqual(self._object_files(), [])

    def test_client_supplied_storage_key_and_path_cannot_redirect_download_or_delete(self) -> None:
        resume_id, content = self._upload()
        other_id, other_content = self._upload(self.other, label="other")
        with Session(self.engine) as db:
            other_key = db.get(ResumeDocument, other_id).object_key
        forged = {"object_key": other_key, "file_path": "../../outside.pdf", "user_id": str(self.other_id)}
        response = self.owner.get(self._endpoint(resume_id, "/download"), params=forged)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content)
        self.assertEqual(self.owner.delete(self._endpoint(resume_id), params=forged).status_code, 204)
        self.assertEqual(self.other.get(self._endpoint(other_id, "/download")).content, other_content)

    def test_interrupted_upload_is_hidden_and_recovery_removes_stale_only(self) -> None:
        stale_id, stale_content = self._upload(label="stale")
        recent_id, recent_content = self._upload(label="recent")
        stale_original = next(path for path in self._files() if path.read_bytes() == stale_content)
        recent_original = next(path for path in self._files() if path.read_bytes() == recent_content)
        self._mark_interrupted_upload(stale_id, stale=True)
        self._mark_interrupted_upload(recent_id, stale=False)
        self._assert_hidden(stale_id)
        self._assert_hidden(recent_id)
        with Session(self.engine) as db:
            result = recover_pending_files(
                db, self.storage,
                stale_before=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        self.assertEqual(result.completed, 1)
        self.assertEqual(result.failed, 0)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ResumeDocument, stale_id))
            self.assertIsNone(db.get(ResumeFileOperation, stale_id))
            self.assertIsNotNone(db.get(ResumeDocument, recent_id))
        self._assert_hidden(stale_id)
        self.assertEqual(self.owner.get(self._endpoint(stale_id, "/lifecycle")).status_code, 404)
        self.assertFalse(any(path.read_bytes() == stale_content for path in self._files()))
        self.assertTrue(any(path.read_bytes() == recent_content for path in self._files()))
        self.assertFalse(stale_original.with_name(f"{stale_original.name}.claim").exists())
        self.assertEqual(recent_original.with_name(f"{recent_original.name}.claim").read_bytes(), recent_id.hex.encode("ascii"))

    def test_recovery_retries_failed_delete_and_second_recovery_is_safe(self) -> None:
        resume_id, _content = self._upload()
        profile_id, fact_id = self._derived(resume_id)
        self.storage.fail_delete = True
        self.assertEqual(self.owner.delete(self._endpoint(resume_id)).status_code, 503)
        self.storage.fail_delete = False
        with Session(self.engine) as db:
            result = recover_pending_files(db, self.storage, stale_before=datetime.now(timezone.utc))
        self.assertEqual(result.completed, 1)
        self._assert_deleted(resume_id, profile_id, fact_id)
        calls = self.storage.delete_count
        with Session(self.engine) as db:
            repeated = recover_pending_files(db, self.storage, stale_before=datetime.now(timezone.utc))
        self.assertEqual(repeated.attempted, 0)
        self.assertEqual(self.storage.delete_count, calls)
        self.assertEqual(self._object_files(), [])

    def test_partial_upload_write_failure_cleans_original_and_document(self) -> None:
        self.storage.fail_save_after_write = True
        response = self.owner.post("/api/v1/resumes", files={"file": ("failed.pdf", make_pdf(), "application/pdf")})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.root), response.text)
        self.assertEqual(self._object_files(), [])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ResumeDocument)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Profile)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(ProfileFact)), 0)

    def test_download_buffer_started_before_delete_can_finish_then_access_is_revoked(self) -> None:
        resume_id, content = self._upload()
        self.storage.block_read = True
        with ThreadPoolExecutor(max_workers=2) as pool:
            read_future = pool.submit(self.owner.get, self._endpoint(resume_id, "/download"))
            self.assertTrue(self.storage.entered.wait(5))
            delete_future = pool.submit(self.owner.delete, self._endpoint(resume_id))
            try:
                # The database lock serializes deletion against this open I/O;
                # the completed byte buffer may finish before delete commits.
                self.assertFalse(delete_future.done())
            finally:
                self.storage.release.set()
            downloaded = read_future.result(timeout=10)
            deleted = delete_future.result(timeout=10)
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, content)
        self.assertEqual(deleted.status_code, 204)
        self._assert_deleted(resume_id)
        self.assertEqual(self._object_files(), [])

    def test_upload_io_and_delete_are_serialized_without_reviving_data(self) -> None:
        content = make_pdf("concurrent-upload")
        self.storage.block_save = True
        with ThreadPoolExecutor(max_workers=2) as pool:
            upload_future = pool.submit(
                self.owner.post, "/api/v1/resumes",
                files={"file": ("concurrent.pdf", content, "application/pdf")},
            )
            self.assertTrue(self.storage.entered.wait(5))
            with Session(self.engine) as db:
                pending = db.scalar(select(ResumeDocument).where(ResumeDocument.user_id == self.owner_id))
                self.assertIsNotNone(pending)
                resume_id = pending.id
            # Metadata reads do not request a competing write lock; download
            # deliberately serializes with the in-flight object write.
            for suffix in ("", "/profile", "/profile/facts"):
                self.assertEqual(self.owner.get(self._endpoint(resume_id, suffix)).status_code, 404)
            delete_future = pool.submit(self.owner.delete, self._endpoint(resume_id))
            try:
                self.assertFalse(delete_future.done())
            finally:
                self.storage.release.set()
            uploaded = upload_future.result(timeout=10)
            deleted = delete_future.result(timeout=10)
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(deleted.status_code, 204)
        self._assert_deleted(resume_id)
        self.assertEqual(self._object_files(), [])

    def test_late_processing_completion_and_fk_writes_cannot_resurrect_deleted_data(self) -> None:
        resume_id, content = self._upload()
        profile_id, fact_id = self._derived(resume_id)
        self.assertEqual(self.owner.delete(self._endpoint(resume_id)).status_code, 204)
        with Session(self.engine) as db:
            with self.assertRaises(ResumeLifecycleError) as caught:
                complete_resume_upload(
                    db, resume_id=resume_id, user_id=self.owner_id,
                    raw_text="Synthetic late processing result", byte_size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(caught.exception.code, "processing_result_discarded")
            db.rollback()
            db.add(Profile(resume_id=resume_id, name="Synthetic late profile"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            db.add(ProfileFact(profile_id=profile_id, field_name="skills[0]", value="Python", confidence=0.9, source_text="Synthetic late fact"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
        self._assert_deleted(resume_id, profile_id, fact_id)
        self.assertEqual(self._object_files(), [])


if __name__ == "__main__":
    unittest.main()
