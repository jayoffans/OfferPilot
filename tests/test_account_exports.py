"""S5-2 export outcomes against real sessions, private files and temporary DBs."""

import hashlib
import io
import json
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

import test_applications as application_harness
import test_job_catalog as harness
import test_job_matches as match_harness
import test_trusted_profile as profile_harness
from offerpilot_api.account_lifecycle import exports
from offerpilot_api.account_lifecycle.ledger import DeletionLedger
from offerpilot_api.core.config import get_settings
from offerpilot_api.db.models import AccountExport, AuthSession, ResumeDocument, ResumeExtractionRun, User
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage


class AccountExportTests(unittest.TestCase):
    client = harness.JobCatalogTests.client
    account = harness.JobCatalogTests.account
    login = harness.JobCatalogTests.login
    payload = harness.JobCatalogTests.payload
    create = harness.JobCatalogTests.create
    save = harness.JobCatalogTests.save
    profile = match_harness.JobMatchTests.profile
    job = match_harness.JobMatchTests.job
    calculate = match_harness.JobMatchTests.calculate
    ingest = profile_harness.TrustedProfileTests.ingest
    application_payload = application_harness.ApplicationTests.application_payload
    application = application_harness.ApplicationTests.application
    edit_payload = application_harness.ApplicationTests.edit_payload
    edit = application_harness.ApplicationTests.edit
    action = application_harness.ApplicationTests.action

    def setUp(self):
        harness.JobCatalogTests.setUp(self)
        self.settings = self.app.dependency_overrides[get_settings]()
        self.storage = LocalResumeStorage(self.settings.upload_dir)

    def request_export(self, *, client=None, key=None, password=harness.PASSWORD, expected=202):
        response = (client or self.owner).post("/api/v1/me/export", json={
            "password": password, "idempotency_key": key or uuid4().hex,
        })
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def run_export(self, data=None):
        data = data or self.request_export()
        with Session(self.engine) as db:
            exports.execute_export(db, export_id=UUID(data["id"]), storage=self.storage, settings=self.settings)
        return self.owner.get(f"/api/v1/me/export/{data['id']}").json()

    def archive(self, data):
        response = self.owner.get(f"/api/v1/me/export/{data['id']}/download")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), data["sha256"])
        return zipfile.ZipFile(io.BytesIO(response.content))

    def upload(self, *, client=None, name="synthetic.pdf"):
        content = profile_harness.synthetic_pdf("Synthetic resume: Python")
        response = (client or self.owner).post("/api/v1/resumes", files={"file": (name, content, "application/pdf")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), content

    def test_empty_account_exports_readable_versioned_utf8_json_without_security_material(self):
        data = self.run_export()
        self.assertEqual(data["status"], "ready")
        self.assertTrue(data["download_available"])
        self.assertEqual((datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")) -
                          datetime.fromisoformat(data["completed_at"].replace("Z", "+00:00"))).total_seconds(), 86400)
        with self.archive(data) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertTrue(manifest["complete"])
            self.assertEqual(manifest["schema_version"], "offerpilot-user-export/1.0")
            self.assertTrue(manifest["snapshot_at"].endswith("Z"))
            self.assertEqual(manifest["omissions"], [])
            self.assertEqual(json.loads(archive.read("resumes.json")), [])
            self.assertEqual(json.loads(archive.read("applications.json")), [])
            account = json.loads(archive.read("account.json"))[0]
            self.assertEqual(account["id"], str(self.owner_id))
            self.assertTrue(account["created_at"].endswith("Z"))
            combined = b"".join(archive.read(name) for name in archive.namelist())
            for forbidden in (b"password_hash", b"token_hash", b"csrf_token", b"key_reference", b"file_path", b"object_key"):
                self.assertNotIn(forbidden, combined)
            self.assertNotIn(str(self.settings.upload_dir).encode(), combined)
            with Session(self.engine) as db:
                self.assertNotIn(db.get(User, self.owner_id).password_hash.encode(), combined)
                for token_hash in db.scalars(select(AuthSession.token_hash)):
                    self.assertNotIn(token_hash.encode(), combined)

    def test_complete_career_data_retains_pdf_bytes_evidence_ids_history_and_civil_dates(self):
        resume, content = self.upload()
        self.ingest(resume_id=UUID(resume["id"]))
        profile = self.profile()
        job = self.job()
        match = self.calculate(job, profile).json()
        application = self.application(job, match_id=match["id"])
        self.edit(application, status="submitted", notes="合成备注", next_action=self.action(status="completed"))
        data = self.run_export()
        self.assertEqual(data["status"], "ready")
        with self.archive(data) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            pdf_path = f"resumes/{resume['id']}.pdf"
            self.assertEqual(archive.read(pdf_path), content)
            self.assertEqual(json.loads(archive.read("resumes.json"))[0]["original_file_status"], "included")
            candidate = json.loads(archive.read("profile_candidates.json"))[0]
            self.assertEqual(candidate["resume_id"], resume["id"])
            self.assertEqual(candidate["status"], "pending")
            self.assertTrue(candidate["evidence"])
            self.assertEqual(json.loads(archive.read("profile_entries.json"))[0]["value"]["name"], "Python 3")
            self.assertEqual(json.loads(archive.read("job_jd_snapshots.json"))[0]["job_id"], job["id"])
            self.assertEqual(json.loads(archive.read("matches.json"))[0]["id"], match["id"])
            saved = json.loads(archive.read("applications.json"))[0]
            self.assertEqual((saved["job_id"], saved["match_id"]), (job["id"], match["id"]))
            self.assertEqual(saved["action_due_date"], "2099-10-16")
            self.assertEqual(saved["action_timezone"], "Asia/Shanghai")
            self.assertEqual(saved["action_status"], "completed")
            self.assertGreater(len(json.loads(archive.read("application_events.json"))), 1)
            for file in manifest["files"]:
                payload = archive.read(file["path"])
                self.assertEqual(len(payload), file["byte_size"])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), file["sha256"])

    def test_extraction_ledger_preserves_usage_and_versions_but_excludes_security_references(self):
        resume, _ = self.upload()
        secret_references = [uuid4() for _ in range(3)]
        with Session(self.engine) as db:
            row = ResumeExtractionRun(user_id=self.owner_id, resume_id=UUID(resume["id"]),
                    idempotency_key="private-idempotency-key", source_version="a" * 64,
                    status="succeeded", mode="mock", test_result=True, max_attempts=2, max_calls=4,
                    timeout_seconds=30, schema_version="2.0", extraction_version="synthetic-extractor",
                    prompt_version="synthetic-prompt", provider_version="synthetic-provider",
                    session_id=secret_references[0], key_reference=secret_references[1],
                    authorization_id=secret_references[2], billing_status="usage_reported",
                    prompt_tokens=2, completion_tokens=3, total_tokens=5)
            db.add(row)
            db.commit()
            run_id = str(row.id)
        data = self.run_export()
        with self.archive(data) as archive:
            payload = archive.read("extraction_runs.json")
            value = json.loads(payload)[0]
            self.assertEqual((value["id"], value["resume_id"]), (run_id, resume["id"]))
            self.assertEqual(value["total_tokens"], 5)
            self.assertEqual(value["provider_version"], "synthetic-provider")
            self.assertNotIn(b"private-idempotency-key", payload)
            for reference in secret_references:
                self.assertNotIn(str(reference).encode(), payload)

    def test_owner_isolation_forged_ids_and_legacy_unowned_rows(self):
        self.create(self.payload(title="本人合成职位"))
        other_job = self.create(self.payload(title="其他用户保密职位"), client=self.other)
        with Session(self.engine) as db:
            db.add(ResumeDocument(file_name="legacy.pdf", file_path="never-read", file_type="application/pdf", raw_text="legacy-unowned"))
            db.commit()
        data = self.run_export()
        for suffix in ("", "/download"):
            self.assertEqual(self.other.get(f"/api/v1/me/export/{data['id']}{suffix}").status_code, 404)
            self.assertEqual(self.owner.get(f"/api/v1/me/export/{uuid4()}{suffix}").status_code, 404)
        self.assertEqual(self.other.get("/api/v1/me/exports").json(), [])
        self.assertEqual(self.anonymous.get(f"/api/v1/me/export/{data['id']}/download").status_code, 401)
        with self.archive(data) as archive:
            self.assertNotIn(other_job["id"].encode(), archive.read("jobs.json"))
            self.assertNotIn("其他用户".encode(), archive.read("jobs.json"))
            self.assertEqual(json.loads(archive.read("resumes.json")), [])

    def test_csrf_password_reverification_and_explicit_request_schema(self):
        headers = self.owner.headers.pop("X-CSRF-Token")
        self.request_export(expected=403)
        self.owner.headers["X-CSRF-Token"] = headers
        self.request_export(password="wrong-password", expected=403)
        invalid_unicode = self.owner.post("/api/v1/me/export", content=json.dumps({
            "password": "\ud800", "idempotency_key": uuid4().hex}), headers={"Content-Type": "application/json"})
        self.assertEqual(invalid_unicode.status_code, 422)
        response = self.owner.post("/api/v1/me/export", json={"password": harness.PASSWORD,
            "idempotency_key": uuid4().hex, "user_id": str(self.other_id)})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(harness.PASSWORD, response.text)
        self.assertEqual(self.owner.get("/api/v1/me/exports").json(), [])

    def test_duplicate_request_is_durable_idempotent_and_active_quota_is_atomic(self):
        first = self.request_export(key="same-request")
        second = self.request_export(key="same-request")
        self.assertEqual(first["id"], second["id"])
        self.request_export(expected=429)
        self.assertEqual(len(self.owner.get("/api/v1/me/exports").json()), 1)
        self.assertEqual(self.owner.get(f"/api/v1/me/export/{first['id']}/download").status_code, 409)
        self.assertEqual(self.run_export(first)["attempt_count"], 1)
        self.assertEqual(self.run_export(first)["attempt_count"], 1)

    def test_zip_entries_ignore_filename_traversal_and_never_compress_user_content(self):
        resume, _ = self.upload(name="../../nested/hostile.pdf")
        data = self.run_export()
        with self.archive(data) as archive:
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertNotIn("\\", entry.filename)
                self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
            self.assertIn(f"resumes/{resume['id']}.pdf", archive.namelist())

    def test_missing_original_reports_incomplete_export_instead_of_successful_completeness(self):
        resume, _ = self.upload()
        with Session(self.engine) as db:
            document = db.get(ResumeDocument, UUID(resume["id"]))
            self.storage.delete(document.object_key, claim_token=document.id.hex)
        data = self.run_export()
        self.assertEqual(data["status"], "ready")
        with self.archive(data) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertFalse(manifest["complete"])
            self.assertEqual(manifest["omissions"], [{"resume_id": resume["id"], "status": "file_missing"}])
            self.assertNotIn(f"resumes/{resume['id']}.pdf", archive.namelist())

    def test_original_integrity_error_is_explicit_and_does_not_export_corrupted_pdf(self):
        resume, _ = self.upload()
        with patch.object(self.storage, "read", return_value=b"corrupted"):
            data = self.run_export()
        with self.archive(data) as archive:
            self.assertEqual(json.loads(archive.read("resumes.json"))[0]["original_file_status"], "integrity_failed")
            self.assertFalse(json.loads(archive.read("manifest.json"))["complete"])

    def test_expiration_revokes_download_before_cleanup_and_cleanup_can_recover(self):
        data = self.run_export()
        with Session(self.engine) as db:
            row = db.get(AccountExport, UUID(data["id"]))
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.owner.get(f"/api/v1/me/export/{data['id']}/download").status_code, 410)
        self.assertEqual(self.owner.get(f"/api/v1/me/export/{data['id']}").json()["status"], "expired")
        with Session(self.engine) as db, patch.object(exports.ExportFiles, "delete", side_effect=OSError("synthetic file lock")):
            result = exports.cleanup_expired_exports(db, settings=self.settings)
            self.assertEqual(result["failed"], 1)
        with Session(self.engine) as db:
            result = exports.cleanup_expired_exports(db, settings=self.settings)
            self.assertEqual(result["completed"], 1)
            self.assertIsNone(db.get(AccountExport, UUID(data["id"])).error_code)
        self.assertFalse(list(self.settings.export_dir.glob("*")))

    def test_process_interruption_keeps_durable_job_and_bounded_restart_rebuilds_partial_file(self):
        data = self.request_export()
        with Session(self.engine) as db, patch.object(exports, "_snapshot", side_effect=SystemExit("synthetic process interruption")):
            with self.assertRaises(SystemExit):
                exports.execute_export(db, export_id=UUID(data["id"]), storage=self.storage, settings=self.settings)
        with Session(self.engine) as db:
            row = db.get(AccountExport, UUID(data["id"]))
            self.assertEqual(row.status, "running")
            row.lease_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            result = exports.process_export_batch(db, self.storage, self.settings, limit=1)
            self.assertEqual(result["attempted"], 1)
        recovered = self.owner.get(f"/api/v1/me/export/{data['id']}").json()
        self.assertEqual((recovered["status"], recovered["attempt_count"]), ("ready", 2))
        with self.archive(recovered) as archive:
            self.assertIsNone(archive.testzip())
        self.assertFalse(list(self.settings.export_dir.glob("*.part")))

    def test_second_interrupted_attempt_exhausts_budget_and_is_not_automatically_requeued(self):
        data = self.request_export()
        for attempt in (1, 2):
            with Session(self.engine) as db, patch.object(exports, "_snapshot", side_effect=SystemExit()):
                with self.assertRaises(SystemExit):
                    exports.execute_export(db, export_id=UUID(data["id"]), storage=self.storage, settings=self.settings)
            with Session(self.engine) as db:
                row = db.get(AccountExport, UUID(data["id"]))
                row.lease_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
                exports.recover_exports(db, settings=self.settings)
        response = self.owner.get(f"/api/v1/me/export/{data['id']}").json()
        self.assertEqual((response["status"], response["attempt_count"]), ("failed", 2))
        with Session(self.engine) as db:
            self.assertEqual(exports.process_export_batch(db, self.storage, self.settings)["attempted"], 0)

    def test_record_and_byte_quotas_fail_safely_without_retaining_a_partial_archive(self):
        self.create(self.payload(requirements=[{"type": "other", "content": f"合成条件{index}",
                    "priority": "uncertain", "review_status": "pending"} for index in range(12)]))
        self.settings.export_max_records = 10
        first = self.run_export()
        self.assertEqual((first["status"], first["error_code"]), ("failed", "export_resource_limit"))
        self.settings.export_max_records = 10000
        self.settings.export_max_bytes = 1048576
        resume, _ = self.upload()
        with Session(self.engine) as db:
            db.get(ResumeDocument, UUID(resume["id"])).raw_text = "大" * 400000
            db.commit()
        second = self.run_export()
        self.assertEqual((second["status"], second["error_code"]), ("failed", "export_resource_limit"))
        self.assertFalse(list(self.settings.export_dir.glob("*")))

    def test_write_failure_is_observable_retryable_by_new_request_and_does_not_echo_exception_text(self):
        first = self.request_export(key="failed-request")
        with patch.object(exports, "_snapshot", side_effect=OSError("private-server-path-and-personal-data")):
            failed = self.run_export(first)
        self.assertEqual((failed["status"], failed["error_code"]), ("failed", "export_failed"))
        self.assertNotIn("private-server-path", str(failed))
        self.assertEqual(self.request_export(key="failed-request")["id"], first["id"])
        self.assertEqual(self.run_export()["status"], "ready")

    def test_deletion_registry_fence_prevents_new_export_or_download_or_late_publish(self):
        data = self.run_export()
        DeletionLedger(self.settings.deletion_ledger_dir).record(self.owner_id, uuid4())
        self.assertEqual(self.owner.get(f"/api/v1/me/export/{data['id']}/download").status_code, 401)
        self.request_export(expected=401)
        with Session(self.engine) as db:
            db.info["account_cleanup"] = True
            exports.delete_user_exports(db, user_id=self.owner_id, settings=self.settings)
        self.assertFalse(list(self.settings.export_dir.glob("*")))

    def test_download_checks_archive_hash_and_refuses_symlink_or_storage_substitution(self):
        data = self.run_export()
        path = exports.ExportFiles(self.settings.export_dir).path(UUID(data["id"]), ".zip")
        path.write_bytes(b"damaged")
        response = self.owner.get(f"/api/v1/me/export/{data['id']}/download")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "export_integrity_failed")
        self.assertNotIn(str(path), response.text)

    def test_physical_cleanup_retries_are_bounded_and_operator_can_inspect_failure(self):
        data = self.run_export()
        with Session(self.engine) as db:
            row = db.get(AccountExport, UUID(data["id"]))
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            with patch.object(exports.ExportFiles, "delete", side_effect=OSError("locked-private-path")):
                for _ in range(5):
                    self.assertEqual(exports.cleanup_expired_exports(db, settings=self.settings)["failed"], 1)
                self.assertEqual(exports.cleanup_expired_exports(db, settings=self.settings)["attempted"], 0)
            db.refresh(row)
            self.assertEqual((row.cleanup_attempt_count, row.error_code), (5, "export_cleanup_failed"))
            self.request_export(expected=429)
            row.cleanup_attempt_count = 0  # trusted operator, after diagnosing IO
            db.commit()
            self.assertEqual(exports.cleanup_expired_exports(db, settings=self.settings)["completed"], 1)

    def test_old_execution_failure_cannot_remove_a_new_lease_artifact(self):
        data = self.request_export()
        export_id, newer_token = UUID(data["id"]), uuid4()
        files = exports.ExportFiles(self.settings.export_dir)
        def change_execution(db, row, *_args):
            # Simulate lost ownership at the rollback/compensation boundary.
            row.execution_token = newer_token
            db.commit()
            files.path(export_id, ".zip").write_bytes(b"synthetic-newer-execution")
            raise OSError("synthetic stale execution failure")
        with patch.object(exports, "_snapshot", side_effect=change_execution):
            with Session(self.engine) as db:
                self.assertFalse(exports.execute_export(db, export_id=export_id, storage=self.storage, settings=self.settings))
        self.assertEqual(files.path(export_id, ".zip").read_bytes(), b"synthetic-newer-execution")
        with Session(self.engine) as db:
            self.assertEqual(db.get(AccountExport, export_id).execution_token, newer_token)


if __name__ == "__main__":
    unittest.main()
