"""No network: the authenticated disclosure is exactly the provider's resume data."""

import hashlib
import json
import unittest
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import test_byok_security as byok_harness
from sqlalchemy import select
from offerpilot_api.agent_runtime.byok import redact_resume
from offerpilot_api.agent_runtime.extraction_runs import execute_extraction_run
from offerpilot_api.db.models import CareerProfileEntry, ProfileCandidate, ResumeDocument, ResumeExtractionRun
from offerpilot_api.resume_ingestion.synthetic_fixtures import SYNTHETIC_RESUME_TEXT


class DeepSeekPreviewConsistencyTests(unittest.TestCase):
    # Reuse the real identity/API/SQLite harness without inheriting its tests.
    setUp = byok_harness.BYOKSecurityTests.setUp
    tearDown = byok_harness.BYOKSecurityTests.tearDown
    client = byok_harness.BYOKSecurityTests.client
    login = byok_harness.BYOKSecurityTests.login
    registered = byok_harness.BYOKSecurityTests.registered
    profile = byok_harness.BYOKSecurityTests.profile
    factory = byok_harness.BYOKSecurityTests.factory
    upload = byok_harness.BYOKSecurityTests.upload
    configure = byok_harness.BYOKSecurityTests.configure
    preview = byok_harness.BYOKSecurityTests.preview
    execute = byok_harness.BYOKSecurityTests.execute
    status = byok_harness.BYOKSecurityTests.status

    def authorize(self, resume_id, preview):
        response = self.owner.post(f"/api/v1/resumes/{resume_id}/extraction-runs", json={
            "mode": "deepseek", "idempotency_key": uuid4().hex,
            "source_version": preview["source_version"],
            "authorization": {
                "authorization_id": preview["authorization_id"], "accepted": True, "max_calls": 1,
            },
        })
        return response

    def change_source(self, resume_id):
        with self.factory() as db:
            db.get(ResumeDocument, resume_id).raw_text += "\n技能：SQL。"
            db.commit()

    def test_provider_resume_text_is_exact_preview_with_private_contacts_masked(self):
        self.configure()
        private = ("合成姓名甲", "13800138000", "preview-only@example.com", "https://example.com/private-resume")
        text = "姓名：合成姓名甲\n联系方式：13800138000 preview-only@example.com\n" + SYNTHETIC_RESUME_TEXT
        text = text.replace("技能：Python。", "技能：Python。13800138000 preview-only@example.com https://example.com/private-resume")
        resume_id = self.upload(text=text)
        preview = self.preview(resume_id)
        for value in private:
            self.assertNotIn(value, preview["redacted_text"])
        self.assertIn("Python", preview["redacted_text"])
        response = self.authorize(resume_id, preview)
        self.assertEqual(response.status_code, 202, response.text)
        provider = byok_harness.FakePaidProvider()
        self.assertTrue(self.execute(response.json(), provider))
        self.assertEqual(provider.calls, 1)
        # Extract the real V2 JSON wrapper rather than accepting a substring.
        prompt = provider.prompts[0]["user_prompt"]
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(set(payload), {"resume_text"})
        self.assertEqual(payload["resume_text"], preview["redacted_text"])
        self.assertEqual(hashlib.sha256(payload["resume_text"].encode()).hexdigest(), preview["sent_text_sha256"])
        for value in private:
            self.assertNotIn(value, prompt)
            self.assertNotIn(value, provider.prompts[0]["system_prompt"])
        run = self.status(response.json())
        self.assertEqual((run["status"], run["call_count"], run["max_calls"]), ("succeeded", 1, 1))
        self.assertTrue(all(item["status"] == "pending" for item in self.profile()["candidates"]))

    def test_changed_source_before_authorization_rejects_without_a_call(self):
        self.configure()
        resume_id = self.upload()
        preview = self.preview(resume_id)
        self.change_source(resume_id)
        response = self.authorize(resume_id, preview)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "source_version_changed")
        self.assertEqual(self.owner.get(f"/api/v1/resumes/{resume_id}/extraction").json()["recent_runs"], [])
        self.assertEqual(self.profile()["candidates"], [])

    def test_changed_source_before_execution_stops_with_zero_provider_calls(self):
        self.configure()
        resume_id = self.upload()
        response = self.authorize(resume_id, self.preview(resume_id))
        self.assertEqual(response.status_code, 202, response.text)
        self.change_source(resume_id)
        provider = byok_harness.FakePaidProvider()
        self.assertFalse(self.execute(response.json(), provider))
        self.assertEqual(provider.calls, 0)
        run = self.status(response.json())
        self.assertEqual((run["status"], run["error_code"], run["call_count"], run["billing_status"]),
                         ("failed", "source_version_changed", 0, "not_sent"))
        self.assertEqual(self.profile()["candidates"], [])

    def test_changed_redaction_before_execution_requires_new_preview_and_zero_calls(self):
        self.configure()
        resume_id = self.upload()
        response = self.authorize(resume_id, self.preview(resume_id))
        self.assertEqual(response.status_code, 202, response.text)
        provider = byok_harness.FakePaidProvider()
        with patch("offerpilot_api.agent_runtime.extraction_runs.redact_resume",
                   side_effect=lambda raw: redact_resume(raw) + "\n不同的脱敏输出"):
            self.assertFalse(self.execute(response.json(), provider))
        self.assertEqual(provider.calls, 0)
        run = self.status(response.json())
        self.assertEqual((run["status"], run["error_code"], run["call_count"], run["billing_status"]),
                         ("failed", "source_version_changed", 0, "not_sent"))
        self.assertEqual(self.profile()["candidates"], [])

    def test_queued_old_prompt_requires_new_authorization_without_constructing_provider(self):
        self.configure()
        resume_id = self.upload()
        response = self.authorize(resume_id, self.preview(resume_id))
        self.assertEqual(response.status_code, 202, response.text)
        run_id = UUID(response.json()["id"])
        with self.factory() as db:
            db.get(ResumeExtractionRun, run_id).prompt_version = "resume_extract_v2"
            db.commit()
        provider_factory = Mock(side_effect=AssertionError("Old authorization must never create a provider"))
        self.assertFalse(execute_extraction_run(self.factory, run_id=run_id, settings=self.settings,
            byok_vault=self.app.state.byok_vault, deepseek_provider_factory=provider_factory))
        provider_factory.assert_not_called()
        run = self.status(response.json())
        self.assertEqual((run["status"], run["error_code"], run["call_count"], run["billing_status"]),
                         ("failed", "extraction_version_changed", 0, "not_sent"))
        self.assertEqual(self.profile()["candidates"], [])

    def test_completed_old_prompt_metadata_remains_readable_and_confirmable(self):
        self.configure()
        resume_id = self.upload()
        response = self.authorize(resume_id, self.preview(resume_id))
        self.assertEqual(response.status_code, 202, response.text)
        provider = byok_harness.FakePaidProvider()
        self.assertTrue(self.execute(response.json(), provider))
        # Simulate the valid persisted provenance from before the template upgrade.
        with self.factory() as db:
            db.get(ResumeExtractionRun, UUID(response.json()["id"])).prompt_version = "resume_extract_v2"
            candidates = db.scalars(select(ProfileCandidate).where(ProfileCandidate.resume_id == resume_id)).all()
            self.assertTrue(candidates)
            for candidate in candidates:
                candidate.extraction_metadata = {**candidate.extraction_metadata, "prompt_version": "resume_extract_v2"}
            db.commit()
        profile = self.profile()
        self.assertTrue(all(item["extraction_metadata"]["prompt_version"] == "resume_extract_v2"
                            for item in profile["candidates"]))
        positive = next(item for item in profile["candidates"]
                        if item["category"] == "skill" and item["value"]["name"] == "Python")
        reviewed = self.owner.post(f"/api/v1/profile/candidates/{positive['id']}/review", json={
            "revision": profile["revision"], "action": "confirm",
        })
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["skills"][0]["name"], "Python")
        self.assertEqual(reviewed.json()["skills"][0]["source"]["run_id"], response.json()["id"])
        with self.factory() as db:
            candidate = db.get(ProfileCandidate, UUID(positive["id"]))
            entry = db.get(CareerProfileEntry, candidate.entry_id)
            self.assertIsNotNone(entry)
            self.assertEqual(candidate.extraction_metadata["prompt_version"], "resume_extract_v2")
            self.assertEqual(entry.extraction_metadata["run_id"], response.json()["id"])
        self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
