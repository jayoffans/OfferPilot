"""Real PostgreSQL identity and bounded extraction transaction invariants.

Only synthetic local data and the network-free Mock provider are used. Every
test gets a separately migrated database and the restricted application role.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
import time
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from offerpilot_api.agent_runtime import extraction_runs
from offerpilot_api.agent_runtime.extraction_runs import (
    ExtractionRunError, _BudgetedProvider, _now, claim_extraction_run,
    create_extraction_run, execute_extraction_run, finish_extraction_run,
    mutate_extraction_run, recover_expired_runs, source_version,
)
from offerpilot_api.agent_runtime.extraction_schemas import ExtractionRunCreateRequest
from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.agent_runtime.providers.mock import MockResumeProvider
from offerpilot_api.core.config import get_settings
from offerpilot_api.db.models import (
    AuthSession, AuthThrottle, CareerProfile, CareerProfileEntry,
    ProfileCandidate, RegistrationInvitation, ResumeDocument,
    ResumeExtractionRun, User,
)
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, hash_password, token_hash
from offerpilot_api.identity.services import IdentityError, _take_attempt, create_invitation
from offerpilot_api.main import create_app
from offerpilot_api.resume_ingestion.extractor_v2 import extract_student_profile_v2
from offerpilot_api.resume_ingestion.synthetic_fixtures import SYNTHETIC_RESUME_TEXT
from tests.postgres.support import PostgresTestCase

PASSWORD = "Synthetic-PostgreSQL-password-2026"


class AuthRuntimePostgresTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.clients = []
        self.app = create_app()

        def database():
            with self.session_factory() as db:
                yield db

        self.app.dependency_overrides[get_db] = database
        self.app.dependency_overrides[get_settings] = lambda: self.settings
        self.settings.auth_ip_attempt_limit = 1000
        self.settings.auth_registration_attempt_limit = 100

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.app.state.byok_vault.clear()
        super().tearDown()

    def client(self):
        client = TestClient(self.app, raise_server_exceptions=False)
        self.clients.append(client)
        return client

    def invitation(self, email):
        with self.session_factory() as db:
            return create_invitation(db, email=email)

    def register(self, client, email, token):
        return client.post("/api/v1/auth/register", json={
            "email": email, "password": PASSWORD, "invite_token": token,
        })

    def login(self, client, email):
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def user(self):
        with self.session_factory() as db:
            user = User(email_normalized=f"synthetic-{uuid4().hex}@example.com", password_hash=hash_password(PASSWORD))
            db.add(user)
            db.commit()
            return user.id

    def document(self, user_id):
        with self.session_factory() as db:
            document = ResumeDocument(user_id=user_id, file_name="synthetic.pdf", file_path="", file_type="application/pdf",
                                      raw_text=SYNTHETIC_RESUME_TEXT, processing_status="parsed", lifecycle_status="active")
            db.add(document)
            db.commit()
            return document.id, source_version(document)

    def create_run(self, user_id, document, key=None):
        resume_id, version = document
        with self.session_factory() as db:
            return create_extraction_run(db, user_id=user_id, resume_id=resume_id, settings=self.settings,
                payload=ExtractionRunCreateRequest(idempotency_key=key or uuid4().hex, source_version=version, mode="mock"))

    def count(self, model):
        with self.session_factory() as db:
            return db.scalar(select(func.count()).select_from(model))

    def parallel(self, values, operation):
        start = Barrier(len(values))

        def invoke(value):
            start.wait(timeout=15)
            return operation(value)

        with ThreadPoolExecutor(max_workers=len(values)) as pool:
            return list(pool.map(invoke, values))

    def test_invitation_can_be_consumed_once_by_concurrent_registrations(self):
        email = "invitation-race@example.com"
        invitation = self.invitation(email)
        clients = [self.client() for _ in range(4)]
        responses = self.parallel(clients, lambda client: self.register(client, email, invitation))
        statuses = [response.status_code for response in responses]
        self.assertEqual(statuses.count(201), 1, statuses)
        self.assertTrue(all(status in (201, 400, 409) for status in statuses), statuses)
        self.assertEqual(self.count(User), 1)
        with self.session_factory() as db:
            self.assertIsNotNone(db.scalar(select(RegistrationInvitation)).consumed_at)

    def test_duplicate_email_rolls_back_only_losing_invitation(self):
        email = "unique-registration@example.com"
        invitations = [self.invitation(email), self.invitation(email)]
        clients = [self.client(), self.client()]
        statuses = self.parallel(list(zip(clients, invitations)), lambda item: self.register(item[0], email, item[1]).status_code)
        self.assertCountEqual(statuses, [201, 409])
        self.assertEqual(self.count(User), 1)
        with self.session_factory() as db:
            rows = db.scalars(select(RegistrationInvitation)).all()
            self.assertEqual(sum(row.consumed_at is not None for row in rows), 1)

    def test_new_and_expired_rate_limit_buckets_reserve_exact_capacity(self):
        for expired in (False, True):
            key = f"synthetic-rate-limit:{expired}"
            if expired:
                with self.session_factory() as db:
                    db.add(AuthThrottle(key_hash=token_hash(key), window_started_at=_now() - timedelta(seconds=61), attempt_count=3))
                    db.commit()

            def attempt(_):
                with self.session_factory() as db:
                    try:
                        _take_attempt(db, key=key, limit=3, window_seconds=60)
                        return "reserved"
                    except IdentityError as error:
                        return error.code

            outcomes = self.parallel(list(range(8)), attempt)
            self.assertEqual(outcomes.count("reserved"), 3, outcomes)
            self.assertEqual(outcomes.count("rate_limited"), 5, outcomes)
            with self.session_factory() as db:
                self.assertEqual(db.get(AuthThrottle, token_hash(key)).attempt_count, 3)

    def test_csrf_logout_and_session_replay_use_real_postgresql_state(self):
        email = "session-revocation@example.com"
        client = self.client()
        self.assertEqual(self.register(client, email, self.invitation(email)).status_code, 201)
        logged_in = self.login(client, email)
        token = client.cookies.get(SESSION_COOKIE_NAME)
        self.assertEqual(client.post("/api/v1/auth/logout").status_code, 403)
        self.assertEqual(client.post("/api/v1/auth/logout", headers={"x-csrf-token": "0" * 64}).status_code, 403)
        self.assertEqual(client.get("/api/v1/me").status_code, 200)
        self.assertEqual(client.post("/api/v1/auth/logout", headers={"x-csrf-token": logged_in.json()["csrf_token"]}).status_code, 204)
        replay = self.client()
        replay.cookies.set(SESSION_COOKIE_NAME, token)
        self.assertEqual(replay.get("/api/v1/me").status_code, 401)
        with self.session_factory() as db:
            self.assertIsNotNone(db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token))).revoked_at)

    def test_global_run_capacity_is_atomic_across_users_and_resume_rows(self):
        users = [self.user(), self.user()]
        inputs = [(users[index % 2], self.document(users[index % 2])) for index in range(8)]
        self.settings.extraction_max_active_total = 2
        self.settings.extraction_max_active_per_user = 10

        def create(item):
            try:
                return self.create_run(*item).status
            except ExtractionRunError as error:
                return error.code

        outcomes = self.parallel(inputs, create)
        self.assertEqual(outcomes.count("queued"), 2, outcomes)
        self.assertEqual(outcomes.count("extraction_capacity_reached"), 6, outcomes)
        self.assertEqual(self.count(ResumeExtractionRun), 2)

    def test_user_run_capacity_is_atomic_across_resume_rows(self):
        owner = self.user()
        inputs = [self.document(owner) for _ in range(6)]
        self.settings.extraction_max_active_per_user = 1

        def create(document):
            try:
                return self.create_run(owner, document).status
            except ExtractionRunError as error:
                return error.code

        outcomes = self.parallel(inputs, create)
        self.assertEqual(outcomes.count("queued"), 1, outcomes)
        self.assertEqual(outcomes.count("extraction_capacity_reached"), 5, outcomes)

    def test_concurrent_request_key_on_different_resumes_is_safe_conflict(self):
        owner = self.user()
        self.settings.extraction_max_active_per_user = 10
        inputs = [self.document(owner) for _ in range(6)]

        def create(document):
            try:
                return self.create_run(owner, document, key="same-owner-request-key").status
            except ExtractionRunError as error:
                self.assertEqual(error.status_code, 409)
                return error.code

        outcomes = self.parallel(inputs, create)
        self.assertEqual(outcomes.count("queued"), 1, outcomes)
        self.assertEqual(outcomes.count("extraction_idempotency_conflict"), 5, outcomes)
        self.assertEqual(self.count(ResumeExtractionRun), 1)

    def test_concurrent_claims_invoke_one_provider_and_commit_one_pending_batch(self):
        owner = self.user()
        created = self.create_run(owner, self.document(owner))
        provider = MockResumeProvider()
        outcomes = self.parallel(list(range(6)), lambda _: execute_extraction_run(self.session_factory,
            run_id=created.id, provider_factory=lambda: provider))
        self.assertEqual(outcomes.count(True), 1, outcomes)
        self.assertEqual(provider.calls, 1)
        with self.session_factory() as db:
            run = db.get(ResumeExtractionRun, created.id)
            self.assertEqual((run.status, run.attempt_count, run.call_count), ("succeeded", 1, 1))
            self.assertEqual(self.count(ProfileCandidate), run.candidate_count)
            self.assertTrue(all(item.status == "pending" for item in db.scalars(select(ProfileCandidate))))
        self.assertEqual(self.count(CareerProfileEntry), 0)

    def test_call_ledger_prevents_parallel_over_budget_invocations(self):
        owner = self.user()
        self.settings.extraction_max_calls = 1
        created = self.create_run(owner, self.document(owner))
        with self.session_factory() as db:
            claim = claim_extraction_run(db, run_id=created.id)
        provider = MockResumeProvider()
        budgeted = _BudgetedProvider(self.session_factory, claim, provider)

        def call(_):
            try:
                budgeted.generate_json(system_prompt="Synthetic", user_prompt='{"resume_text": "synthetic"}')
                return "called"
            except LLMProviderError:
                return "bounded"

        # The provider may reject an unregistered synthetic payload, but any
        # invocation still consumes the durable budget before the adapter runs.
        self.parallel(list(range(6)), call)
        self.assertEqual(provider.calls, 1)
        with self.session_factory() as db:
            self.assertEqual(db.get(ResumeExtractionRun, created.id).call_count, 1)

    def test_expired_lease_can_retry_and_old_token_cannot_insert_candidates(self):
        owner = self.user()
        created = self.create_run(owner, self.document(owner))
        with self.session_factory() as db:
            claim = claim_extraction_run(db, run_id=created.id, now=_now() - timedelta(seconds=100))
        with self.session_factory() as db:
            self.assertEqual(recover_expired_runs(db), 1)
            run = db.get(ResumeExtractionRun, created.id)
            retried = mutate_extraction_run(db, user_id=owner, run_id=run.id, revision=run.revision, action="retry", settings=self.settings)
            self.assertEqual(retried.status, "queued")
        result = extract_student_profile_v2(claim.raw_text, MockResumeProvider(), text_layout=claim.text_layout)
        with self.session_factory() as db:
            self.assertFalse(finish_extraction_run(db, claim=claim, result=result))
        self.assertEqual(self.count(ProfileCandidate), 0)
        self.assertTrue(execute_extraction_run(self.session_factory, run_id=created.id))
        with self.session_factory() as db:
            self.assertEqual(db.get(ResumeExtractionRun, created.id).attempt_count, 2)

    def test_cancel_during_provider_call_discards_late_result(self):
        owner = self.user()
        created = self.create_run(owner, self.document(owner))
        entered, release = Event(), Event()
        delegate = MockResumeProvider()

        class PausedProvider:
            def generate_json(self, **kwargs):
                entered.set()
                if not release.wait(timeout=15):
                    raise RuntimeError("Synthetic provider gate timed out")
                return delegate.generate_json(**kwargs)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execute_extraction_run, self.session_factory, run_id=created.id, provider_factory=PausedProvider)
            self.assertTrue(entered.wait(timeout=10))
            try:
                with self.session_factory() as db:
                    run = db.get(ResumeExtractionRun, created.id)
                    cancelled = mutate_extraction_run(db, user_id=owner, run_id=run.id, revision=run.revision, action="cancel", settings=self.settings)
                    self.assertEqual(cancelled.status, "cancelled")
            finally:
                release.set()
            self.assertFalse(future.result(timeout=15))
        self.assertEqual(self.count(ProfileCandidate), 0)
        self.assertEqual(self.count(CareerProfileEntry), 0)

    def test_candidate_flush_failure_rolls_back_candidate_and_success_together(self):
        owner = self.user()
        created = self.create_run(owner, self.document(owner))
        original_ingest = extraction_runs.ingest_profile_candidates

        def flush_then_fail(*args, **kwargs):
            original_ingest(*args, **kwargs)
            raise RuntimeError("Synthetic failure after candidate flush")

        with patch.object(extraction_runs, "ingest_profile_candidates", side_effect=flush_then_fail):
            self.assertFalse(execute_extraction_run(self.session_factory, run_id=created.id))
        self.assertEqual(self.count(ProfileCandidate), 0)
        self.assertEqual(self.count(CareerProfile), 0)
        with self.session_factory() as db:
            self.assertEqual(db.get(ResumeExtractionRun, created.id).status, "failed")

    def test_different_resume_completions_share_one_user_profile(self):
        owner = self.user()
        runs = [self.create_run(owner, self.document(owner)) for _ in range(2)]
        # A concurrent user-row transaction delays FK checks and makes the
        # empty-profile race reproducible, while allowing a correct parent
        # lock to serialize initialization before checking for the profile.
        with self.session_factory() as holder:
            holder.execute(select(User.id).where(User.id == owner).with_for_update())
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(execute_extraction_run, self.session_factory, run_id=run.id) for run in runs]
                deadline = time.monotonic() + 10
                blocked = 0
                try:
                    while time.monotonic() < deadline:
                        with self.engine.connect() as monitor:
                            blocked = monitor.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"))
                        if blocked >= 2:
                            break
                        time.sleep(0.01)
                finally:
                    holder.rollback()
                self.assertGreaterEqual(blocked, 2, "Both independent completions must reach the blocked transaction")
                outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(outcomes, [True, True])
        self.assertEqual(self.count(CareerProfile), 1)
        with self.session_factory() as db:
            candidate_total = sum(db.get(ResumeExtractionRun, run.id).candidate_count for run in runs)
            self.assertEqual(self.count(ProfileCandidate), candidate_total)
