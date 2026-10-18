"""S5-2 deletion fencing and old-backup replay in isolated PostgreSQL only."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
import unittest
from uuid import uuid4

from sqlalchemy import func, select

from offerpilot_api.agent_runtime.extraction_runs import (
    ExtractionRunError, claim_extraction_run, create_extraction_run, execute_extraction_run,
    finish_extraction_run, source_version,
)
from offerpilot_api.agent_runtime.extraction_schemas import ExtractionRunCreateRequest
from offerpilot_api.agent_runtime.providers.mock import MockResumeProvider
from offerpilot_api.db.models import AccountDeletion, ProfileCandidate, ResumeDocument, ResumeExtractionRun, User
from offerpilot_api.identity.security import SESSION_COOKIE_NAME
from offerpilot_api.resume_ingestion.extractor_v2 import extract_student_profile_v2
from offerpilot_api.resume_ingestion.synthetic_fixtures import SYNTHETIC_RESUME_TEXT
from scripts.postgres.lifecycle_restore import (
    PASSWORD, ROOT, assert_revoked, client_for, rehearse, scoped_fingerprint, synthetic_account,
)
from tests.postgres.support import PostgresTestCase


class AccountLifecyclePostgresTests(PostgresTestCase):
    def ledger(self):
        from offerpilot_api.account_lifecycle.ledger import DeletionLedger
        return DeletionLedger(self.settings.deletion_ledger_dir)

    def replay(self, storage=None):
        from offerpilot_api.account_lifecycle.deletion import replay_deletion_registry
        with self.session_factory() as db:
            return replay_deletion_registry(db, settings=self.settings, storage=storage or self.storage, limit=100)

    def account(self, label="owner"):
        return synthetic_account(self, self.settings, label)

    def test_missing_registry_denies_old_sessions_and_password_login_without_auto_initialization(self):
        account = self.account()
        marker = Path(self.settings.deletion_ledger_dir) / "registry-v1"
        marker.unlink()
        with client_for(self, self.settings) as client:
            client.cookies.set(SESSION_COOKIE_NAME, account["token"])
            self.assertEqual(client.get("/api/v1/me").status_code, 503)
            client.cookies.clear()
            self.assertEqual(client.post("/api/v1/auth/login", json={
                "email": account["email"], "password": PASSWORD}).status_code, 503)
        self.assertFalse(marker.exists())
        with self.session_factory() as db:
            self.assertIsNotNone(db.get(User, account["user_id"]))

    def test_corrupt_registry_entry_denies_access_and_never_reopens_account(self):
        account = self.account()
        self.ledger().record(account["user_id"], uuid4(), datetime.now(timezone.utc))
        entry = Path(self.settings.deletion_ledger_dir) / f"{account['user_id'].hex}.json"
        entry.write_text('{"user_id":', encoding="utf-8")
        with client_for(self, self.settings) as client:
            client.cookies.set(SESSION_COOKIE_NAME, account["token"])
            self.assertEqual(client.get("/api/v1/me").status_code, 503)
            client.cookies.clear()
            self.assertEqual(client.post("/api/v1/auth/login", json={
                "email": account["email"], "password": PASSWORD}).status_code, 503)
        self.assertEqual(entry.read_text(encoding="utf-8"), '{"user_id":')

    def test_concurrent_registry_replay_is_idempotent_and_preserves_other_user(self):
        account, retained = self.account(), self.account("retained")
        before = scoped_fingerprint(self.engine, retained["user_id"], retained["email"])
        self.ledger().record(account["user_id"], uuid4(), datetime.now(timezone.utc))
        start = Barrier(2)

        def replay():
            start.wait(timeout=15)
            return self.replay()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: replay(), range(2)))
        self.assertTrue(all(result["failed"] == 0 for result in results), results)
        self.assertEqual(scoped_fingerprint(self.engine, retained["user_id"], retained["email"]), before)
        with self.session_factory() as db:
            self.assertIsNone(db.get(User, account["user_id"]))
            receipts = db.scalars(select(AccountDeletion).where(AccountDeletion.user_id == account["user_id"])).all()
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].status, "completed")
        self.assertEqual(self.replay()["failed"], 0)

    def test_late_mock_result_cannot_write_candidates_after_registry_deletion(self):
        account = self.account()
        with self.session_factory() as db:
            document = db.get(ResumeDocument, account["resume_id"])
            document.raw_text = SYNTHETIC_RESUME_TEXT
            document.text_layout = None
            db.commit()
            run = create_extraction_run(db, user_id=account["user_id"], resume_id=document.id,
                settings=self.settings, payload=ExtractionRunCreateRequest(idempotency_key=uuid4().hex,
                    source_version=source_version(document), mode="mock"))
            claim = claim_extraction_run(db, run_id=run.id)
        result = extract_student_profile_v2(claim.raw_text, MockResumeProvider(), text_layout=claim.text_layout)
        self.ledger().record(account["user_id"], uuid4(), datetime.now(timezone.utc))
        self.assertEqual(self.replay()["failed"], 0)
        with self.session_factory() as db:
            try:
                self.assertFalse(finish_extraction_run(db, claim=claim, result=result))
            except ExtractionRunError as error:
                self.assertEqual((error.code, error.status_code), ("account_deleted", 409))
            self.assertEqual(db.scalar(select(func.count()).select_from(ProfileCandidate)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResumeExtractionRun)), 0)
            self.assertIsNone(db.get(User, account["user_id"]))
        assert_revoked(self, self.settings, account)

    def test_registry_intent_before_database_commit_prevents_model_call_after_restart(self):
        account = self.account()
        with self.session_factory() as db:
            document = db.get(ResumeDocument, account["resume_id"])
            document.raw_text = SYNTHETIC_RESUME_TEXT
            document.text_layout = None
            db.commit()
            run = create_extraction_run(db, user_id=account["user_id"], resume_id=document.id,
                settings=self.settings, payload=ExtractionRunCreateRequest(idempotency_key=uuid4().hex,
                    source_version=source_version(document), mode="mock"))
            run_id = run.id
        # A process can die after fsync of the independent intent and before
        # its transaction marks User inactive. The ledger remains authoritative.
        self.ledger().record(account["user_id"], uuid4(), datetime.now(timezone.utc))
        with self.session_factory() as db:
            self.assertTrue(db.get(User, account["user_id"]).is_active)
        provider = MockResumeProvider()
        self.assertFalse(execute_extraction_run(self.session_factory, run_id=run_id,
            settings=self.settings, provider_factory=lambda: provider))
        self.assertEqual(provider.calls, 0)
        with self.session_factory() as db:
            self.assertNotEqual(db.get(ResumeExtractionRun, run_id).status, "succeeded")
        self.assertEqual(self.replay()["failed"], 0)


@unittest.skipUnless(os.environ.get("OFFERPILOT_POSTGRES_ADMIN_URL")
    and os.environ.get("OFFERPILOT_POSTGRES_APP_URL"), "Real PostgreSQL S5 URLs not configured")
class AccountLifecycleRestorePostgresTests(unittest.TestCase):
    def test_old_database_backup_replays_independent_registry_before_access(self):
        output = ROOT / "frontend" / "artifacts" / "s5-2" / f"restore-test-{uuid4().hex[:12]}"
        report = rehearse(output)
        self.assertTrue(report["old_session_denied_before_replay"])
        self.assertTrue(report["password_login_denied_before_replay"])
        self.assertTrue(report["cleanup_failure_observed"])
        self.assertTrue(report["retry_completed"])
        self.assertTrue(report["deleted_account_removed"])
        self.assertTrue(report["unrelated_user_row_fingerprints_equal"])
        self.assertTrue(report["legacy_null_owner_row_and_file_preserved"])
        self.assertTrue(report["replay_idempotent"])
