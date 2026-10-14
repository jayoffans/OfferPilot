"""Free tests: real sessions/DB/locks and injected, network-free paid adapters."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4
from unittest.mock import patch
from sqlalchemy import select
from offerpilot_api.agent_runtime.byok import now, redact_resume
from offerpilot_api.agent_runtime.extraction_runs import execute_extraction_run, claim_extraction_run, recover_expired_runs
from offerpilot_api.agent_runtime.llm_provider import LLMProviderError, ProviderUsage
from offerpilot_api.db.models import AuthSession, ResumeDocument, ResumeExtractionRun, User
from offerpilot_api.resume_ingestion.synthetic_fixtures import fixture_cases, fixture_response, SYNTHETIC_RESUME_TEXT
import test_extraction_runs as runtime_harness

KEY = "synthetic-key-no-paid-calls"


class FakePaidProvider:
    def __init__(self, response=None, error=None, gate=None):
        self.calls = 0
        self.response = response or fixture_response(fixture_cases()[0])
        self.error = error
        self.gate = gate
        self.last_usage = None
        self.prompts = []

    def generate_json(self, **kwargs):
        self.calls += 1
        self.prompts.append(kwargs)
        if self.gate:
            self.gate[0].set()
            if not self.gate[1].wait(5):
                raise RuntimeError("Synthetic gate timeout")
        if self.error:
            raise self.error
        self.last_usage = ProviderUsage(100, 200, 300)
        return self.response


class BYOKSecurityTests(unittest.TestCase):
    setUp = runtime_harness.ExtractionRunTests.setUp
    tearDown = runtime_harness.ExtractionRunTests.tearDown
    client = runtime_harness.ExtractionRunTests.client
    login = runtime_harness.ExtractionRunTests.login
    registered = runtime_harness.ExtractionRunTests.registered
    profile = runtime_harness.ExtractionRunTests.profile
    factory = runtime_harness.ExtractionRunTests.factory
    upload = runtime_harness.ExtractionRunTests.upload

    def configure(self, client=None):
        self.settings.deepseek_extraction_enabled = True
        response = (client or self.owner).put("/api/v1/ai/deepseek-key", json={"api_key": KEY})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def preview(self, resume_id, client=None):
        response = (client or self.owner).get(f"/api/v1/resumes/{resume_id}/extraction-preview")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_paid(self, resume_id=None):
        self.configure()
        resume_id = resume_id or self.upload()
        preview = self.preview(resume_id)
        payload = dict(mode="deepseek", source_version=preview["source_version"], idempotency_key=uuid4().hex,
                       authorization=dict(authorization_id=preview["authorization_id"], accepted=True, max_calls=1))
        response = self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json=payload)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json(), payload

    def execute(self, run, provider):
        return execute_extraction_run(self.factory, run_id=UUID(run["id"]), settings=self.settings,
            byok_vault=self.app.state.byok_vault, deepseek_provider_factory=lambda **kwargs: provider)

    def status(self, run):
        response = self.owner.get(f"/api/v1/extraction-runs/{run['id']}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_key_only_encrypted_memory_no_response_echo_and_cross_session_isolation(self):
        status = self.configure()
        self.assertTrue(status["configured"])
        self.assertNotIn(KEY, json.dumps(status))
        self.assertLessEqual((__import__('datetime').datetime.fromisoformat(status['expires_at']) - now()).total_seconds(), 3600)
        self.assertNotIn(KEY, repr(self.app.state.byok_vault._keys))
        item = next(iter(self.app.state.byok_vault._keys.values()))
        self.assertNotIn(KEY.encode(), item.ciphertext)
        self.assertFalse(self.other.get("/api/v1/ai/deepseek-key").json()["configured"])
        separate = self.client()
        self.login(separate, "profile-owner@example.com")
        self.assertFalse(separate.get("/api/v1/ai/deepseek-key").json()["configured"])
        with self.factory() as db:
            other_session = db.scalar(select(AuthSession).where(AuthSession.user_id == self.other_id))
            self.assertIsNone(self.app.state.byok_vault.resolve(db, user_id=self.other_id, session_id=other_session.id, reference=item.reference))
            self.assertNotIn(KEY, json.dumps({table: list(db.execute(__import__('sqlalchemy').text(f'SELECT * FROM {table}')).mappings()) for table in ('resume_extraction_runs',)}, default=str))

    def test_old_reference_cannot_revoke_new_key_or_its_consent(self):
        self.configure()
        original = next(iter(self.app.state.byok_vault._keys.values()))
        self.configure()
        current = next(iter(self.app.state.byok_vault._keys.values()))
        resume = self.upload()
        preview = self.preview(resume)
        with self.factory() as db:
            self.assertIsNone(self.app.state.byok_vault.resolve(db, user_id=self.owner_id, session_id=original.session_id, reference=original.reference))
            self.assertEqual(self.app.state.byok_vault.resolve(db, user_id=self.owner_id, session_id=current.session_id, reference=current.reference), KEY)
        self.assertTrue(self.owner.get("/api/v1/ai/deepseek-key").json()["configured"])
        self.assertIn(UUID(preview["authorization_id"]), self.app.state.byok_vault._consents)

    def test_anonymous_csrf_unsafe_origin_and_invalid_key_never_echo(self):
        self.settings.deepseek_extraction_enabled = True
        self.assertEqual(self.anonymous.get("/api/v1/ai/deepseek-key").status_code, 401)
        csrf = self.owner.headers.pop("X-CSRF-Token")
        self.assertEqual(self.owner.put("/api/v1/ai/deepseek-key", json={"api_key": KEY}).status_code, 403)
        self.owner.headers["X-CSRF-Token"] = csrf
        response = self.owner.put("/api/v1/ai/deepseek-key", json={"api_key": KEY}, headers={"origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        response = self.owner.put("/api/v1/ai/deepseek-key", json={"api_key": "private-secret with whitespace", "user_id": str(self.other_id)})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-secret", response.text)

    def test_expiry_revoke_password_change_and_account_deactivation(self):
        for mode in ("expire", "revoke", "password", "deactivate"):
            with self.subTest(mode=mode):
                self.configure()
                item = next(iter(self.app.state.byok_vault._keys.values()))
                if mode == "expire":
                    item.expires_at = now() - timedelta(seconds=1)
                elif mode == "revoke":
                    self.assertEqual(self.owner.delete("/api/v1/ai/deepseek-key").status_code, 200)
                else:
                    with self.factory() as db:
                        user = db.get(User, self.owner_id)
                        if mode == "password": user.password_hash += "-changed"
                        else: user.is_active = False
                        db.commit()
                with self.factory() as db:
                    self.assertIsNone(self.app.state.byok_vault.resolve(db, user_id=self.owner_id, session_id=item.session_id, reference=item.reference))
                if mode == "deactivate":
                    with self.factory() as db:
                        db.get(User, self.owner_id).is_active = True
                        db.commit()

    def test_upload_browsing_no_call_no_key_and_explicit_authorization(self):
        self.settings.deepseek_extraction_enabled = True
        with patch.dict('os.environ', {"DEEPSEEK_API_KEY": "developer-key-must-never-be-used"}):
            resume_id = self.upload()
            capability = self.owner.get(f"/api/v1/resumes/{resume_id}/extraction").json()
            self.assertEqual(capability["recent_runs"], [])
            payload = dict(mode="deepseek", source_version=capability["source_version"], idempotency_key="no-consent")
            self.assertEqual(self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json=payload).status_code, 422)
            self.assertEqual(self.owner.get(f"/api/v1/resumes/{resume_id}/extraction-preview").status_code, 409)
        self.configure()
        self.assertEqual(self.other.get(f"/api/v1/resumes/{resume_id}/extraction-preview").status_code, 404)
        preview = self.preview(resume_id)
        payload["authorization"] = {"authorization_id": preview["authorization_id"], "accepted": False, "max_calls": 1}
        self.assertEqual(self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json=payload).status_code, 422)
        payload["authorization"]["accepted"] = 1
        self.assertEqual(self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json=payload).status_code, 422)
        with self.factory() as db:
            self.assertEqual(db.scalars(select(ResumeExtractionRun)).all(), [])

    def test_preview_masks_name_phone_email_addresses_outside_sections_and_bound_key(self):
        text = "张三\n姓名：张三\n13800138000 x@example.com\n教育背景\n示例大学\n技术技能\nPython 13800138000 x@example.com\n住址：示例地址\n"
        sent = redact_resume(text)
        for private in ("张三", "13800138000", "x@example.com", "示例地址"):
            self.assertNotIn(private, sent)
        self.assertEqual(len(sent), len(text))
        self.assertIn("Python", sent)
        self.configure()
        resume_id = self.upload()
        preview = self.preview(resume_id)
        self.configure()  # Replacing the key invalidates this snapshot.
        response = self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json={"mode": "deepseek", "idempotency_key": "old-preview", "source_version": preview["source_version"], "authorization": {"authorization_id": preview["authorization_id"], "accepted": True, "max_calls": 1}})
        self.assertEqual(response.status_code, 409)

    def test_success_pending_four_groups_and_user_review_keeps_real_provenance(self):
        run, payload = self.create_paid()
        provider = FakePaidProvider()
        self.assertTrue(self.execute(run, provider))
        status = self.status(run)
        self.assertEqual((status["call_count"], status["max_calls"], status["max_attempts"]), (1, 1, 1))
        self.assertEqual(status["billing_status"], "usage_reported")
        self.assertEqual(status["total_tokens"], 300)
        profile = self.profile()
        self.assertTrue(all(profile[group] == [] for group in ("education", "skills", "projects", "experiences")))
        self.assertEqual({item["category"] for item in profile["candidates"]}, {"education", "skill", "project", "experience"})
        self.assertTrue(all(item["status"] == "pending" and not item["extraction_metadata"]["test_result"] for item in profile["candidates"]))
        positive = next(item for item in profile["candidates"] if item["category"] == "skill" and item["value"]["name"] == "Python")
        result = self.owner.post(f"/api/v1/profile/candidates/{positive['id']}/review", json={"revision": profile["revision"], "action": "confirm"})
        self.assertEqual(result.status_code, 200, result.text)
        source = result.json()["skills"][0]["source"]
        self.assertEqual((source["provider"], source["model"], source["run_id"], source["test_result"]), ("deepseek", "deepseek-flash", run["id"], False))
        duplicate = self.owner.post(f"/api/v1/resumes/{run['resume_id']}/extraction-runs", json=payload)
        self.assertEqual(duplicate.json()["id"], run["id"])
        self.assertFalse(self.execute(run, provider))
        self.assertEqual(provider.calls, 1)

    def test_fault_matrix_single_call_no_hidden_repair_and_no_retry(self):
        errors = ["provider_unauthorized", "provider_insufficient_balance", "provider_rate_limited", "provider_invalid_request", "provider_timeout", "provider_connection_error", "provider_server_error", "provider_truncated_response", "provider_empty_response"]
        for error in errors + ["invalid_json", "invalid_schema", "empty"]:
            with self.subTest(error=error):
                run, _ = self.create_paid()
                provider = FakePaidProvider(error=LLMProviderError("safe failure", code=error)) if error in errors else FakePaidProvider(response={"invalid_json": "{bad", "invalid_schema": "{}", "empty": '{"schema_version":"2.0","education":[],"skills":[],"projects":[],"experiences":[]}'}[error])
                self.assertFalse(self.execute(run, provider))
                status = self.status(run)
                self.assertEqual((status["status"], status["call_count"], provider.calls), ("failed", 1, 1))
                self.assertEqual(self.owner.post(f"/api/v1/extraction-runs/{run['id']}/retry", json={"revision": status["revision"]}).status_code, 409)
        self.assertEqual(self.profile()["candidates"], [])

    def test_excessive_supplier_usage_preserved_but_cannot_insert_candidates(self):
        class OversizedProvider(FakePaidProvider):
            def generate_json(self, **kwargs):
                result = super().generate_json(**kwargs)
                self.last_usage = ProviderUsage(100, 5000, 5100)
                return result
        run, _ = self.create_paid()
        provider = OversizedProvider()
        self.assertFalse(self.execute(run, provider))
        status = self.status(run)
        self.assertEqual((status["error_code"], status["call_count"], status["total_tokens"]), ("provider_usage_exceeded_bounds", 1, 5100))
        self.assertEqual(self.profile()["candidates"], [])

    def test_duplicate_and_concurrent_claim_only_one_charge(self):
        run, _ = self.create_paid()
        started, release = threading.Event(), threading.Event()
        provider = FakePaidProvider(gate=(started, release))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute, run, provider)
            self.assertTrue(started.wait(3))
            second = pool.submit(self.execute, run, provider)
            self.assertFalse(second.result(timeout=3))
            release.set()
            self.assertTrue(first.result(timeout=3))
        self.assertEqual(provider.calls, 1)

    def test_concurrent_run_admission_and_idempotent_request(self):
        run, payload = self.create_paid()
        self.assertEqual(self.owner.post(f"/api/v1/resumes/{run['resume_id']}/extraction-runs", json=payload).json()["id"], run["id"])
        other_resume = self.upload()
        preview = self.preview(other_resume)
        payload = {**payload, "idempotency_key": uuid4().hex, "source_version": preview["source_version"], "authorization": {"authorization_id": preview["authorization_id"], "accepted": True, "max_calls": 1}}
        self.assertEqual(self.owner.post(f"/api/v1/resumes/{other_resume}/extraction-runs", json=payload).status_code, 429)

    def test_two_sessions_parallel_admission_queues_only_one_paid_run(self):
        self.configure()
        second = self.client()
        self.login(second, "profile-owner@example.com")
        self.configure(second)
        first_resume, second_resume = self.upload(), self.upload(second)
        previews = [self.preview(first_resume), self.preview(second_resume, second)]
        barrier = threading.Barrier(2)
        def submit(client, resume, preview):
            barrier.wait(timeout=3)
            return client.post(f"/api/v1/resumes/{resume}/extraction-runs", json={"mode": "deepseek", "source_version": preview["source_version"],
                "idempotency_key": uuid4().hex, "authorization": {"authorization_id": preview["authorization_id"], "accepted": True, "max_calls": 1}}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, client, resume, preview) for client, resume, preview in zip((self.owner, second), (first_resume, second_resume), previews)]
            self.assertEqual(sorted(future.result(timeout=5) for future in futures), [202, 429])
        with self.factory() as db:
            runs = db.scalars(select(ResumeExtractionRun)).all()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].call_count, 0)

    def test_cancel_delete_revoke_logout_and_versions_discard_late_results(self):
        for action in ("cancel", "delete", "revoke", "logout", "change_version"):
            with self.subTest(action=action):
                run, _ = self.create_paid()
                started, release = threading.Event(), threading.Event()
                provider = FakePaidProvider(gate=(started, release))
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.execute, run, provider)
                    self.assertTrue(started.wait(3))
                    current = self.status(run)
                    if action == "cancel": self.owner.post(f"/api/v1/extraction-runs/{run['id']}/cancel", json={"revision": current["revision"]})
                    elif action == "delete": self.owner.delete(f"/api/v1/resumes/{run['resume_id']}")
                    elif action == "revoke": self.owner.delete("/api/v1/ai/deepseek-key")
                    elif action == "logout": self.owner.post("/api/v1/auth/logout")
                    else:
                        with self.factory() as db:
                            db.get(ResumeDocument, UUID(run["resume_id"])).raw_text += "\nversion changed"
                            db.commit()
                    release.set()
                    self.assertFalse(future.result(timeout=3))
                if action == "logout": self.login(self.owner, "profile-owner@example.com")
                self.assertEqual(self.profile()["candidates"], [])
                self.assertEqual(self.profile()["skills"], [])
                self.assertEqual(provider.calls, 1)

    def test_restart_expiry_and_lease_recovery_do_not_replay_charge(self):
        run, _ = self.create_paid()
        self.app.state.byok_vault.clear()
        provider = FakePaidProvider()
        self.assertFalse(self.execute(run, provider))
        self.assertEqual(provider.calls, 0)

        self.assertEqual(self.status(run)["call_count"], 0)
        run, _ = self.create_paid()
        with self.factory() as db:
            claim = claim_extraction_run(db, run_id=UUID(run["id"]))
        with self.factory() as db:
            persisted = db.get(ResumeExtractionRun, claim.id)
            persisted.call_count = 1
            persisted.billing_status = "unknown"
            persisted.lease_deadline = now() - timedelta(seconds=1)
            db.commit()
            recover_expired_runs(db)
        status = self.status(run)
        self.assertEqual((status["error_code"], status["billing_status"]), ("billing_outcome_unknown", "unknown"))
        self.assertFalse(self.execute(run, provider))
        self.assertEqual(provider.calls, 0)

    def test_read_recovers_expired_or_restarted_queued_run_even_when_dispatcher_disabled(self):
        run, _ = self.create_paid()
        self.app.state.byok_vault.clear()
        self.settings.deepseek_extraction_enabled = False
        status = self.status(run)
        self.assertEqual((status["status"], status["error_code"], status["call_count"], status["billing_status"]), ("failed", "key_unavailable", 0, "not_sent"))


if __name__ == "__main__":
    unittest.main()
