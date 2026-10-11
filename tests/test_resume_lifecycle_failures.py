"""Real database/IO failures verify durable compensation and crash recovery."""

import io
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

import test_resume_file_lifecycle as fixtures
from offerpilot_api.db.models import AuthThrottle, ResumeDocument, ResumeFileOperation
from offerpilot_api.resume_ingestion.access import ResumeAccessError, rename_owned_resume
from offerpilot_api.resume_ingestion.lifecycle import (
    ResumeLifecycleError, delete_owned_resume, get_owned_lifecycle, lock_active_upload, recover_pending_files,
)
from offerpilot_api.services.resume import ResumeUploadError, normalize_pdf_filename, save_resume_document


class FailingCommitSession(Session):
    """Execute a real invalid SQL write, or lose a successful acknowledgement."""

    def __init__(self, *args, fail_at: int, lose_ack: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_at = fail_at
        self.lose_ack = lose_ack
        self.commits = 0

    def commit(self):
        self.commits += 1
        if self.commits == self.fail_at:
            if self.lose_ack:
                super().commit()
                raise OperationalError("Synthetic lost commit acknowledgement", None, RuntimeError("Synthetic connection failure"))
            self.add(AuthThrottle(
                key_hash="f" * 64, window_started_at=datetime.now(timezone.utc), attempt_count=-1,
            ))
        return super().commit()


class ProcessInterrupted(BaseException):
    pass


class InterruptedSource(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.reads = 0

    def read(self, size=-1):
        self.reads += 1
        if self.reads == 2:
            raise ProcessInterrupted("Synthetic process interruption")
        return super().read(min(size, 64))


class ResumeLifecycleFailureTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids inheriting/discovering the fixture's 14 tests again.
        self.fixture = fixtures.ResumeFileLifecycleTests(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def _upload_file(self, source=None):
        file = UploadFile(
            file=source if source is not None else io.BytesIO(fixtures.make_pdf()),
            filename="synthetic-failure.pdf", headers=Headers({"content-type": "application/pdf"}),
        )
        self.addCleanup(file.file.close)
        return file

    def _save(self, db, file=None):
        f = self.fixture
        return save_resume_document(
            file or self._upload_file(), db, f.settings.upload_dir,
            user_id=f.owner_id, storage=f.storage,
        )

    def _count(self, model):
        with Session(self.fixture.engine) as db:
            return db.scalar(select(func.count()).select_from(model))

    def _recover(self):
        with Session(self.fixture.engine) as db:
            return recover_pending_files(
                db, self.fixture.storage, stale_before=datetime.now(timezone.utc) + timedelta(seconds=1),
            )

    def test_initial_commit_failure_creates_no_file_or_untracked_record(self):
        with FailingCommitSession(self.fixture.engine, fail_at=1) as db:
            with self.assertRaises(ResumeUploadError) as error:
                self._save(db)
        self.assertEqual(error.exception.status_code, 503)
        self.assertIsInstance(error.exception.resume_id, UUID)
        self.assertEqual(self._count(ResumeDocument), 0)
        self.assertEqual(self._count(ResumeFileOperation), 0)
        self.assertEqual(self.fixture._object_files(), [])

    def test_final_upload_commit_failure_removes_actual_file_claim_and_text(self):
        with FailingCommitSession(self.fixture.engine, fail_at=2) as db:
            with self.assertRaises(ResumeUploadError) as error:
                self._save(db)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self._count(ResumeDocument), 0)
        self.assertEqual(self._count(ResumeFileOperation), 0)
        self.assertEqual(self.fixture._object_files(), [])
        self.assertGreater(self.fixture.storage.delete_count, 0)

    def test_final_commit_acknowledgement_loss_returns_confirmed_success(self):
        f = self.fixture
        with FailingCommitSession(f.engine, fail_at=2, lose_ack=True) as db:
            result = self._save(db)
        self.assertEqual(self._count(ResumeDocument), 1)
        self.assertEqual(self._count(ResumeFileOperation), 0)
        downloaded = f.owner.get(f._endpoint(result.id, "/download"))
        self.assertEqual(downloaded.status_code, 200)
        with Session(f.engine) as db:
            document = db.get(ResumeDocument, result.id)
            self.assertEqual(document.processing_status, "parsed")
            self.assertEqual(downloaded.content, f.storage.read(document.object_key, claim_token=result.id.hex))

    def test_initial_commit_acknowledgement_loss_remains_queryable_and_recoverable(self):
        f = self.fixture
        with FailingCommitSession(f.engine, fail_at=1, lose_ack=True) as db:
            with self.assertRaises(ResumeUploadError) as error:
                self._save(db)
        resume_id = error.exception.resume_id
        self.assertEqual(error.exception.code, "resume_upload_pending")
        state = f.owner.get(f._endpoint(resume_id, "/lifecycle"))
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["processing_status"], "uploading")
        f._assert_hidden(resume_id)
        self.assertEqual(f._object_files(), [])
        self.assertEqual(self._recover().completed, 1)
        self.assertEqual(self._count(ResumeDocument), 0)
        self.assertEqual(self._count(ResumeFileOperation), 0)

    def test_final_delete_commit_failure_hides_pending_resource_until_retry(self):
        f = self.fixture
        resume_id, _ = f._upload()
        profile_id, fact_id = f._derived(resume_id)
        with FailingCommitSession(f.engine, fail_at=2) as db:
            with self.assertRaises(ResumeLifecycleError) as error:
                delete_owned_resume(db, resume_id=resume_id, user_id=f.owner_id, storage=f.storage)
        self.assertEqual(error.exception.code, "resume_delete_pending")
        self.assertEqual(f._object_files(), [])
        f._assert_hidden(resume_id)
        with Session(f.engine) as db:
            self.assertIsNone(db.get(ResumeDocument, resume_id).raw_text)
            self.assertEqual(db.get(ResumeFileOperation, resume_id).status, "deleting")
        result = self._recover()
        self.assertEqual((result.completed, result.failed), (1, 0))
        f._assert_deleted(resume_id, profile_id, fact_id)
        self.assertEqual(f.owner.delete(f._endpoint(resume_id)).status_code, 204)

    def test_failed_upload_cleanup_retains_trackable_failure_then_recovers(self):
        f = self.fixture
        f.storage.fail_save_after_write = True
        f.storage.fail_delete = True
        with Session(f.engine) as db:
            with self.assertRaises(ResumeUploadError) as error:
                self._save(db)
        self.assertEqual(error.exception.code, "resume_cleanup_pending")
        resume_id = error.exception.resume_id
        self.assertEqual(len(f._object_files()), 2)
        f._assert_hidden(resume_id)
        state = f.owner.get(f._endpoint(resume_id, "/lifecycle"))
        self.assertEqual(state.json()["lifecycle_status"], "delete_failed")
        f.storage.fail_delete = False
        result = self._recover()
        self.assertEqual((result.completed, result.failed), (1, 0))
        self.assertEqual(f._object_files(), [])
        self.assertEqual(self._count(ResumeDocument), 0)
        self.assertEqual(self._count(ResumeFileOperation), 0)

    def test_source_interruption_preserves_journal_and_partial_then_recovers(self):
        f = self.fixture
        content = fixtures.make_pdf()
        with Session(f.engine) as db:
            with self.assertRaises(ProcessInterrupted):
                self._save(db, self._upload_file(InterruptedSource(content)))
        with Session(f.engine) as db:
            operation = db.scalar(select(ResumeFileOperation))
            resume_id = operation.resume_id
            self.assertEqual(operation.status, "uploading")
            document = db.get(ResumeDocument, resume_id)
            self.assertIsNone(document.raw_text)
            self.assertEqual(document.processing_status, "uploading")
        files = f._object_files()
        self.assertEqual(sorted(p.suffix for p in files), [".claim", ".part"])
        self.assertEqual(next(p for p in files if p.suffix == ".part").read_bytes(), content[:64])
        self.assertEqual(next(p for p in files if p.suffix == ".part").stat().st_size, 64)
        self.assertEqual(next(p for p in files if p.suffix == ".claim").read_text(), resume_id.hex)
        f._assert_hidden(resume_id)
        self.assertEqual(self._recover().completed, 1)
        self.assertEqual(f._object_files(), [])
        self.assertEqual(self._count(ResumeDocument), 0)
        with Session(f.engine) as db:
            with self.assertRaises(ResumeLifecycleError) as error:
                lock_active_upload(db, resume_id=resume_id, user_id=f.owner_id)
            db.rollback()
        self.assertEqual(error.exception.status_code, 409)

    def test_collision_and_failed_journal_update_never_delete_preexisting_bytes(self):
        f = self.fixture
        resume_id, key_id = uuid4(), uuid4()
        key = f"{key_id.hex}.pdf"
        original = fixtures.make_pdf("Preexisting object must survive")
        f.storage.delegate.save(key, io.BytesIO(original))
        with patch("offerpilot_api.services.resume.uuid.uuid4", side_effect=[resume_id, key_id]):
            with FailingCommitSession(f.engine, fail_at=2) as db:
                with self.assertRaises(ResumeUploadError):
                    self._save(db)
        self.assertEqual(f.storage.delegate.read(key), original)
        result = self._recover()
        self.assertEqual((result.completed, result.failed), (0, 1))
        self.assertEqual(f.storage.delegate.read(key), original)
        deletion = f.owner.delete(f._endpoint(resume_id))
        self.assertEqual(deletion.status_code, 503)
        self.assertEqual(f.storage.delegate.read(key), original)
        with Session(f.engine) as db:
            self.assertEqual(db.get(ResumeFileOperation, resume_id).status, "delete_failed")
            self.assertIsNone(db.get(ResumeDocument, resume_id).raw_text)

    def test_real_recovery_cli_finalizes_failure_and_closes_database(self):
        f = self.fixture
        resume_id, _ = f._upload()
        f.storage.fail_delete = True
        self.assertEqual(f.owner.delete(f._endpoint(resume_id)).status_code, 503)
        env = os.environ.copy()
        env.update({
            "OFFERPILOT_ENVIRONMENT": "development",
            "OFFERPILOT_DATABASE_URL": f.settings.database_url,
            "OFFERPILOT_UPLOAD_DIR": str(f.settings.upload_dir),
        })
        root = Path(__file__).resolve().parents[1]
        env["PYTHONPATH"] = str(root / "backend" / "src")
        command = [sys.executable, "-B", "-m", "offerpilot_api.resume_ingestion.recover", "--limit", "10"]
        result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"completed": 1', result.stdout)
        self.assertEqual(f._object_files(), [])
        f._assert_deleted(resume_id)
        repeated = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn('"attempted": 0', repeated.stdout)

    def test_lifecycle_read_interleaved_with_delete_never_reports_active_failure(self):
        f = self.fixture
        resume_id, _ = f._upload()
        f.storage.fail_delete = True

        def delete_in_other_transaction():
            with Session(f.engine) as deleting:
                with self.assertRaises(ResumeLifecycleError):
                    delete_owned_resume(deleting, resume_id=resume_id, user_id=f.owner_id, storage=f.storage)

        class InterleavedReadSession(Session):
            def scalar(session, *args, **kwargs):
                result = super().scalar(*args, **kwargs)
                if isinstance(result, ResumeDocument):
                    # The document has really been read. Commit a real delete
                    # intent before reading its journal in this transaction.
                    delete_in_other_transaction()
                return result

        with InterleavedReadSession(f.engine) as db:
            state = get_owned_lifecycle(db, resume_id=resume_id, user_id=f.owner_id)
        self.assertEqual(state["lifecycle_status"], "delete_failed")
        self.assertEqual(state["deletion_status"], "delete_failed")
        f._assert_hidden(resume_id)

    def test_rename_interleaved_with_delete_cannot_update_hidden_resource(self):
        f = self.fixture
        resume_id, _ = f._upload()
        f.storage.fail_delete = True

        def normalize_then_delete(name):
            normalized = normalize_pdf_filename(name)
            with Session(f.engine) as deleting:
                with self.assertRaises(ResumeLifecycleError):
                    delete_owned_resume(deleting, resume_id=resume_id, user_id=f.owner_id, storage=f.storage)
            return normalized

        with patch("offerpilot_api.resume_ingestion.access.normalize_pdf_filename", side_effect=normalize_then_delete):
            with Session(f.engine) as db:
                with self.assertRaises(ResumeAccessError) as error:
                    rename_owned_resume(db, resume_id=resume_id, user_id=f.owner_id, file_name="after.pdf")
        self.assertEqual(error.exception.status_code, 404)
        with Session(f.engine) as db:
            document = db.get(ResumeDocument, resume_id)
            self.assertEqual(document.file_name, "owner.pdf")
            self.assertEqual(document.lifecycle_status, "delete_failed")

    def test_owned_s1_1_file_remains_downloadable_deletable_and_null_owner_untouched(self):
        f = self.fixture
        owned_key, null_key = f"{uuid4().hex}.pdf", f"{uuid4().hex}.pdf"
        owned_pdf, null_pdf = fixtures.make_pdf("owned legacy"), fixtures.make_pdf("unowned legacy")
        f.storage.delegate.save(owned_key, io.BytesIO(owned_pdf))
        f.storage.delegate.save(null_key, io.BytesIO(null_pdf))
        with Session(f.engine) as db:
            owned = ResumeDocument(
                user_id=f.owner_id, file_name="owned-legacy.pdf", file_type="application/pdf",
                file_path=str(f.settings.upload_dir / owned_key), raw_text="Synthetic owned legacy text",
            )
            unowned = ResumeDocument(
                user_id=None, file_name="null-legacy.pdf", file_type="application/pdf",
                file_path=str(f.settings.upload_dir / null_key), raw_text="Synthetic unowned legacy text",
            )
            db.add_all([owned, unowned])
            db.commit()
            owned_id, null_id = owned.id, unowned.id
        profile_id, fact_id = f._derived(owned_id)
        self.assertEqual(f.owner.get(f._endpoint(owned_id, "/download")).content, owned_pdf)
        self.assertEqual(f.other.get(f._endpoint(owned_id, "/download")).status_code, 404)
        self.assertEqual(f.owner.get(f._endpoint(null_id, "/download")).status_code, 404)
        self.assertEqual(f.owner.delete(f._endpoint(null_id)).status_code, 404)
        self.assertEqual(f.owner.delete(f._endpoint(owned_id)).status_code, 204)
        self.assertFalse((f.settings.upload_dir / owned_key).exists())
        self.assertEqual((f.settings.upload_dir / null_key).read_bytes(), null_pdf)
        f._assert_deleted(owned_id, profile_id, fact_id)
        with Session(f.engine) as db:
            self.assertEqual(db.get(ResumeDocument, null_id).raw_text, "Synthetic unowned legacy text")
            self.assertIsNone(db.get(ResumeDocument, null_id).user_id)


if __name__ == "__main__":
    unittest.main()
