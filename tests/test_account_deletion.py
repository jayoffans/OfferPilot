"""Real account/session boundaries, physical erasure and interrupted recovery."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import test_job_catalog as harness
import test_account_exports as export_harness
from offerpilot_api.account_lifecycle.deletion import process_deletion, replay_deletion_registry
from offerpilot_api.account_lifecycle.ledger import DeletionLedger, LedgerError
from offerpilot_api.account_lifecycle.exports import ExportFiles
from offerpilot_api.agent_runtime.byok import SessionKeyVault
from offerpilot_api.core.config import get_settings
from offerpilot_api.db import models as m
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, token_hash
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, StorageError


class AccountDeletionTests(unittest.TestCase):
    client = export_harness.AccountExportTests.client
    account = export_harness.AccountExportTests.account
    login = export_harness.AccountExportTests.login
    payload = export_harness.AccountExportTests.payload
    create = export_harness.AccountExportTests.create
    save = export_harness.AccountExportTests.save
    upload = export_harness.AccountExportTests.upload
    profile = export_harness.AccountExportTests.profile
    job = export_harness.AccountExportTests.job
    calculate = export_harness.AccountExportTests.calculate
    application_payload = export_harness.AccountExportTests.application_payload
    application = export_harness.AccountExportTests.application
    request_export = export_harness.AccountExportTests.request_export
    run_export = export_harness.AccountExportTests.run_export

    def setUp(self):
        export_harness.AccountExportTests.setUp(self)

    def request_deletion(self, client=None, expected=202, **changes):
        body = {"password": harness.PASSWORD, "confirmation": "注销账户", "idempotency_key": uuid4().hex, **changes}
        response = (client or self.owner).post("/api/v1/me/deletion", json=body)
        self.assertEqual(response.status_code, expected, response.text)
        return response

    def process(self, data, storage=None):
        with Session(self.engine) as db:
            return process_deletion(db, deletion_id=UUID(data["id"]), settings=self.settings, storage=storage or self.storage)

    def replay(self, storage=None):
        with Session(self.engine) as db:
            return replay_deletion_registry(db, settings=self.settings, storage=storage or self.storage, limit=100)

    def test_password_csrf_confirmation_and_client_owner_are_required(self):
        self.request_deletion(expected=403, password="incorrect-password")
        invalid_unicode = self.owner.post("/api/v1/me/deletion", content=json.dumps({
            "password": "\ud800", "confirmation": "注销账户", "idempotency_key": uuid4().hex}),
            headers={"Content-Type": "application/json"})
        self.assertEqual(invalid_unicode.status_code, 422)
        self.request_deletion(expected=422, confirmation="退出登录")
        self.request_deletion(expected=422, user_id=str(self.other_id))
        csrf = self.owner.headers.pop("X-CSRF-Token")
        self.request_deletion(expected=403)
        self.owner.headers["X-CSRF-Token"] = csrf
        self.request_deletion(client=self.anonymous, expected=401)
        self.assertEqual(self.owner.get("/api/v1/me").status_code, 200)

    def test_all_sessions_revoked_immediately_and_no_new_business_data(self):
        second = self.client()
        self.login(second, "job-owner@example.com")
        data = self.request_deletion().json()
        for client in (self.owner, second):
            self.assertEqual(client.get("/api/v1/me").status_code, 401)
            self.assertEqual(client.post("/api/v1/jobs", json=self.payload()).status_code, 401)
        self.assertEqual(self.owner.get("/api/v1/me/deletion/status").json()["id"], data["id"])
        with Session(self.engine) as db:
            self.assertFalse(db.get(m.User, self.owner_id).is_active)
            self.assertTrue(all(row.revoked_at for row in db.scalars(select(m.AuthSession).where(m.AuthSession.user_id == self.owner_id))))

    def test_complete_database_file_export_erasure_preserves_other_user_and_legacy(self):
        resume, _ = self.upload()
        other_resume, other_bytes = self.upload(client=self.other)
        profile = self.profile()
        job = self.job()
        match = self.calculate(job, profile).json()
        self.application(job, match_id=match["id"])
        exported = self.run_export()
        with Session(self.engine) as db:
            legacy = m.ResumeDocument(file_name="legacy.pdf", file_path="legacy-unknown", file_type="application/pdf", raw_text="保留无归属")
            db.add(legacy)
            db.commit()
            legacy_id = legacy.id
        data = self.request_deletion().json()
        self.assertTrue(self.process(data))
        self.assertTrue(self.process(data))
        self.assertEqual(self.replay()["failed"], 0)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(m.User, self.owner_id))
            self.assertIsNotNone(db.get(m.User, self.other_id))
            self.assertEqual(db.get(m.ResumeDocument, legacy_id).raw_text, "保留无归属")
            for table in m.Base.metadata.tables.values():
                if "user_id" in table.c and table.name != "account_deletions":
                    self.assertEqual(db.scalar(select(func.count()).select_from(table).where(table.c.user_id == self.owner_id)), 0, table.name)
        self.assertFalse(ExportFiles(self.settings.export_dir).path(UUID(exported["id"]), ".zip").exists())
        self.assertEqual(self.other.get(f"/api/v1/resumes/{other_resume['id']}/download").content, other_bytes)
        self.assertEqual(self.owner.get("/api/v1/me/deletion/status").json()["status"], "completed")
        self.assertEqual(self.other.get("/api/v1/me/deletion/status").status_code, 404)
        self.assertEqual(self.other.get("/api/v1/me").status_code, 200)

    def test_status_cookie_short_lived_httponly_and_response_loss_fallback(self):
        response = self.request_deletion()
        cookies = response.headers.get_list("set-cookie")
        self.assertTrue(any("HttpOnly" in c and "Max-Age=3600" in c and "SameSite=strict" in c for c in cookies))
        self.owner.cookies.delete("offerpilot_deletion_status")
        self.assertEqual(self.owner.get("/api/v1/me/deletion/status").status_code, 200)
        with Session(self.engine) as db:
            receipt = db.get(m.AccountDeletion, UUID(response.json()["id"]))
            receipt.status_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.owner.get("/api/v1/me/deletion/status").status_code, 404)
        self.replay()
        with Session(self.engine) as db:
            receipt = db.get(m.AccountDeletion, UUID(response.json()["id"]))
            self.assertIsNone(receipt.status_token_hash)
            self.assertIsNone(receipt.status_session_hash)

    def test_file_failure_is_visible_and_retry_erases_actual_bytes(self):
        self.upload()
        data = self.request_deletion().json()
        with patch.object(self.storage, "delete", side_effect=StorageError("unavailable", "sensitive/path")):
            self.assertFalse(self.process(data))
        status = self.owner.get("/api/v1/me/deletion/status").json()
        self.assertEqual((status["status"], status["error_code"]), ("failed", "account_cleanup_failed"))
        self.assertNotIn("sensitive", json.dumps(status))
        self.assertTrue(self.process(data))
        self.assertEqual(list(self.settings.upload_dir.iterdir()), [])

    def test_export_cleanup_failure_remains_recoverable(self):
        self.run_export()
        data = self.request_deletion().json()
        with patch.object(ExportFiles, "delete", side_effect=OSError("private/path")):
            self.assertFalse(self.process(data))
        self.assertEqual(self.owner.get("/api/v1/me/deletion/status").json()["status"], "failed")
        self.assertTrue(self.process(data))

    def test_persisted_intent_survives_process_loss_before_database_commit(self):
        self.upload()
        ledger = DeletionLedger(self.settings.deletion_ledger_dir)
        deletion_id = uuid4()
        ledger.record(self.owner_id, deletion_id)
        self.assertEqual(self.owner.get("/api/v1/me").status_code, 401)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(m.AccountDeletion, deletion_id))
        self.assertEqual(self.replay()["completed"], 1)
        self.assertEqual(self.replay()["failed"], 0)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(m.User, self.owner_id))

    def test_expired_processing_lease_recovers_after_restart(self):
        self.upload()
        data = self.request_deletion().json()
        with Session(self.engine) as db:
            receipt = db.get(m.AccountDeletion, UUID(data["id"]))
            receipt.status, receipt.lease_deadline = "processing", datetime.now(timezone.utc) + timedelta(minutes=1)
            db.commit()
        self.assertFalse(self.process(data))
        with Session(self.engine) as db:
            receipt = db.get(m.AccountDeletion, UUID(data["id"]))
            receipt.lease_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertTrue(self.process(data))

    def test_unrecoverable_file_error_is_bounded_and_minimal(self):
        self.upload()
        self.settings.deletion_max_attempts = 2
        data = self.request_deletion().json()
        with patch.object(self.storage, "delete", side_effect=StorageError("bad_claim", "private")):
            self.assertFalse(self.process(data))
            self.assertFalse(self.process(data))
        self.assertFalse(self.process(data))
        status = self.owner.get("/api/v1/me/deletion/status").json()
        self.assertEqual((status["status"], status["attempt_count"]), ("manual_review", 2))

    def test_stale_request_cannot_commit_new_business_rows_after_intent(self):
        with Session(self.engine) as stale:
            stale.info.update(account_user_id=self.owner_id, account_settings=self.settings)
            stale.get(m.User, self.owner_id)
            self.request_deletion()
            stale.add(m.CareerProfile(user_id=self.owner_id))
            with self.assertRaises(Exception) as failure:
                stale.commit()
            self.assertEqual(getattr(failure.exception, "status_code", None), 401)
            stale.rollback()
        self.assertEqual(self.other.get("/api/v1/me").status_code, 200)

    def test_ledger_failure_prevents_deletion_and_never_exposes_details(self):
        with patch.object(DeletionLedger, "record", side_effect=LedgerError("secret/path")):
            response = self.request_deletion(expected=503)
        self.assertNotIn("secret", response.text)
        self.assertEqual(self.owner.get("/api/v1/me").status_code, 200)

    def test_registry_minimality_no_email_password_or_tokens(self):
        data = self.request_deletion().json()
        entries = list(DeletionLedger(self.settings.deletion_ledger_dir).entries())
        self.assertEqual(len(entries), 1)
        self.assertEqual(set(entries[0]), {"user_id", "deletion_id", "created_at"})
        self.assertEqual(entries[0]["deletion_id"], data["id"])
        self.assertNotIn(harness.PASSWORD, json.dumps(entries))

    def test_repeated_http_request_cannot_start_a_second_deletion(self):
        key = uuid4().hex
        data = self.request_deletion(idempotency_key=key).json()
        self.request_deletion(expected=401, idempotency_key=key)
        self.assertEqual(self.owner.get("/api/v1/me/deletion/status").json()["id"], data["id"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(m.AccountDeletion)), 1)

    def test_all_owner_keys_and_queued_runs_are_revoked_without_touching_other_key(self):
        from offerpilot_api.agent_runtime.extraction_runs import create_extraction_run, source_version
        from offerpilot_api.agent_runtime.extraction_schemas import ExtractionRunCreateRequest
        self.settings.mock_extraction_enabled = True
        resume, _ = self.upload()
        vault = self.app.state.byok_vault
        with Session(self.engine) as db:
            for owner_id in (self.owner_id, self.other_id):
                user = db.get(m.User, owner_id)
                session = db.scalar(select(m.AuthSession).where(m.AuthSession.user_id == owner_id))
                vault.put(user=user, session=session, api_key="synthetic-key-never-sent", ttl_seconds=60)
            document = db.get(m.ResumeDocument, UUID(resume["id"]))
            run = create_extraction_run(db, user_id=self.owner_id, resume_id=document.id, settings=self.settings,
                payload=ExtractionRunCreateRequest(idempotency_key=uuid4().hex, source_version=source_version(document), mode="mock"))
            run_id = run.id
        self.request_deletion()
        with Session(self.engine) as db:
            run = db.get(m.ResumeExtractionRun, run_id)
            self.assertEqual((run.status, run.execution_token, run.lease_deadline), ("cancelled", None, None))
        self.assertFalse(any(item.user_id == self.owner_id for item in vault._keys.values()))
        self.assertTrue(any(item.user_id == self.other_id for item in vault._keys.values()))

    def test_final_database_commit_failure_preserves_retry_after_files_are_gone(self):
        from sqlalchemy.exc import SQLAlchemyError
        self.upload()
        data = self.request_deletion().json()
        with Session(self.engine) as db:
            original = db.commit
            fired = False
            def commit():
                nonlocal fired
                receipt = db.get(m.AccountDeletion, UUID(data["id"]))
                if receipt is not None and receipt.status == "completed" and not fired:
                    fired = True
                    raise SQLAlchemyError("synthetic final commit failure")
                original()
            with patch.object(db, "commit", side_effect=commit):
                self.assertFalse(process_deletion(db, deletion_id=UUID(data["id"]), settings=self.settings, storage=self.storage))
            self.assertTrue(fired)
            self.assertIsNotNone(db.get(m.User, self.owner_id))
        self.assertFalse(list(self.settings.upload_dir.glob("*")))
        self.assertTrue(self.process(data))
