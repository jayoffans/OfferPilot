"""Free, network-forbidden PDF evaluation through real app/SQLite services."""

import copy
import hashlib
import json
import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import func, select
from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.evaluations.pdf_evaluation import (
    GROUPS, MOCK_NOTICE, SYNTHETIC_CONTACTS, MockResponseSpy,
    PdfEvaluationError, PdfEvaluationSession, synthetic_pdf_bytes,
)
from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.db.models import CareerProfileEntry, ProfileCandidate, ResumeDocument, ResumeExtractionRun


class PdfEvaluationPipelineTests(unittest.TestCase):
    def setUp(self):
        original_connect = socket.socket.connect
        def no_external_connect(connection, address):
            # Windows' asyncio self-pipe uses a loopback socket pair even when
            # TestClient runs purely in process. No external host is permitted.
            if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
                return original_connect(connection, address)
            raise AssertionError("External network forbidden")
        self.network = patch.object(socket.socket, "connect", no_external_connect)
        self.network.start()
        self.addCleanup(self.network.stop)
        self.session = PdfEvaluationSession().__enter__()
        self.addCleanup(self.session.__exit__, None, None, None)

    def prepared(self):
        return self.session.prepare("normal_chinese")

    def create_sample_run(self, prepared=None, *, mode="mock", key=None):
        prepared = prepared or self.prepared()
        run = self.session.create_run(prepared, mode=mode, confirmed_sha256=prepared.preview["sent_text_sha256"], api_key=key)
        return prepared, run

    def execute(self, prepared, run, provider=None):
        provider = provider or MockResponseSpy(prepared.case["response"])
        success = self.session.execute(prepared, run, provider_factory=lambda **kwargs: provider)
        return success, provider

    def count(self, model):
        with self.session.factory() as db:
            return db.scalar(select(func.count()).select_from(model))

    def test_real_pdf_file_private_storage_parser_hash_and_migrations(self):
        prepared = self.prepared()
        self.assertTrue(prepared.pdf_path.is_file())
        content = prepared.pdf_path.read_bytes()
        self.assertEqual(content, synthetic_pdf_bytes("normal_chinese"))
        self.assertEqual(prepared.pdf["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(prepared.pdf["page_count"], 2)
        self.assertEqual(prepared.pdf["processing_status"], "parsed")
        self.assertTrue(prepared.pdf["private_storage_byte_roundtrip"])
        self.assertEqual({item["page"] for item in prepared.text_layout["pages"]}, {1, 2})
        with self.session.factory() as db:
            self.assertEqual(db.get(ResumeDocument, prepared.resume_id).user_id, self.session.owner_id)
        self.assertTrue(any(self.session.settings.upload_dir.glob("*.pdf")))

    def test_final_production_provider_json_matches_redacted_disclosure_and_pdf_evidence(self):
        prepared, run = self.create_sample_run()
        success, provider = self.execute(prepared, run)
        self.assertTrue(success)
        report, result = self.session.collect(prepared, run)
        self.assertEqual(provider.calls, 1)
        current_head = ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parents[1] / "backend" / "alembic.ini"))).get_current_head()
        self.assertEqual(report["migration_head"], current_head)
        self.assertTrue(report["preview_matches_actual_provider_input"])
        self.assertEqual(report["actual_sent_sha256"], prepared.preview["sent_text_sha256"])
        self.assertEqual(self.session.spies[run["id"]].input_text, prepared.preview["redacted_text"])
        for private in SYNTHETIC_CONTACTS:
            self.assertIn(private, prepared.raw_text)
            self.assertNotIn(private, self.session.spies[run["id"]].input_text)
        self.assertEqual(report["notice"], MOCK_NOTICE)
        self.assertEqual(report["schema_validation"], "passed")
        self.assertEqual(len(result.batch.candidates), 5)
        self.assertTrue(all(field["verified_against_pdf_text"] for field in report["field_evidence"]))
        self.assertTrue(all(location["page"] in (1, 2) and location["paragraph"] is not None
            for field in report["field_evidence"] for location in field["locations"]))
        self.assertIsNone(report["token_usage"])
        self.assertIsNone(report["run"]["total_tokens"])
        self.assertEqual(report["paid_calls"], 0)

    def test_candidates_are_durable_pending_and_only_explicit_review_updates_profile(self):
        prepared, run = self.create_sample_run()
        self.assertTrue(self.execute(prepared, run)[0])
        report, _ = self.session.collect(prepared, run)
        self.assertTrue(report["unconfirmed_candidates_not_in_profile"])
        self.assertEqual(self.count(ProfileCandidate), 5)
        self.assertEqual(self.count(CareerProfileEntry), 0)
        candidates = report["candidates"]
        self.assertTrue(all(item["status"] == "pending" and item["extraction_metadata"]["test_result"] for item in candidates))
        restored = self.session.relogin()
        self.assertEqual({item["id"] for item in restored["candidates"]}, {item["id"] for item in candidates})
        self.assertTrue(all(not restored[group] for group in GROUPS))
        skill = next(item for item in candidates if item["category"] == "skill" and item["value"]["name"] == "Python")
        project = next(item for item in candidates if item["category"] == "project")
        review = self.session.review_demonstration(run, confirm_candidate_id=skill["id"], reject_candidate_id=project["id"])
        self.assertTrue(review["relogin_preserved_profile_and_candidates"])
        self.assertEqual(review["profile_after_confirmation"]["skills"][0]["name"], "Python")
        self.assertEqual(review["profile_after_rejection"]["projects"], [])
        self.assertEqual(self.count(CareerProfileEntry), 1)
        final = self.session.profile()
        rejected = next(item for item in final["candidates"] if item["id"] == project["id"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertTrue(final["skills"][0]["source"]["test_result"])

    def test_idempotent_create_and_repeated_execute_do_not_duplicate_candidates(self):
        prepared, run = self.create_sample_run()
        success, provider = self.execute(prepared, run)
        self.assertTrue(success)
        replay = self.session.owner.post(f"/api/v1/resumes/{prepared.resume_id}/extraction-runs",
                                        json=run["evaluation_create_payload"])
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(replay.json()["id"], run["id"])
        self.assertFalse(self.session.execute(prepared, run, provider_factory=lambda: provider))
        self.assertEqual((provider.calls, self.count(ProfileCandidate)), (1, 5))

    def test_anonymous_and_other_account_cannot_read_file_run_or_candidates(self):
        prepared, run = self.create_sample_run()
        self.assertTrue(self.execute(prepared, run)[0])
        for path in (f"/api/v1/resumes/{prepared.resume_id}",
                     f"/api/v1/resumes/{prepared.resume_id}/download",
                     f"/api/v1/extraction-runs/{run['id']}",
                     f"/api/v1/extraction-runs/{run['id']}/candidates"):
            self.assertEqual(self.session.other.get(path).status_code, 404)
            self.assertEqual(self.session.anonymous.get(path).status_code, 401)
        self.assertEqual(self.session.profile(self.session.other)["candidates"], [])

    def test_changed_pdf_text_invalidates_confirmation_before_run(self):
        prepared = self.prepared()
        with self.session.factory() as db:
            db.get(ResumeDocument, prepared.resume_id).raw_text += "\n技能：SQL。"
            db.commit()
        with self.assertRaises(PdfEvaluationError):
            self.session.create_run(prepared, confirmed_sha256=prepared.preview["sent_text_sha256"])
        self.assertEqual(self.count(ResumeExtractionRun), 0)

    def test_changed_redaction_or_wrong_confirmed_hash_rejects_before_run(self):
        prepared = self.prepared()
        with self.assertRaises(PdfEvaluationError):
            self.session.create_run(prepared, confirmed_sha256="0" * 64)
        with patch("backend.evaluations.pdf_evaluation.redact_resume", return_value="changed"):
            with self.assertRaises(PdfEvaluationError):
                self.session.create_run(prepared, confirmed_sha256=prepared.preview["sent_text_sha256"])
        self.assertEqual(self.count(ResumeExtractionRun), 0)

    def test_preview_metadata_tampering_and_mock_key_are_rejected_without_a_run(self):
        prepared = self.prepared()
        original = copy.deepcopy(prepared.preview)
        for field, value in (("sent_text_sha256", "0" * 64), ("model", "deepseek-pro"),
                             ("max_calls", 2), ("max_output_tokens", 1), ("max_input_tokens", 1),
                             ("redaction_version", "different")):
            with self.subTest(field=field):
                prepared.preview = {**original, field: value}
                with self.assertRaises(PdfEvaluationError):
                    self.session.create_run(prepared, confirmed_sha256=prepared.preview["sent_text_sha256"])
        prepared.preview = original
        with self.assertRaises(PdfEvaluationError):
            self.session.create_run(prepared, confirmed_sha256=original["sent_text_sha256"],
                                    api_key="synthetic-key-never-accepted-in-mock")
        self.assertEqual(self.count(ResumeExtractionRun), 0)

    def test_source_changed_after_run_prevents_provider_construction(self):
        prepared, run = self.create_sample_run()
        with self.session.factory() as db:
            db.get(ResumeDocument, prepared.resume_id).raw_text += "\n技能：SQL。"
            db.commit()
        with patch("backend.evaluations.pdf_evaluation.MockResponseSpy", side_effect=AssertionError("No construction")) as factory:
            self.assertFalse(self.session.execute(prepared, run, provider_factory=factory))
        factory.assert_not_called()
        report, _ = self.session.collect(prepared, run)
        self.assertEqual(report["run"]["error_code"], "source_version_changed")
        self.assertEqual(report["provider_invocations"], 0)
        self.assertEqual(self.count(ProfileCandidate), 0)

    def test_authenticated_deepseek_authorization_uses_only_injected_free_spy(self):
        key = "synthetic-protocol-key-not-real"
        prepared, run = self.create_sample_run(mode="deepseek", key=key)
        success, provider = self.execute(prepared, run)
        self.assertTrue(success)
        report, _ = self.session.collect(prepared, run, evaluation_kind="protocol_spy")
        self.assertTrue(report["preview_matches_actual_provider_input"])
        self.assertEqual((report["run"]["max_calls"], report["run"]["max_attempts"], provider.calls), (1, 1, 1))
        self.assertEqual(report["run"]["billing_status"], "unknown")
        self.assertIsNone(report["token_usage"])
        self.assertEqual(report["paid_calls"], 0)
        self.assertNotIn(key, json.dumps(report, ensure_ascii=False))
        retry = self.session.owner.post(f"/api/v1/extraction-runs/{run['id']}/retry", json={"revision": report["run"]["revision"]})
        self.assertEqual(retry.status_code, 409)

    def test_secret_in_unicode_escaped_provider_json_is_rejected_before_database_write(self):
        key = "synthetic-protocol-key-not-real"
        prepared, run = self.create_sample_run(mode="deepseek", key=key)
        response = copy.deepcopy(prepared.case["response"])
        response["skills"][0]["name"]["value"] = key
        serialized = json.dumps(response, ensure_ascii=True).replace(key, "".join(f"\\u{ord(c):04x}" for c in key))
        class SecretProvider:
            calls = 0
            last_usage = None
            def generate_json(self, **kwargs):
                self.calls += 1
                return serialized
        success, provider = self.execute(prepared, run, SecretProvider())
        self.assertFalse(success)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self.count(ProfileCandidate), 0)
        report, _ = self.session.collect(prepared, run, evaluation_kind="protocol_spy")
        self.assertEqual(report["run"]["error_code"], "provider_invalid_response")
        self.assertNotIn(key, json.dumps(report, ensure_ascii=False))
        self.assertNotIn(key.encode(), (self.session.root / "evaluation.db").read_bytes())

    def test_changed_paid_redaction_before_execute_does_not_send(self):
        prepared, run = self.create_sample_run(mode="deepseek", key="synthetic-protocol-key-not-real")
        provider = MockResponseSpy(prepared.case["response"])
        with patch("offerpilot_api.agent_runtime.extraction_runs.redact_resume", return_value="changed"):
            self.assertFalse(self.execute(prepared, run, provider)[0])
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.count(ProfileCandidate), 0)

    def test_ledger_failure_prevents_actual_provider_request(self):
        prepared, run = self.create_sample_run()
        provider = MockResponseSpy(prepared.case["response"])
        def ledger_unavailable():
            raise OSError("Synthetic ledger failure")
        self.assertFalse(self.session.execute(prepared, run, provider_factory=lambda: provider,
                                             before_call=ledger_unavailable))
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.count(ProfileCandidate), 0)

    def test_invalid_schema_single_budget_does_not_persist_or_repeat(self):
        prepared, run = self.create_sample_run()
        provider = MockResponseSpy({"schema_version": "1.0", "skills": ["Python"]})
        self.assertFalse(self.execute(prepared, run, provider)[0])
        report, _ = self.session.collect(prepared, run)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(report["run"]["call_count"], 1)
        self.assertEqual(report["run"]["status"], "failed")
        self.assertEqual(self.count(ProfileCandidate), 0)
        self.assertFalse(self.execute(prepared, run, provider)[0])
        self.assertEqual(provider.calls, 1)

    def test_timeout_preserves_unknown_billing_and_cannot_automatically_resend(self):
        prepared, run = self.create_sample_run(mode="deepseek", key="synthetic-protocol-key-not-real")
        class TimeoutProvider:
            calls = 0
            last_usage = None
            def generate_json(self, **kwargs):
                self.calls += 1
                raise LLMProviderError("Synthetic timeout", code="provider_timeout")
        provider = TimeoutProvider()
        self.assertFalse(self.execute(prepared, run, provider)[0])
        report, _ = self.session.collect(prepared, run, evaluation_kind="protocol_spy")
        self.assertEqual((report["run"]["billing_status"], report["run"]["call_count"], provider.calls), ("unknown", 1, 1))
        self.assertFalse(self.execute(prepared, run, provider)[0])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self.count(ProfileCandidate), 0)

    def test_deleted_resume_blocks_late_candidate_and_profile_writes(self):
        prepared, run = self.create_sample_run()
        entered, release = threading.Event(), threading.Event()
        delegate = MockResponseSpy(prepared.case["response"])
        class BlockedProvider:
            def generate_json(self, **kwargs):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("Synthetic test gate timeout")
                return delegate.generate_json(**kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.session.execute, prepared, run, provider_factory=BlockedProvider)
            self.assertTrue(entered.wait(5))
            try:
                response = self.session.owner.delete(f"/api/v1/resumes/{prepared.resume_id}")
                self.assertEqual(response.status_code, 204)
            finally:
                release.set()
            self.assertFalse(future.result(10))
        self.assertEqual((self.count(ProfileCandidate), self.count(CareerProfileEntry)), (0, 0))
        self.assertFalse(any(self.session.settings.upload_dir.glob("*.pdf")))
        with self.session.factory() as db:
            self.assertIsNone(db.get(ResumeDocument, prepared.resume_id))
        report, _ = self.session.collect(prepared, run)
        self.assertEqual(report["run"]["status"], "cancelled")

    def test_candidate_transaction_failure_rolls_back_success_and_candidates(self):
        prepared, run = self.create_sample_run()
        from offerpilot_api.agent_runtime import extraction_runs
        real_ingest = extraction_runs.ingest_profile_candidates
        def fail_after_flush(*args, **kwargs):
            real_ingest(*args, **kwargs)
            raise RuntimeError("Synthetic transaction failure")
        with patch.object(extraction_runs, "ingest_profile_candidates", side_effect=fail_after_flush):
            self.assertFalse(self.execute(prepared, run)[0])
        report, _ = self.session.collect(prepared, run)
        self.assertEqual(report["run"]["status"], "failed")
        self.assertEqual((self.count(ProfileCandidate), self.count(CareerProfileEntry)), (0, 0))

    def test_all_five_registered_pdf_sources_roundtrip_without_clipped_lines(self):
        from backend.evaluations.deepseek_v2_cases import CASE_IDS
        for case_id in CASE_IDS:
            with self.subTest(case_id=case_id):
                prepared = self.session.prepare(case_id)
                self.assertTrue(prepared.pdf["synthetic_whitelist_verified"])
                self.assertTrue(prepared.pdf["private_storage_byte_roundtrip"])
                self.assertTrue(prepared.preview["redacted_text"])

    def test_unregistered_source_cannot_enter_public_report_or_import_a_pdf(self):
        with self.assertRaises(PdfEvaluationError):
            self.session.prepare("external_personal_resume")
        self.assertEqual(self.count(ResumeDocument), 0)

    def test_default_mock_never_constructs_deepseek_adapter_or_opens_socket(self):
        prepared, run = self.create_sample_run()
        with patch("offerpilot_api.agent_runtime.providers.deepseek.DeepSeekProvider",
                   side_effect=AssertionError("Paid adapter forbidden")) as provider:
            self.assertTrue(self.execute(prepared, run)[0])
        provider.assert_not_called()
        self.assertEqual(self.session.spies[run["id"]].calls, 1)


if __name__ == "__main__":
    unittest.main()
