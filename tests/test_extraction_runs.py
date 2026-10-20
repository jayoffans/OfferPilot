"""Synthetic PDFs, real sessions, durable SQLite races and interruption fences."""

import threading
import unittest
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pymupdf
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from offerpilot_api.agent_runtime.extraction_runs import (
    ExtractionRunError, _now, claim_extraction_run, create_extraction_run,
    execute_extraction_run, finish_extraction_run, get_extraction_run,
    process_extraction_batch, recover_expired_runs, source_version,
)
from offerpilot_api.agent_runtime.extraction_schemas import ExtractionRunCreateRequest
from offerpilot_api.agent_runtime.providers.mock import MockResumeProvider
from offerpilot_api.db.models import CareerProfile, CareerProfileEntry, ProfileCandidate, ResumeDocument, ResumeExtractionRun
from offerpilot_api.resume_ingestion.extractor_v2 import extract_student_profile_v2
from offerpilot_api.resume_ingestion.synthetic_fixtures import SYNTHETIC_RESUME_TEXT
import test_trusted_profile as profile_harness


def synthetic_chinese_pdf(text=SYNTHETIC_RESUME_TEXT, *, split_pages=False):
    with pymupdf.open() as pdf:
        lines = text.splitlines()
        groups = [lines[:4], lines[4:]] if split_pages else [lines]
        for group in groups:
            page = pdf.new_page()
            page.insert_text((36, 36), "\n".join(group), fontname="china-s", fontsize=6)
        return pdf.tobytes()


class InvalidProvider:
    def generate_json(self, **kwargs):
        return "{invalid JSON contains synthetic private content"


class ExtractionRunTests(unittest.TestCase):
    # Reuse authenticated test harness, without inheriting/re-running its tests.
    setUp = profile_harness.TrustedProfileTests.setUp
    tearDown = profile_harness.TrustedProfileTests.tearDown
    client = profile_harness.TrustedProfileTests.client
    login = profile_harness.TrustedProfileTests.login
    registered = profile_harness.TrustedProfileTests.registered
    profile = profile_harness.TrustedProfileTests.profile

    def enabled(self):
        self.settings.mock_extraction_enabled = True
        return self.settings

    def factory(self):
        return Session(self.engine, autoflush=False)

    def upload(self, client=None, text=SYNTHETIC_RESUME_TEXT, *, split_pages=False):
        response = (client or self.owner).post("/api/v1/resumes", files={
            "file": ("合成抽取测试.pdf", synthetic_chinese_pdf(text, split_pages=split_pages), "application/pdf"),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return UUID(response.json()["id"])

    def create(self, resume_id=None, client=None, key=None, *, expected=202):
        self.enabled()
        client = client or self.owner
        resume_id = resume_id or self.upload(client)
        capability = client.get(f"/api/v1/resumes/{resume_id}/extraction")
        self.assertEqual(capability.status_code, 200, capability.text)
        payload = {"idempotency_key": key or uuid4().hex, "source_version": capability.json()["source_version"], "mode": "mock"}
        response = client.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json=payload)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def run_status(self, run_id):
        response = self.owner.get(f"/api/v1/extraction-runs/{run_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def count(self, model):
        with self.factory() as db:
            return db.scalar(select(func.count()).select_from(model))

    def test_synthetic_pdf_full_flow_never_auto_confirms_and_records_parser_locations(self):
        resume_id = self.upload(split_pages=True)
        created = self.create(resume_id)
        self.assertEqual(created["status"], "queued")
        self.assertEqual(self.profile()["education"], [])
        provider = MockResumeProvider()
        self.assertTrue(execute_extraction_run(self.factory, run_id=UUID(created["id"]), provider_factory=lambda: provider))
        finished = self.run_status(created["id"])
        self.assertEqual((finished["status"], finished["call_count"], finished["attempt_count"]), ("succeeded", 1, 1))
        self.assertTrue(finished["test_result"])
        profile = self.profile()
        for group in ("education", "skills", "projects", "experiences"):
            self.assertEqual(profile[group], [])
        self.assertEqual(len(profile["candidates"]), finished["candidate_count"])
        self.assertTrue(all(item["status"] == "pending" for item in profile["candidates"]))
        for candidate in profile["candidates"]:
            self.assertEqual(candidate["extraction_run_id"], created["id"])
            self.assertTrue(candidate["extraction_metadata"]["test_result"])
            for field in candidate["extraction_metadata"]["fields"]:
                for location in field["locations"]:
                    self.assertIn(location["page"], (1, 2))
                    self.assertIsNotNone(location["paragraph"])
        positive = next(item for item in profile["candidates"] if item["category"] == "skill" and item["value"]["name"] == "Python")
        response = self.owner.post(f"/api/v1/profile/candidates/{positive['id']}/review", json={"revision": profile["revision"], "action": "confirm"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["skills"][0]["name"], "Python")
        self.assertTrue(response.json()["skills"][0]["source"]["test_result"])
        negative = next(item for item in response.json()["candidates"] if item["category"] == "skill" and item["value"]["name"] == "Java")
        rejected_confirm = self.owner.post(f"/api/v1/profile/candidates/{negative['id']}/review", json={"revision": response.json()["revision"], "action": "confirm"})
        self.assertEqual(rejected_confirm.status_code, 422)
        self.assertEqual(self.count(CareerProfileEntry), 1)

    def test_default_disabled_and_non_development_override_cannot_call_provider(self):
        resume_id = self.upload()
        capability = self.owner.get(f"/api/v1/resumes/{resume_id}/extraction").json()
        self.assertEqual((capability["enabled"], capability["mode"]), (False, "disabled"))
        payload = {"idempotency_key": "disabled", "source_version": capability["source_version"], "mode": "mock"}
        self.assertEqual(self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json=payload).status_code, 503)
        self.enabled().environment = "production"
        self.assertEqual(self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json=payload).status_code, 503)
        self.assertEqual(self.count(ResumeExtractionRun), 0)
        self.assertNotIn("raw_text", capability)
        self.assertNotIn(SYNTHETIC_RESUME_TEXT, str(capability))

    def test_identity_csrf_ownership_strict_schema_and_source_version(self):
        resume_id = self.upload()
        run = self.create(resume_id)
        paths = (f"/api/v1/resumes/{resume_id}/extraction", f"/api/v1/extraction-runs/{run['id']}", f"/api/v1/extraction-runs/{run['id']}/candidates")
        for path in paths:
            self.assertEqual(self.anonymous.get(path).status_code, 401)
            self.assertEqual(self.other.get(path).status_code, 404)
        self.assertEqual(self.other.get(f"/api/v1/extraction-runs/{uuid4()}").status_code, 404)
        payload = {"idempotency_key": "new-request", "source_version": run["source_version"], "mode": "mock"}
        self.owner.headers.pop("X-CSRF-Token")
        self.assertEqual(self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json=payload).status_code, 403)
        self.login(self.owner, "profile-owner@example.com")
        response = self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json={**payload, "user_id": "private-input-not-echoed"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-input-not-echoed", response.text)
        self.assertEqual(self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json={**payload, "source_version": "a" * 64}).status_code, 409)
        for operation in ("cancel", "retry"):
            self.assertEqual(self.other.post(f"/api/v1/extraction-runs/{run['id']}/{operation}", json={"revision": run["revision"]}).status_code, 404)

    def test_request_idempotence_content_uniqueness_and_repeat_execution(self):
        resume_id = self.upload()
        created = self.create(resume_id, key="stable-request")
        again = self.create(resume_id, key="stable-request")
        self.assertEqual(again["id"], created["id"])
        self.create(resume_id, key="different-request", expected=409)
        self.assertTrue(execute_extraction_run(self.factory, run_id=UUID(created["id"])))
        candidate_count = self.count(ProfileCandidate)
        revision = self.profile()["revision"]
        self.assertFalse(execute_extraction_run(self.factory, run_id=UUID(created["id"])))
        self.assertEqual(self.count(ProfileCandidate), candidate_count)
        self.assertEqual(self.profile()["revision"], revision)
        self.assertEqual(self.create(resume_id, key="stable-request")["status"], "succeeded")
        self.create(self.upload(), key="stable-request", expected=409)

    def test_two_concurrent_creates_with_new_keys_only_one_source_run(self):
        resume_id = self.upload()
        self.enabled()
        with self.factory() as db:
            version = source_version(db.get(ResumeDocument, resume_id))
        barrier = threading.Barrier(2)
        def create_one(key):
            barrier.wait(timeout=5)
            try:
                with self.factory() as db:
                    return create_extraction_run(db, user_id=self.owner_id, resume_id=resume_id, settings=self.settings,
                        payload=ExtractionRunCreateRequest(idempotency_key=key, source_version=version, mode="mock")).status
            except ExtractionRunError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(create_one, ("concurrent-a", "concurrent-b")))
        self.assertCountEqual(outcomes, ["queued", "extraction_already_exists"])
        self.assertEqual(self.count(ResumeExtractionRun), 1)

    def test_concurrent_same_run_only_one_provider_and_candidate_batch(self):
        created = self.create()
        barrier = threading.Barrier(2)
        provider = MockResumeProvider()
        def execute_one(_):
            barrier.wait(timeout=5)
            return execute_extraction_run(self.factory, run_id=UUID(created["id"]), provider_factory=lambda: provider)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(execute_one, (1, 2)))
        self.assertCountEqual(outcomes, [True, False])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self.count(ProfileCandidate), self.run_status(created["id"])["candidate_count"])

    def test_concurrent_two_users_are_isolated(self):
        a, b = self.create(), self.create(client=self.other)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda item: execute_extraction_run(self.factory, run_id=UUID(item["id"])), (a, b)))
        self.assertEqual(outcomes, [True, True])
        owned, other = self.profile(), self.profile(self.other)
        self.assertTrue(all(item["resume_id"] == a["resume_id"] for item in owned["candidates"]))
        self.assertTrue(all(item["resume_id"] == b["resume_id"] for item in other["candidates"]))
        self.assertEqual(self.other.get(f"/api/v1/extraction-runs/{a['id']}/candidates").status_code, 404)

    def test_queued_cancel_fences_execution_and_checks_revision(self):
        created = self.create()
        path = f"/api/v1/extraction-runs/{created['id']}/cancel"
        self.assertEqual(self.owner.post(path, json={"revision": 999}).status_code, 409)
        cancelled = self.owner.post(path, json={"revision": created["revision"]})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        provider = MockResumeProvider()
        self.assertFalse(execute_extraction_run(self.factory, run_id=UUID(created["id"]), provider_factory=lambda: provider))
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.run_status(created["id"])["status"], "cancelled")
        self.assertEqual(self.owner.post(f"/api/v1/extraction-runs/{created['id']}/retry", json={"revision": cancelled.json()["revision"]}).status_code, 409)

    def blocked_execution(self, created, operation):
        entered, release = threading.Event(), threading.Event()
        delegate = MockResumeProvider()
        class Blocked:
            def generate_json(self, **kwargs):
                entered.set()
                if not release.wait(timeout=10):
                    raise RuntimeError("synthetic gate timed out")
                return delegate.generate_json(**kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(execute_extraction_run, self.factory, run_id=UUID(created["id"]), provider_factory=Blocked)
            self.assertTrue(entered.wait(timeout=5))
            try:
                operation()
            finally:
                release.set()
            self.assertFalse(running.result(timeout=10))
        self.assertEqual(self.count(ProfileCandidate), 0)
        self.assertEqual(self.count(CareerProfileEntry), 0)

    def test_running_cancel_discards_late_result(self):
        created = self.create()
        def cancel():
            running = self.run_status(created["id"])
            self.assertEqual(running["status"], "running")
            response = self.owner.post(f"/api/v1/extraction-runs/{created['id']}/cancel", json={"revision": running["revision"]})
            self.assertEqual(response.status_code, 200, response.text)
        self.blocked_execution(created, cancel)
        self.assertEqual(self.run_status(created["id"])["status"], "cancelled")

    def test_running_delete_clears_pdf_text_locations_and_discards_late_result(self):
        created = self.create()
        def remove():
            response = self.owner.delete(f"/api/v1/resumes/{created['resume_id']}")
            self.assertEqual(response.status_code, 204, response.text)
        self.blocked_execution(created, remove)
        run = self.run_status(created["id"])
        self.assertEqual((run["status"], run["error_code"], run["candidate_count"]), ("cancelled", "resume_deleted", 0))
        self.assertEqual(self.owner.get(f"/api/v1/extraction-runs/{created['id']}/candidates").json()["candidates"], [])
        with self.factory() as db:
            self.assertIsNone(db.get(ResumeDocument, UUID(created["resume_id"])))
        self.assertFalse(any(self.settings.upload_dir.glob("*.pdf")))

    def test_text_version_change_while_running_cannot_write_old_result(self):
        created = self.create()
        def change():
            with self.factory() as db:
                db.execute(update(ResumeDocument).where(ResumeDocument.id == UUID(created["resume_id"])).values(raw_text="新的合成简历正文"))
                db.commit()
        self.blocked_execution(created, change)
        self.assertEqual(self.run_status(created["id"])["error_code"], "source_version_changed")

    def test_expired_persisted_running_is_queryable_retryable_and_old_token_rejected(self):
        created = self.create()
        with self.factory() as db:
            claim = claim_extraction_run(db, run_id=UUID(created["id"]), now=_now() - timedelta(seconds=100))
        recovered = self.run_status(created["id"])
        self.assertEqual((recovered["status"], recovered["error_code"]), ("failed", "lease_expired"))
        # A fresh Session sees recovery after the original processor disappeared.
        with self.factory() as db:
            self.assertIsNone(db.get(ResumeExtractionRun, UUID(created["id"])).execution_token)
        result = extract_student_profile_v2(claim.raw_text, MockResumeProvider(), text_layout=claim.text_layout)
        with self.factory() as db:
            self.assertFalse(finish_extraction_run(db, claim=claim, result=result))
        retry = self.owner.post(f"/api/v1/extraction-runs/{created['id']}/retry", json={"revision": recovered["revision"]})
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(execute_extraction_run(self.factory, run_id=UUID(created["id"])))
        self.assertEqual(self.run_status(created["id"])["attempt_count"], 2)

    def test_real_process_interruption_then_finite_cli_recovery(self):
        self.enabled().extraction_timeout_seconds = 1
        created = self.create()
        environment = {**os.environ, "OFFERPILOT_DATABASE_URL": self.settings.database_url,
                       "OFFERPILOT_ENVIRONMENT": "development", "OFFERPILOT_MOCK_EXTRACTION_ENABLED": "true"}
        code = (
            "import os,sys; from uuid import UUID; "
            "from offerpilot_api.db.session import SessionLocal; "
            "from offerpilot_api.agent_runtime.extraction_runs import claim_extraction_run; "
            "db=SessionLocal(); claim=claim_extraction_run(db,run_id=UUID(sys.argv[1])); "
            "os._exit(0 if claim else 2)"
        )
        crashed = subprocess.run([sys.executable, "-c", code, created["id"]], env=environment, capture_output=True, timeout=10)
        self.assertEqual(crashed.returncode, 0, crashed.stderr.decode(errors="replace"))
        with self.factory() as db:
            self.assertEqual(db.get(ResumeExtractionRun, UUID(created["id"])).status, "running")
        time.sleep(1.1)
        recovered = subprocess.run([sys.executable, "-m", "offerpilot_api.agent_runtime.process_extractions", "--recover-only", "--limit", "1"],
                                   env=environment, capture_output=True, timeout=10)
        self.assertEqual(recovered.returncode, 0, recovered.stderr.decode(errors="replace"))
        self.assertIn('"recovered": 1', recovered.stdout.decode())
        self.assertEqual(self.run_status(created["id"])["error_code"], "lease_expired")
        self.assertEqual(self.count(ProfileCandidate), 0)

    def test_invalid_json_has_finite_calls_retries_and_redacted_failure(self):
        created = self.create()
        for attempt in (1, 2):
            self.assertFalse(execute_extraction_run(self.factory, run_id=UUID(created["id"]), provider_factory=InvalidProvider))
            failed = self.run_status(created["id"])
            self.assertEqual((failed["status"], failed["attempt_count"], failed["call_count"]), ("failed", attempt, attempt * 2))
            self.assertNotIn("synthetic private content", str(failed))
            response = self.owner.post(f"/api/v1/extraction-runs/{created['id']}/retry", json={"revision": failed["revision"]})
            self.assertEqual(response.status_code, 200 if attempt == 1 else 409, response.text)
        self.assertEqual(self.count(ProfileCandidate), 0)
        self.assertEqual(self.count(CareerProfile), 0)

    def test_unapproved_real_text_mock_safely_fails(self):
        created = self.create(self.upload(text="未经批准的正文不应该被Mock伪装提取"))
        self.assertFalse(execute_extraction_run(self.factory, run_id=UUID(created["id"])))
        self.assertEqual(self.run_status(created["id"])["error_code"], "mock_fixture_not_found")
        self.assertEqual(self.count(ProfileCandidate), 0)

    def test_call_budget_prevents_extra_provider_invocation(self):
        self.enabled().extraction_max_calls = 1
        created = self.create()
        provider = InvalidProvider()
        self.assertFalse(execute_extraction_run(self.factory, run_id=UUID(created["id"]), provider_factory=lambda: provider))
        self.assertEqual(self.run_status(created["id"])["call_count"], 1)
        self.assertEqual(self.count(ProfileCandidate), 0)

    def test_candidate_flush_failure_rolls_back_candidates_and_success_together(self):
        from offerpilot_api.agent_runtime import extraction_runs
        created = self.create()
        real_ingest = extraction_runs.ingest_profile_candidates
        def flushed_then_failed(*args, **kwargs):
            real_ingest(*args, **kwargs)
            raise RuntimeError("synthetic failure after candidate flush")
        with patch.object(extraction_runs, "ingest_profile_candidates", side_effect=flushed_then_failed):
            self.assertFalse(execute_extraction_run(self.factory, run_id=UUID(created["id"])))
        self.assertEqual(self.run_status(created["id"])["status"], "failed")
        self.assertEqual(self.count(ProfileCandidate), 0)
        self.assertEqual(self.count(CareerProfile), 0)

    def test_success_commit_acknowledgement_loss_never_marks_success_failed(self):
        created = self.create()
        original_commit = Session.commit
        lost = False
        def acknowledgement_lost(db):
            nonlocal lost
            succeeded = any(isinstance(item, ResumeExtractionRun) and item.status == "succeeded" for item in db.identity_map.values())
            # SQL UPDATE uses synchronize_session=False, so inspect the transaction.
            committed_status = db.scalar(select(ResumeExtractionRun.status).where(ResumeExtractionRun.id == UUID(created["id"])))
            original_commit(db)
            if not lost and (succeeded or committed_status == "succeeded"):
                lost = True
                raise OperationalError("synthetic commit acknowledgement lost", {}, Exception("synthetic"))
        with patch.object(Session, "commit", acknowledgement_lost):
            self.assertTrue(execute_extraction_run(self.factory, run_id=UUID(created["id"])))
        finished = self.run_status(created["id"])
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(self.count(ProfileCandidate), finished["candidate_count"])
        self.assertFalse(execute_extraction_run(self.factory, run_id=UUID(created["id"])))

    def test_deadline_during_candidate_flush_rolls_back_entire_success(self):
        from offerpilot_api.agent_runtime import extraction_runs
        created = self.create()
        with self.factory() as db:
            claim = claim_extraction_run(db, run_id=UUID(created["id"]))
        result = extract_student_profile_v2(claim.raw_text, MockResumeProvider(), text_layout=claim.text_layout)
        real_ingest = extraction_runs.ingest_profile_candidates
        def advance_clock(*args, **kwargs):
            response = real_ingest(*args, **kwargs)
            clock_mock.return_value = claim.deadline + timedelta(seconds=1)
            return response
        with patch.object(extraction_runs, "_now", return_value=claim.deadline - timedelta(seconds=1)) as clock_mock:
            with patch.object(extraction_runs, "ingest_profile_candidates", side_effect=advance_clock):
                with self.factory() as db:
                    self.assertFalse(finish_extraction_run(db, claim=claim, result=result))
        self.assertEqual(self.count(ProfileCandidate), 0)
        self.assertEqual(self.count(CareerProfile), 0)
        with self.factory() as db:
            self.assertEqual(recover_expired_runs(db, now=claim.deadline + timedelta(seconds=1)), 1)

    def test_active_backlog_limits_and_bounded_local_processing(self):
        self.enabled().extraction_max_active_per_user = 1
        first = self.create()
        self.create(expected=429)
        processed = process_extraction_batch(self.factory, limit=1)
        self.assertEqual(processed["selected"], 1)
        self.assertEqual(processed["succeeded"], 1)
        self.assertEqual(self.run_status(first["id"])["status"], "succeeded")
        with self.assertRaises(ValueError):
            process_extraction_batch(self.factory, limit=101)


if __name__ == "__main__":
    unittest.main()

