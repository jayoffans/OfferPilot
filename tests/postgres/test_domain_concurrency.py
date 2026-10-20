"""Real PostgreSQL transactions for existing profile/job/application/file flows.

Only synthetic users, a generated PDF and isolated fixture databases are used.
Each concurrent call owns an independent SQLAlchemy Session/connection.
"""

import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pymupdf
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from offerpilot_api.application_tracker import services as applications
from offerpilot_api.application_tracker.dashboard import get_dashboard
from offerpilot_api.application_tracker.schemas import ApplicationCreateRequest, ApplicationDeleteRequest, ApplicationUpdateRequest
from offerpilot_api.db.models import (
    Application, ApplicationEvent, ApplicationReceipt, AuthSession, AuthThrottle, CareerProfile,
    CareerProfileEntry, JobMatch, JobMatchReceipt, ProfileCandidate, ResumeDocument,
    ResumeFileOperation, SavedJob, User,
)
from offerpilot_api.job_catalog import services as jobs
from offerpilot_api.core.config import get_settings
from offerpilot_api.db.session import build_engine, get_db
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, new_token, token_hash
from offerpilot_api.main import create_app
from offerpilot_api.job_catalog.schemas import JobCreateRequest, JobRequirementsSaveRequest
from offerpilot_api.job_intelligence import match_services, matching
from offerpilot_api.job_intelligence.match_api_schemas import JobMatchCreateRequest
from offerpilot_api.profile import services as profiles
from offerpilot_api.profile.schemas import ProfileCandidateReviewRequest, ProfileSaveRequest
from offerpilot_api.resume_ingestion.lifecycle import (
    ResumeLifecycleError, complete_resume_upload, delete_owned_resume, get_owned_lifecycle,
    recover_pending_files,
)
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, StorageError
from offerpilot_api.services.resume import ResumeUploadError, save_resume_document

from tests.postgres.support import PostgresTestCase


class FaultSession(Session):
    """Fail a real commit at the database constraint boundary, after file I/O."""

    def __init__(self, *args, fail_at=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_at, self.commits = fail_at, 0

    def commit(self):
        self.commits += 1
        if self.commits == self.fail_at:
            self.add(AuthThrottle(key_hash=uuid4().hex * 2,
                window_started_at=datetime.now(timezone.utc), attempt_count=-1))
        return super().commit()


class ControlledStorage:
    def __init__(self, root):
        self.delegate = LocalResumeStorage(root)
        self.fail_delete = False
        self.block_save = False
        self.entered, self.release = threading.Event(), threading.Event()

    def save(self, *args, **kwargs):
        if self.block_save:
            self.entered.set()
            if not self.release.wait(10):
                raise StorageError("synthetic_timeout", "Synthetic storage barrier timed out")
        return self.delegate.save(*args, **kwargs)

    def read(self, *args, **kwargs):
        return self.delegate.read(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.fail_delete:
            raise StorageError("synthetic_failure", "Synthetic deletion failure")
        return self.delegate.delete(*args, **kwargs)


class DomainConcurrencyTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.owner_id, self.other_id = uuid4(), uuid4()
        with Session(self.engine) as db:
            db.add_all([User(id=owner, email_normalized=f"{owner.hex}@synthetic.example",
                password_hash="synthetic-unused-hash", is_active=True)
                for owner in (self.owner_id, self.other_id)])
            db.commit()
        self.storage = ControlledStorage(self.root / "private-objects")
        self.addCleanup(self.storage.release.set)

    def call(self, fn, *, session_class=Session, **kwargs):
        with session_class(self.engine) as db:
            return fn(db, **kwargs)

    def count(self, model):
        with Session(self.engine) as db:
            return db.scalar(select(func.count()).select_from(model))

    def race(self, fn, count=2):
        barrier = threading.Barrier(count)
        def work(index):
            barrier.wait(10)
            try:
                return fn(index)
            except (profiles.ProfileError, jobs.JobError, match_services.MatchError,
                    applications.ApplicationError, ResumeLifecycleError) as exc:
                return exc
        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = [pool.submit(work, index) for index in range(count)]
            return [future.result(20) for future in futures]

    def profile(self, revision=0, name="Python"):
        return self.call(profiles.save_profile, user_id=self.owner_id,
            payload=ProfileSaveRequest(revision=revision, education=[], skills=[{"name": name}], projects=[], experiences=[]))

    def job(self, **changes):
        values = {"idempotency_key": uuid4().hex, "title": "合成后端实习岗位",
            "jd_text": "必须掌握 Python", "requirements": [{"type": "technical_skill",
                "content": "必须掌握 Python", "priority": "must", "review_status": "confirmed",
                "evidence_text": "必须掌握 Python"}], **changes}
        return self.call(jobs.create_job, user_id=self.owner_id, payload=JobCreateRequest(**values))

    def application(self, job=None):
        job = job or self.job()
        return self.call(applications.create_application, user_id=self.owner_id,
            payload=ApplicationCreateRequest(job_id=job.id, job_revision=job.revision, idempotency_key=uuid4().hex))

    def edit_payload(self, row, **changes):
        return ApplicationUpdateRequest(**{"revision": row.revision, "idempotency_key": uuid4().hex,
            "status": row.status, "submitted_at": row.submitted_at, "notes": row.notes,
            "next_action": row.next_action, **changes})

    def upload(self, *, session_class=Session):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "Synthetic PostgreSQL resume: Python")
            content = pdf.tobytes()
        file = UploadFile(file=io.BytesIO(content), filename="synthetic-postgres.pdf",
            headers=Headers({"content-type": "application/pdf"}))
        try:
            return self.call(lambda db: save_resume_document(file, db, self.root / "private-objects",
                user_id=self.owner_id, storage=self.storage), session_class=session_class)
        finally:
            file.file.close()

    def ingest(self, resume_id, *, session_class=Session):
        return self.call(profiles.ingest_profile_candidates, user_id=self.owner_id, resume_id=resume_id,
            session_class=session_class, batch_key=uuid4().hex,
            payload={"schema_version": "1.0", "extraction_version": "synthetic-pg-1",
                "candidates": [{"category": "skill", "value": {"name": "Python"},
                    "evidence": [{"field": "name", "source_text": "Python"}]}]})

    def test_concurrent_first_profile_is_unique_and_one_writer_conflicts(self):
        results = self.race(lambda index: self.profile(name=f"Synthetic-{index}"))
        self.assertEqual(sum(isinstance(item, profiles.ProfileError) for item in results), 1)
        self.assertEqual([item.status_code for item in results if isinstance(item, profiles.ProfileError)], [409])
        self.assertEqual((self.count(CareerProfile), self.count(CareerProfileEntry)), (1, 1))
        current = self.call(profiles.get_profile, user_id=self.owner_id)
        self.assertEqual(current.revision, 1)
        self.assertEqual(len(current.skills), 1)

    def test_profile_revision_cas_never_silently_overwrites(self):
        profile = self.profile()
        results = self.race(lambda index: self.profile(revision=profile.revision, name=f"Synthetic-{index}"))
        self.assertEqual(sum(isinstance(item, profiles.ProfileError) for item in results), 1)
        current = self.call(profiles.get_profile, user_id=self.owner_id)
        winner = next(item for item in results if not isinstance(item, Exception))
        self.assertEqual((current.revision, current.skills[0].name), (2, winner.skills[0].name))
        self.assertEqual(self.count(CareerProfileEntry), 1)

    def test_internal_initialization_recovers_when_manual_profile_commits_after_update_miss(self):
        resume = self.upload()
        missed, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class PausingProfileSession(Session):
            def execute(session, statement, *args, **kwargs):
                result = super().execute(statement, *args, **kwargs)
                if (getattr(statement, "is_update", False)
                    and statement.table.name == "career_profiles" and result.rowcount == 0):
                    missed.set()
                    if not release.wait(10):
                        raise RuntimeError("Synthetic profile initialization barrier timed out")
                return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            ingesting = pool.submit(self.ingest, resume.id, session_class=PausingProfileSession)
            self.assertTrue(missed.wait(10))
            try:
                initial = self.profile(name="User-provided synthetic skill")
            finally:
                release.set()
            completed = ingesting.result(10)
        self.assertEqual((initial.revision, completed.revision), (1, 2))
        self.assertEqual((len(completed.skills), len(completed.candidates)), (1, 1))
        self.assertEqual(completed.candidates[0].status, "pending")
        self.assertEqual(self.count(CareerProfile), 1)

    def test_concurrent_candidate_confirmation_creates_one_formal_entry(self):
        resume = self.upload()
        profile = self.ingest(resume.id)
        candidate = profile.candidates[0]
        results = self.race(lambda _: self.call(profiles.review_profile_candidate, user_id=self.owner_id,
            candidate_id=candidate.id, payload=ProfileCandidateReviewRequest(revision=profile.revision, action="confirm")))
        self.assertEqual(sum(isinstance(item, profiles.ProfileError) for item in results), 1)
        current = self.call(profiles.get_profile, user_id=self.owner_id)
        self.assertEqual((len(current.skills), current.candidates[0].status, current.revision), (1, "confirmed", 2))

    def test_concurrent_jd_review_uses_revision_fence(self):
        job = self.job()
        requirements = [{key: value for key, value in row.model_dump().items()
            if key in {"id", "type", "content", "priority", "review_status", "evidence_text", "source_start", "source_jd_version"}}
            for row in job.requirements]
        results = self.race(lambda _: self.call(jobs.save_requirements, user_id=self.owner_id, job_id=job.id,
            payload=JobRequirementsSaveRequest(revision=job.revision, requirements=requirements)))
        self.assertEqual(sum(isinstance(item, jobs.JobError) for item in results), 1)
        current = self.call(jobs.get_job, user_id=self.owner_id, job_id=job.id)
        self.assertEqual((current.revision, current.review_summary.confirmed), (2, 1))

    def test_concurrent_matching_deduplicates_same_basis_and_preserves_receipts(self):
        profile, job = self.profile(), self.job()
        results = self.race(lambda _: self.call(match_services.create_match, user_id=self.owner_id, job_id=job.id,
            payload=JobMatchCreateRequest(profile_revision=profile.revision, job_revision=job.revision,
                idempotency_key=uuid4().hex)))
        self.assertTrue(all(not isinstance(item, Exception) for item in results), results)
        self.assertEqual(results[0].id, results[1].id)
        self.assertEqual((self.count(JobMatch), self.count(JobMatchReceipt)), (1, 2))
        self.profile(revision=profile.revision, name="Java")
        stale = self.call(match_services.get_match, user_id=self.owner_id, job_id=job.id, match_id=results[0].id)
        self.assertEqual(stale.stale_reasons, ["profile_changed"])
        with patch.object(matching, "RULE_VERSION", "synthetic-new-rule"):
            stale = self.call(match_services.get_match, user_id=self.owner_id, job_id=job.id, match_id=results[0].id)
            self.assertIn("rule_changed", stale.stale_reasons)

    def test_job_quota_check_and_insert_share_owner_lock(self):
        entered, release, second_count = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class PausingCountSession(Session):
            def scalar(session, statement, *args, **kwargs):
                value = super().scalar(statement, *args, **kwargs)
                if "count(" in str(statement) and "saved_jobs" in str(statement):
                    entered.set()
                    if not release.wait(10):
                        raise RuntimeError("Synthetic quota barrier timed out")
                return value
        class ObservingCountSession(Session):
            def scalar(session, statement, *args, **kwargs):
                value = super().scalar(statement, *args, **kwargs)
                if "count(" in str(statement) and "saved_jobs" in str(statement):
                    second_count.set()
                return value
        def create(title, session_class):
            try:
                return self.call(jobs.create_job, user_id=self.owner_id, session_class=session_class,
                    payload=JobCreateRequest(idempotency_key=uuid4().hex, title=title))
            except jobs.JobError as exc:
                return exc
        with patch.object(jobs, "MAX_JOBS", 1), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create, "合成 quota A", PausingCountSession)
            self.assertTrue(entered.wait(10))
            second = pool.submit(create, "合成 quota B", ObservingCountSession)
            try:
                self.assertFalse(second_count.wait(0.3), "Another PostgreSQL connection read the quota before the first insert committed")
            finally:
                release.set()
            results = [first.result(10), second.result(10)]
        self.assertEqual(sum(isinstance(item, jobs.JobError) for item in results), 1)
        self.assertEqual(self.count(SavedJob), 1)

    def test_concurrent_distinct_application_requests_create_one_attempt(self):
        job = self.job()
        results = self.race(lambda _: self.call(applications.create_application, user_id=self.owner_id,
            payload=ApplicationCreateRequest(job_id=job.id, job_revision=job.revision, idempotency_key=uuid4().hex)))
        self.assertEqual(sum(isinstance(item, applications.ApplicationError) for item in results), 1)
        self.assertEqual((self.count(Application), self.count(ApplicationEvent), self.count(ApplicationReceipt)), (1, 1, 1))

    def test_concurrent_identical_application_request_replays_once(self):
        job = self.job()
        payload = ApplicationCreateRequest(job_id=job.id, job_revision=job.revision, idempotency_key=uuid4().hex)
        results = self.race(lambda _: self.call(applications.create_application, user_id=self.owner_id, payload=payload))
        self.assertTrue(all(not isinstance(item, Exception) for item in results), results)
        self.assertEqual(results[0].id, results[1].id)
        self.assertEqual((self.count(Application), self.count(ApplicationEvent), self.count(ApplicationReceipt)), (1, 1, 1))

    def test_concurrent_application_update_keeps_state_timeline_and_summary_consistent(self):
        row = self.application()
        results = self.race(lambda index: self.call(applications.update_application, user_id=self.owner_id,
            application_id=row.id, payload=self.edit_payload(row, status=("submitted", "interview")[index],
                next_action={"title": f"合成行动 {index}"})))
        self.assertEqual(sum(isinstance(item, applications.ApplicationError) for item in results), 1)
        current = self.call(applications.get_application, user_id=self.owner_id, application_id=row.id)
        events = self.call(applications.list_events, user_id=self.owner_id, application_id=row.id)
        self.assertEqual(current.revision, 2)
        self.assertEqual(events.total, 3)
        status_event = next(item for item in events.items if item.type == "status_changed")
        self.assertEqual(status_event.changes.model_dump(), {"before": "preparing", "after": current.status})
        dashboard = self.call(get_dashboard, user_id=self.owner_id)
        self.assertEqual(dashboard.summary.total, 1)
        self.assertEqual(dashboard.actions.undated.total, 1)

    def test_failed_application_commit_rolls_back_revision_events_action_and_receipt(self):
        row = self.application()
        payload = self.edit_payload(row, status="interview", notes="Synthetic failure rollback",
            next_action={"title": "合成面试行动"})
        with self.assertRaises(applications.ApplicationError):
            self.call(applications.update_application, user_id=self.owner_id, application_id=row.id,
                payload=payload, session_class=FaultSession)
        current = self.call(applications.get_application, user_id=self.owner_id, application_id=row.id)
        self.assertEqual((current.revision, current.status, current.notes, current.next_action), (1, "preparing", "", None))
        self.assertEqual((self.count(ApplicationEvent), self.count(ApplicationReceipt)), (1, 1))

    def test_action_completion_and_terminal_cancellation_are_atomic(self):
        row = self.application()
        pending = self.call(applications.update_application, user_id=self.owner_id, application_id=row.id,
            payload=self.edit_payload(row, next_action={"title": "合成下一步"}))
        completed = self.call(applications.update_application, user_id=self.owner_id, application_id=row.id,
            payload=self.edit_payload(pending, next_action={"title": "合成下一步", "status": "completed"}))
        self.assertEqual(self.call(get_dashboard, user_id=self.owner_id).actions.completed.total, 1)
        ended = self.call(applications.update_application, user_id=self.owner_id, application_id=row.id,
            payload=self.edit_payload(completed, status="closed", next_action={"title": "合成新行动"}))
        self.assertEqual(ended.next_action.status, "cancelled")
        dashboard = self.call(get_dashboard, user_id=self.owner_id)
        self.assertEqual((dashboard.summary.ended, dashboard.actions.cancelled.total), (1, 1))

    def test_job_delete_and_application_create_are_serialized(self):
        job = self.job()
        def work(index):
            if index:
                return self.call(jobs.delete_job, user_id=self.owner_id, job_id=job.id, revision=job.revision)
            return self.application(job)
        results = self.race(work)
        self.assertEqual(sum(isinstance(item, Exception) for item in results), 1)
        with Session(self.engine) as db:
            application = db.scalar(select(Application))
            job_exists = db.get(SavedJob, job.id) is not None
            self.assertEqual(job_exists, application is not None)
            self.assertEqual(db.scalar(select(func.count()).select_from(ApplicationEvent)), int(application is not None))

    def test_application_delete_cascades_history_and_late_create_cannot_resurrect(self):
        job = self.job()
        create = ApplicationCreateRequest(job_id=job.id, job_revision=job.revision, idempotency_key=uuid4().hex)
        row = self.call(applications.create_application, user_id=self.owner_id, payload=create)
        delete = ApplicationDeleteRequest(revision=row.revision, idempotency_key=uuid4().hex, confirmed=True)
        self.call(applications.delete_application, user_id=self.owner_id, application_id=row.id, payload=delete)
        self.call(applications.delete_application, user_id=self.owner_id, application_id=row.id, payload=delete)
        with self.assertRaises(applications.ApplicationError) as caught:
            self.call(applications.create_application, user_id=self.owner_id, payload=create)
        self.assertEqual(caught.exception.code, "application_deleted")
        self.assertEqual((self.count(Application), self.count(ApplicationEvent)), (0, 0))
        self.call(jobs.delete_job, user_id=self.owner_id, job_id=job.id, revision=job.revision)
        self.assertEqual(self.count(SavedJob), 0)

    def test_upload_final_commit_failure_compensates_real_private_files(self):
        class FinalCommitFaultSession(FaultSession):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, fail_at=2, **kwargs)
        with self.assertRaises(ResumeUploadError):
            self.upload(session_class=FinalCommitFaultSession)
        self.assertEqual((self.count(ResumeDocument), self.count(ResumeFileOperation)), (0, 0))
        self.assertEqual(list((self.root / "private-objects").glob("*")), [])

    def test_concurrent_file_deletion_is_idempotent_and_leaves_one_tombstone(self):
        resume = self.upload()
        results = self.race(lambda _: self.call(delete_owned_resume, user_id=self.owner_id,
            resume_id=resume.id, storage=self.storage))
        self.assertEqual(results, [None, None])
        self.assertEqual((self.count(ResumeDocument), self.count(ResumeFileOperation)), (0, 1))
        state = self.call(get_owned_lifecycle, user_id=self.owner_id, resume_id=resume.id)
        self.assertEqual(state["deletion_status"], "deleted")
        self.assertEqual(list((self.root / "private-objects").glob("*")), [])

    def test_file_delete_failure_cleans_evidence_before_recovery_and_fences_late_writes(self):
        resume = self.upload()
        profile = self.ingest(resume.id)
        self.call(profiles.review_profile_candidate, user_id=self.owner_id, candidate_id=profile.candidates[0].id,
            payload=ProfileCandidateReviewRequest(revision=profile.revision, action="confirm"))
        self.storage.fail_delete = True
        with self.assertRaises(ResumeLifecycleError):
            self.call(delete_owned_resume, user_id=self.owner_id, resume_id=resume.id, storage=self.storage)
        with Session(self.engine) as db:
            document = db.get(ResumeDocument, resume.id)
            self.assertEqual((document.lifecycle_status, document.raw_text, document.text_layout), ("delete_failed", None, None))
        current = self.call(profiles.get_profile, user_id=self.owner_id)
        self.assertEqual((len(current.skills), len(current.candidates)), (1, 0))
        self.assertEqual((current.skills[0].source.kind, current.skills[0].source.evidence), ("resume_deleted", []))
        with self.assertRaises(ResumeLifecycleError):
            self.call(complete_resume_upload, user_id=self.owner_id, resume_id=resume.id,
                raw_text="Synthetic late data", byte_size=1, sha256="a" * 64)
        with self.assertRaises(profiles.ProfileError):
            self.ingest(resume.id)
        self.storage.fail_delete = False
        result = self.call(recover_pending_files, storage=self.storage,
            stale_before=datetime.now(timezone.utc) + timedelta(seconds=1))
        self.assertEqual((result.completed, result.failed), (1, 0))
        self.assertEqual((self.count(ResumeDocument), self.count(ProfileCandidate)), (0, 0))

    def test_upload_and_delete_wait_for_the_same_resume_row(self):
        self.storage.block_save = True
        delete_started = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            uploading = pool.submit(self.upload)
            self.assertTrue(self.storage.entered.wait(10))
            with Session(self.engine) as db:
                resume_id = db.scalar(select(ResumeDocument.id))
            self.assertIsNotNone(resume_id, "Upload journal must commit before object creation")
            def remove():
                delete_started.set()
                return self.call(delete_owned_resume, user_id=self.owner_id, resume_id=resume_id, storage=self.storage)
            deleting = pool.submit(remove)
            self.assertTrue(delete_started.wait(5))
            try:
                self.assertFalse(deleting.done())
            finally:
                self.storage.release.set()
            self.assertEqual(uploading.result(15).id, resume_id)
            self.assertIsNone(deleting.result(15))
        self.assertEqual(self.count(ResumeDocument), 0)
        self.assertEqual(list((self.root / "private-objects").glob("*")), [])

    def test_two_users_cannot_read_or_mutate_each_others_domain_rows(self):
        profile, job, resume = self.profile(), self.job(), self.upload()
        row = self.application(job)
        checks = [
            (jobs.get_job, {"job_id": job.id}, jobs.JobError),
            (applications.get_application, {"application_id": row.id}, applications.ApplicationError),
            (delete_owned_resume, {"resume_id": resume.id, "storage": self.storage}, ResumeLifecycleError),
        ]
        for fn, kwargs, error in checks:
            with self.subTest(fn=fn.__name__), self.assertRaises(error) as caught:
                self.call(fn, user_id=self.other_id, **kwargs)
            self.assertEqual(caught.exception.status_code, 404)
        other = self.call(profiles.get_profile, user_id=self.other_id)
        dashboard = self.call(get_dashboard, user_id=self.other_id)
        self.assertEqual((other.revision, other.skills, dashboard.summary.total, dashboard.onboarding.job_count), (0, [], 0, 0))
        self.assertEqual(self.call(profiles.get_profile, user_id=self.owner_id).revision, profile.revision)

    def test_real_database_permission_failure_returns_safe_503_and_hides_sql_parameters(self):
        marker = "SYNTHETIC_POSTGRES_PRIVATE_PARAMETER"
        with self.engine.connect() as conn:
            with self.assertRaises(SQLAlchemyError) as caught:
                conn.execute(text("SELECT :private FROM synthetic_missing_table"), {"private": marker})
        self.assertNotIn(marker, str(caught.exception))
        self.assertTrue(self.engine.hide_parameters)
        token = new_token()
        with Session(self.engine) as db:
            db.add(AuthSession(user_id=self.owner_id, token_hash=token_hash(token),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
            db.commit()
        app = create_app()
        def database():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_settings] = lambda: self.settings
        client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(client.close)
        client.cookies.set(SESSION_COOKIE_NAME, token)
        self.assertEqual(client.get("/api/v1/me").status_code, 200)
        migration_engine = build_engine(self.migration_url)
        try:
            with migration_engine.begin() as conn:
                role = conn.dialect.identifier_preparer.quote(self.engine.url.username)
                conn.execute(text(f"REVOKE SELECT ON auth_sessions FROM {role}"))
        finally:
            migration_engine.dispose()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger()
        logger.addHandler(handler)
        try:
            response = client.get("/api/v1/me")
        finally:
            logger.removeHandler(handler)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": {"code": "storage_unavailable", "message": "服务暂时不可用，请稍后重试"}})
        self.assertNotIn(token, response.text + stream.getvalue())
        for internal in ("SELECT", "psycopg", "Traceback", "postgresql", marker):
            self.assertNotIn(internal, response.text)
