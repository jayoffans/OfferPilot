"""Trusted career profiles using real sessions and isolated synthetic data."""

import copy
import tempfile
import threading
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from offerpilot_api.account_lifecycle.ledger import DeletionLedger
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import (
    CareerProfile, CareerProfileEntry, Profile, ProfileCandidate, ProfileFact,
    ResumeDocument,
)
from offerpilot_api.db.session import build_engine, get_db
from offerpilot_api.identity.services import create_invitation
from offerpilot_api.main import create_app
from offerpilot_api.profile.schemas import CandidateBatchV1
from offerpilot_api.profile.services import ProfileError, ingest_profile_candidates


PASSWORD = "Synthetic-profile-password-2026"
GROUP_FIELDS = {
    "education": ("id", "school", "degree", "major", "start_date", "end_date", "end_date_kind"),
    "skills": ("id", "name", "level"),
    "projects": ("id", "name", "role", "description", "start_date", "end_date", "is_current"),
    "experiences": ("id", "organization", "role", "experience_type", "description", "start_date", "end_date", "is_current"),
}


def editable(profile: dict) -> dict:
    """Only documented client fields; source metadata is server controlled."""
    return {
        "revision": profile["revision"],
        **{group: [{key: entry[key] for key in fields if key in entry} for entry in profile[group]]
           for group, fields in GROUP_FIELDS.items()},
    }


def synthetic_pdf(text: str = "Synthetic resume: Python") -> bytes:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
        return pdf.tobytes()


class TrustedProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="offerpilot-profile-test-")
        self.root = Path(self.temporary_directory.name)
        database_url = f"sqlite:///{(self.root / 'profile.db').as_posix()}"
        self.engine = build_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.settings = Settings(
            database_url=database_url, upload_dir=self.root / "uploads", session_cookie_secure=False,
            deletion_ledger_dir=self.root / "deletion-ledger",
        )
        DeletionLedger(self.settings.deletion_ledger_dir).initialize()
        self.app = create_app()

        def test_db():
            with Session(self.engine) as session:
                yield session

        self.app.dependency_overrides[get_db] = test_db
        self.app.dependency_overrides[get_settings] = lambda: self.settings
        self.clients: list[TestClient] = []
        self.owner, self.owner_id = self.registered("profile-owner@example.com")
        self.other, self.other_id = self.registered("profile-other@example.com")
        self.anonymous = self.client()

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def client(self) -> TestClient:
        client = TestClient(self.app)
        self.clients.append(client)
        return client

    def login(self, client: TestClient, email: str) -> dict:
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return response.json()

    def registered(self, email: str) -> tuple[TestClient, UUID]:
        client = self.client()
        with Session(self.engine) as session:
            invitation = create_invitation(session, email=email)
        response = client.post("/api/v1/auth/register", json={
            "email": email, "password": PASSWORD, "invite_token": invitation,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return client, UUID(self.login(client, email)["user"]["id"])

    def profile(self, client: TestClient | None = None) -> dict:
        response = (client or self.owner).get("/api/v1/profile")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def save(self, payload: dict, client: TestClient | None = None, expected: int = 200):
        response = (client or self.owner).put("/api/v1/profile", json=payload)
        self.assertEqual(response.status_code, expected, response.text)
        return response

    def upload(self, client: TestClient | None = None, text: str = "Synthetic resume: Python") -> UUID:
        response = (client or self.owner).post("/api/v1/resumes", files={
            "file": ("synthetic-profile.pdf", synthetic_pdf(text), "application/pdf"),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return UUID(response.json()["id"])

    def ingest(self, *, resume_id: UUID | None = None, user_id: UUID | None = None,
               name: str = "Python", quote: str | None = "Synthetic resume: Python",
               batch_key: str | None = None) -> dict:
        if resume_id is None:
            resume_id = self.upload()
        batch = {
            "schema_version": "1.0", "extraction_version": "synthetic-fixture-v1",
            "candidates": [{"category": "skill", "value": {"name": name, "level": None},
                            "evidence": [{"field": "name", "source_text": quote}] if quote else []}],
        }
        with Session(self.engine) as session:
            return ingest_profile_candidates(
                session, user_id=user_id or self.owner_id, resume_id=resume_id,
                batch_key=batch_key or uuid4().hex, payload=batch,
            ).model_dump(mode="json")

    def review(self, candidate_id: str, revision: int, action: str, *, value: dict | None = None,
               client: TestClient | None = None, expected: int = 200):
        payload = {"revision": revision, "action": action}
        if value is not None:
            payload["value"] = value
        response = (client or self.owner).post(
            f"/api/v1/profile/candidates/{candidate_id}/review", json=payload,
        )
        self.assertEqual(response.status_code, expected, response.text)
        return response

    def test_empty_profile_is_unknown_and_has_one_user_level_persistence_record(self) -> None:
        initial = self.profile()
        self.assertEqual(initial["schema_version"], "1.0")
        self.assertEqual(initial["revision"], 0)
        for group in (*GROUP_FIELDS, "candidates"):
            self.assertEqual(initial[group], [])
        self.assertEqual(initial["completion"]["populated_groups"], [])
        self.assertEqual(initial["completion"]["pending_count"], 0)
        self.assertTrue(initial["completion"]["missing_fields"])
        self.assertEqual(self.profile(), initial)
        self.assertNotIn("score", initial["completion"])
        with Session(self.engine) as session:
            self.assertLessEqual(len(session.scalars(select(CareerProfile).where(
                CareerProfile.user_id == self.owner_id,
            )).all()), 1)
            self.assertEqual(session.scalars(select(CareerProfileEntry)).all(), [])

    def test_partial_manual_profile_is_saved_without_default_facts(self) -> None:
        payload = editable(self.profile())
        payload["education"] = [{"school": "合成大学", "degree": None, "major": None,
                                 "start_date": None, "end_date": "2027-06", "end_date_kind": "expected"}]
        result = self.save(payload).json()
        self.assertEqual(result["revision"], payload["revision"] + 1)
        self.assertEqual(result["education"][0]["school"], "合成大学")
        self.assertIsNone(result["education"][0]["degree"])
        self.assertIsNone(result["education"][0]["major"])
        self.assertEqual(result["education"][0]["source"]["kind"], "manual")
        for group in ("skills", "projects", "experiences"):
            self.assertEqual(result[group], [])
        self.assertEqual(self.profile(), result)

    def test_four_groups_can_be_created_modified_and_removed(self) -> None:
        payload = editable(self.profile())
        payload.update({
            "education": [{"school": "合成大学", "degree": "本科", "major": "软件工程",
                           "start_date": "2023-09", "end_date": "2027-06", "end_date_kind": "expected"}],
            "skills": [{"name": "Python", "level": "项目使用"}],
            "projects": [{"name": "合成任务板", "role": "开发", "description": "实现合成资料管理",
                          "start_date": "2025-01", "end_date": None, "is_current": True}],
            "experiences": [{"organization": "合成企业", "role": "实习开发", "experience_type": "internship",
                             "description": None, "start_date": "2026-07", "end_date": "2026-09", "is_current": False}],
        })
        initial = self.save(payload).json()
        self.assertTrue(all(initial[group][0]["source"]["kind"] == "manual" for group in GROUP_FIELDS))
        changed = editable(initial)
        changed["skills"][0]["level"] = "用户补充说明"
        changed["projects"] = []
        saved = self.save(changed).json()
        self.assertEqual(saved["skills"][0]["id"], initial["skills"][0]["id"])
        self.assertEqual(saved["skills"][0]["level"], "用户补充说明")
        self.assertEqual(saved["projects"], [])
        self.assertEqual(self.profile(), saved)

    def test_profile_survives_new_request_logout_and_fresh_login(self) -> None:
        payload = editable(self.profile())
        payload["skills"] = [{"name": "合成技能", "level": None}]
        saved = self.save(payload).json()
        self.assertEqual(self.owner.post("/api/v1/auth/logout").status_code, 204)
        self.assertEqual(self.owner.get("/api/v1/profile").status_code, 401)
        resumed = self.client()
        self.login(resumed, "profile-owner@example.com")
        self.assertEqual(self.profile(resumed), saved)

    def test_candidates_never_enter_formal_profile_before_explicit_review(self) -> None:
        candidate = self.ingest()
        self.assertEqual(candidate["skills"], [])
        self.assertEqual(candidate["candidates"][0]["status"], "pending")
        self.assertEqual(candidate["candidates"][0]["evidence_status"], "supported")
        self.assertEqual(candidate["completion"]["pending_count"], 1)
        self.assertEqual(self.profile()["skills"], [])
        with Session(self.engine) as session:
            self.assertEqual(session.scalars(select(CareerProfileEntry)).all(), [])

    def test_confirmed_candidate_keeps_original_evidence_and_review_state(self) -> None:
        initial = self.ingest()
        candidate = initial["candidates"][0]
        result = self.review(candidate["id"], initial["revision"], "confirm").json()
        self.assertEqual(result["skills"][0]["name"], "Python")
        self.assertEqual(result["skills"][0]["source"]["kind"], "resume_confirmed")
        self.assertEqual(result["skills"][0]["source"]["resume_id"], candidate["resume_id"])
        self.assertTrue(result["skills"][0]["source"]["evidence"])
        self.assertEqual(result["candidates"][0]["status"], "confirmed")
        self.assertEqual(result["candidates"][0]["entry_id"], result["skills"][0]["id"])
        self.assertEqual(result["completion"]["pending_count"], 0)
        self.assertEqual(self.profile(), result)

    def test_modify_candidate_marks_user_input_without_claiming_new_value_has_resume_evidence(self) -> None:
        initial = self.ingest()
        result = self.review(initial["candidates"][0]["id"], initial["revision"], "modify",
                             value={"name": "TypeScript", "level": "用户说明"}).json()
        self.assertEqual(result["skills"][0]["name"], "TypeScript")
        self.assertEqual(result["skills"][0]["source"]["kind"], "user_modified")
        self.assertEqual(result["candidates"][0]["status"], "modified")
        self.assertEqual(result["candidates"][0]["value"]["name"], "Python")

    def test_reject_candidate_does_not_add_formal_fact(self) -> None:
        initial = self.ingest()
        result = self.review(initial["candidates"][0]["id"], initial["revision"], "reject").json()
        self.assertEqual(result["skills"], [])
        self.assertEqual(result["candidates"][0]["status"], "rejected")
        self.assertEqual(result["completion"]["pending_count"], 0)
        self.assertEqual(self.profile(), result)

    def test_missing_evidence_requires_user_supplied_value_and_is_never_automatically_confirmed(self) -> None:
        initial = self.ingest(name="Unsubstantiated synthetic skill", quote=None)
        self.assertEqual(initial["candidates"][0]["evidence_status"], "unsupported")
        candidate_id = initial["candidates"][0]["id"]
        response = self.review(candidate_id, initial["revision"], "confirm", expected=422)
        self.assertEqual(response.json()["detail"]["code"], "candidate_evidence_requires_user_input")
        self.assertEqual(self.profile(), initial)
        corrected = self.review(candidate_id, initial["revision"], "modify",
                                value={"name": "用户自行提供的合成技能", "level": None}).json()
        self.assertEqual(corrected["skills"][0]["source"]["kind"], "user_modified")

    def test_negative_java_evidence_cannot_confirm_positive_skill(self) -> None:
        resume_id = self.upload(text="Not proficient in Java")
        initial = self.ingest(resume_id=resume_id, name="Java", quote="Not proficient in Java")
        self.assertEqual(initial["candidates"][0]["status"], "pending")
        self.assertIn(initial["candidates"][0]["evidence_status"], ("needs_review", "unsupported"))
        self.review(initial["candidates"][0]["id"], initial["revision"], "confirm", expected=422)
        self.assertEqual(self.profile()["skills"], [])

    def test_nonexistent_quote_is_untrusted_and_does_not_enter_formal_collection(self) -> None:
        initial = self.ingest(quote="Fabricated quote Python")
        self.assertEqual(initial["candidates"][0]["evidence_status"], "unsupported")
        self.review(initial["candidates"][0]["id"], initial["revision"], "confirm", expected=422)
        self.assertEqual(self.profile()["skills"], [])

    def test_java_is_not_supported_by_javascript_token(self) -> None:
        resume_id = self.upload(text="Synthetic resume: JavaScript")
        initial = self.ingest(resume_id=resume_id, name="Java", quote="Synthetic resume: JavaScript")
        self.assertEqual(initial["candidates"][0]["evidence_status"], "unsupported")
        self.review(initial["candidates"][0]["id"], initial["revision"], "confirm", expected=422)
        self.assertEqual(self.profile()["skills"], [])

    def test_explicit_chinese_negative_skill_claims_require_user_input(self) -> None:
        for quote in ("不熟练掌握 Java", "未熟练掌握 Java", "无 Java 开发经历"):
            with self.subTest(quote=quote):
                resume_id = self.upload()
                # A synthetic parsed text fixture avoids relying on installed
                # Chinese PDF fonts; no model or real personal data is used.
                with Session(self.engine) as session:
                    session.get(ResumeDocument, resume_id).raw_text = quote
                    session.commit()
                initial = self.ingest(resume_id=resume_id, name="Java", quote=quote)
                candidate = next(item for item in initial["candidates"] if item["resume_id"] == str(resume_id))
                self.assertIn(candidate["evidence_status"], ("needs_review", "unsupported"))
                self.review(candidate["id"], initial["revision"], "confirm", expected=422)
                self.assertEqual(self.profile()["skills"], [])

    def test_shortened_quotes_cannot_hide_negative_or_uncertain_resume_context(self) -> None:
        for raw_text, name, quote in (
            ("Not proficient in Java", "Java", "Java"),
            ("Ｎｏｔ proficient in Java", "Java", "Java"),
            ("n\u200bot proficient in Java", "Java", "Java"),
            ("Planning to learn Java", "Java", "Java"),
            ("未熟练掌握 Java", "Java", "Java"),
            ("没有 Java 项目经历", "Java", "Java"),
            ("未熟练掌握测试", "测试", "测试"),
            ("Not proficient in " + "synthetic filler " * 200 + "Java", "Java", "Java"),
        ):
            with self.subTest(raw_text=raw_text):
                resume_id = self.upload()
                with Session(self.engine) as session:
                    session.get(ResumeDocument, resume_id).raw_text = raw_text
                    session.commit()
                initial = self.ingest(resume_id=resume_id, name=name, quote=quote)
                candidate = next(item for item in initial["candidates"] if item["resume_id"] == str(resume_id))
                self.assertIn(candidate["evidence_status"], ("needs_review", "unsupported"))
                self.review(candidate["id"], initial["revision"], "confirm", expected=422)
                self.assertEqual(self.profile()["skills"], [])

    def test_ambiguous_repeated_short_quote_does_not_select_only_the_positive_occurrence(self) -> None:
        for raw_text in ("Proficient in Java.\nNot proficient in Java.",
                         "Proficient in Java.\n" * 1000 + "Not proficient in Java."):
            with self.subTest(occurrences=raw_text.count("Java")):
                resume_id = self.upload()
                with Session(self.engine) as session:
                    session.get(ResumeDocument, resume_id).raw_text = raw_text
                    session.commit()
                initial = self.ingest(resume_id=resume_id, name="Java", quote="Java")
                candidate = next(item for item in initial["candidates"] if item["resume_id"] == str(resume_id))
                self.assertIn(candidate["evidence_status"], ("needs_review", "unsupported"))
                self.review(candidate["id"], initial["revision"], "confirm", expected=422)
                self.assertEqual(self.profile()["skills"], [])

    def test_short_quotes_cannot_hide_ascii_or_unicode_word_boundaries_in_original_text(self) -> None:
        for raw_text in ("JavaScript", "Javaé", "éJava", "JavaＳｃｒｉｐｔ", "Java\u0301", "Java\u200bScript"):
            with self.subTest(raw_text=raw_text):
                resume_id = self.upload()
                with Session(self.engine) as session:
                    session.get(ResumeDocument, resume_id).raw_text = raw_text
                    session.commit()
                initial = self.ingest(resume_id=resume_id, name="Java", quote="Java")
                candidate = next(item for item in initial["candidates"] if item["resume_id"] == str(resume_id))
                self.assertIn(candidate["evidence_status"], ("unsupported", "needs_review"))
                self.review(candidate["id"], initial["revision"], "confirm", expected=422)
                self.assertEqual(self.profile()["skills"], [])

    def test_client_cannot_forge_owner_source_or_candidate_status(self) -> None:
        initial = self.profile()
        payloads = [
            {**editable(initial), "user_id": str(self.other_id)},
            {**editable(initial), "skills": [{"name": "Python", "source": {"kind": "resume_confirmed"}}]},
            {**editable(initial), "candidates": [{"status": "confirmed"}]},
        ]
        for payload in payloads:
            with self.subTest(payload_keys=list(payload)):
                self.save(payload, expected=422)
        self.assertEqual(self.profile(), initial)
        self.assertEqual(self.profile(self.other)["skills"], [])

    def test_two_users_have_independent_profiles_and_forged_query_does_not_select_other_user(self) -> None:
        payload = editable(self.profile())
        payload["skills"] = [{"name": "Owner-only synthetic skill"}]
        owned = self.save(payload).json()
        self.assertEqual(self.profile(self.other)["skills"], [])
        forged = self.other.get("/api/v1/profile", params={"user_id": str(self.owner_id)})
        self.assertEqual(forged.status_code, 200)
        self.assertEqual(forged.json()["skills"], [])
        copied = editable(owned)
        copied["revision"] = self.profile(self.other)["revision"]
        self.save(copied, self.other, expected=404)
        self.assertEqual(self.profile(), owned)

    def test_cross_user_and_unknown_candidate_ids_share_safe_404_and_never_change_owner(self) -> None:
        initial = self.ingest()
        results = []
        for candidate_id in (initial["candidates"][0]["id"], str(uuid4())):
            results.append(self.review(candidate_id, 0, "confirm", client=self.other, expected=404).json())
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["detail"]["code"], "profile_resource_not_found")
        self.assertEqual(self.profile(), initial)

    def test_unknown_entry_id_cannot_be_used_to_insert_or_reassign_formal_data(self) -> None:
        payload = editable(self.profile())
        payload["skills"] = [{"id": str(uuid4()), "name": "Synthetic forged ID"}]
        self.save(payload, expected=404)
        self.assertEqual(self.profile()["skills"], [])

    def test_authentication_csrf_and_origin_are_required_for_profile_writes_and_reviews(self) -> None:
        payload = editable(self.profile())
        self.assertEqual(self.anonymous.get("/api/v1/profile").status_code, 401)
        self.assertEqual(self.anonymous.put("/api/v1/profile", json=payload).status_code, 401)
        initial = self.ingest()
        candidate_id = initial["candidates"][0]["id"]
        self.assertEqual(self.anonymous.post(f"/api/v1/profile/candidates/{candidate_id}/review",
                                           json={"revision": initial["revision"], "action": "confirm"}).status_code, 401)
        csrf_token = self.owner.headers.pop("X-CSRF-Token")
        try:
            self.save(editable(initial), expected=403)
            self.review(candidate_id, initial["revision"], "confirm", expected=403)
        finally:
            self.owner.headers["X-CSRF-Token"] = csrf_token
        response = self.owner.put("/api/v1/profile", json=editable(initial), headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.profile(), initial)

    def test_interleaved_requests_detect_stale_revision_without_silent_overwrite(self) -> None:
        stale = editable(self.profile())
        current = copy.deepcopy(stale)
        current["skills"] = [{"name": "First saved synthetic fact"}]
        saved = self.save(current).json()
        stale["skills"] = [{"name": "Stale synthetic fact"}]
        failed = self.save(stale, expected=409)
        self.assertEqual(failed.json()["detail"]["code"], "profile_revision_conflict")
        self.assertEqual(self.profile(), saved)

    def test_real_two_thread_same_revision_writes_have_one_winner_and_one_409(self) -> None:
        initial = editable(self.profile())
        second = self.client()
        self.login(second, "profile-owner@example.com")
        barrier = threading.Barrier(2)

        def concurrent_save(client: TestClient, name: str):
            payload = copy.deepcopy(initial)
            payload["skills"] = [{"name": name}]
            barrier.wait(timeout=5)
            response = client.put("/api/v1/profile", json=payload)
            return response.status_code, response.json(), name

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(concurrent_save, self.owner, "Concurrent synthetic A"),
                       pool.submit(concurrent_save, second, "Concurrent synthetic B")]
            outcomes = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), [200, 409], outcomes)
        winner = next(outcome for outcome in outcomes if outcome[0] == 200)
        self.assertEqual(self.profile()["skills"][0]["name"], winner[2])
        self.assertEqual(self.profile()["revision"], initial["revision"] + 1)

    def test_review_and_save_share_one_revision_and_duplicate_review_is_rejected(self) -> None:
        initial = self.ingest()
        candidate_id = initial["candidates"][0]["id"]
        result = self.review(candidate_id, initial["revision"], "confirm").json()
        self.save(editable(initial), expected=409)
        self.review(candidate_id, initial["revision"], "confirm", expected=409)
        self.review(candidate_id, result["revision"], "confirm", expected=409)
        self.assertEqual(len(self.profile()["skills"]), 1)

    def test_candidate_ingestion_is_idempotent_and_does_not_reset_reviewed_fact(self) -> None:
        resume_id, batch_key = self.upload(), "synthetic-idempotent-batch"
        first = self.ingest(resume_id=resume_id, batch_key=batch_key)
        self.assertEqual(self.ingest(resume_id=resume_id, batch_key=batch_key), first)
        confirmed = self.review(first["candidates"][0]["id"], first["revision"], "confirm").json()
        repeated = self.ingest(resume_id=resume_id, batch_key=batch_key)
        self.assertEqual(repeated, confirmed)
        self.assertEqual(len(repeated["candidates"]), 1)
        self.assertEqual(len(repeated["skills"]), 1)

    def test_internal_ingestion_validates_resume_owner_and_lifecycle(self) -> None:
        resume_id = self.upload()
        payload = {"schema_version": "1.0", "extraction_version": "synthetic-v1", "candidates": [
            {"category": "skill", "value": {"name": "Python"}, "evidence": []},
        ]}
        for wrong_owner in (self.other_id, None, str(self.owner_id)):
            with self.subTest(owner_type=type(wrong_owner).__name__), Session(self.engine) as session:
                with self.assertRaises(ProfileError):
                    ingest_profile_candidates(session, user_id=wrong_owner, resume_id=resume_id,
                                              batch_key=uuid4().hex, payload=payload)
        self.assertEqual(self.owner.delete(f"/api/v1/resumes/{resume_id}").status_code, 204)
        with Session(self.engine) as session:
            with self.assertRaises(ProfileError):
                ingest_profile_candidates(session, user_id=self.owner_id, resume_id=resume_id,
                                          batch_key=uuid4().hex, payload=payload)
        self.assertEqual(self.profile()["candidates"], [])

    def test_internal_candidate_schema_validation_is_bounded_and_revalidates_constructed_models(self) -> None:
        resume_id = self.upload()
        valid = {"schema_version": "1.0", "extraction_version": "synthetic-v1", "candidates": [
            {"category": "skill", "value": {"name": "Python"}, "evidence": []},
        ]}
        malformed = [
            {**valid, "schema_version": "9.9"}, {**valid, "candidates": []},
            {**valid, "candidates": valid["candidates"] * 101},
            {**valid, "user_id": str(self.other_id)},
            CandidateBatchV1.model_construct(schema_version="9.9", extraction_version="synthetic-v1",
                                            candidates=[{"category": "skill", "value": {
                                                "name": "PRIVATE_SYNTHETIC_SERIALIZER_MARKER",
                                            }, "evidence": []}]),
        ]
        for payload in malformed:
            with self.subTest(payload_type=type(payload).__name__), Session(self.engine) as session, warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always")
                with self.assertRaises(ProfileError):
                    ingest_profile_candidates(session, user_id=self.owner_id, resume_id=resume_id,
                                              batch_key=uuid4().hex, payload=payload)
                self.assertTrue(all("PRIVATE_SYNTHETIC_SERIALIZER_MARKER" not in str(item.message) for item in emitted))
        self.assertEqual(self.profile()["candidates"], [])

    def test_review_requires_explicit_modify_value_and_rejects_invented_status_or_owner(self) -> None:
        initial = self.ingest()
        path = f"/api/v1/profile/candidates/{initial['candidates'][0]['id']}/review"
        invalid = [
            {"revision": initial["revision"], "action": "modify"},
            {"revision": initial["revision"], "action": "confirm", "value": {"name": "Python"}},
            {"revision": initial["revision"], "action": "reject", "user_id": str(self.other_id)},
            {"revision": initial["revision"], "action": "confirmed"},
            {"revision": initial["revision"], "action": "modify", "value": {"school": "Wrong category"}},
        ]
        for payload in invalid:
            with self.subTest(action=payload["action"]):
                self.assertEqual(self.owner.post(path, json=payload).status_code, 422)
        self.assertEqual(self.profile(), initial)

    def test_legacy_resume_profiles_are_not_implicitly_trusted_or_merged(self) -> None:
        resume_id = self.upload()
        with Session(self.engine) as session:
            legacy = Profile(resume_id=resume_id, skills=["Legacy unreviewed Python"])
            legacy.facts.append(ProfileFact(field_name="skills", value="Legacy unreviewed Python",
                                            confidence=1.0, source_text="Synthetic resume: Python"))
            session.add(legacy)
            session.commit()
        self.assertEqual(self.profile()["skills"], [])
        self.assertEqual(self.profile()["candidates"], [])

    def test_deleting_resume_erases_candidates_evidence_and_preserves_independent_user_facts(self) -> None:
        resume_id = self.upload()
        initial = self.ingest(resume_id=resume_id)
        confirmed = self.review(initial["candidates"][0]["id"], initial["revision"], "confirm").json()
        payload = editable(confirmed)
        payload["education"] = [{"school": "合成手工大学"}]
        before_delete = self.save(payload).json()
        response = self.owner.delete(f"/api/v1/resumes/{resume_id}")
        self.assertEqual(response.status_code, 204, response.text)
        deleted = self.profile()
        self.assertEqual(deleted["candidates"], [])
        self.assertEqual(deleted["skills"][0]["name"], "Python")
        self.assertEqual(deleted["skills"][0]["source"]["kind"], "resume_deleted")
        self.assertIsNone(deleted["skills"][0]["source"]["resume_id"])
        self.assertFalse(deleted["skills"][0]["source"]["evidence"])
        self.assertEqual(deleted["education"][0]["school"], "合成手工大学")
        self.assertEqual(deleted["education"][0]["source"]["kind"], "manual")
        self.assertGreater(deleted["revision"], before_delete["revision"])
        self.save(editable(before_delete), expected=409)
        self.assertNotIn("Synthetic resume: Python", self.owner.get("/api/v1/profile").text)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(ResumeDocument, resume_id))
            self.assertEqual(session.scalars(select(ProfileCandidate)).all(), [])

    def test_delete_intent_clears_profile_sources_even_if_physical_cleanup_fails(self) -> None:
        resume_id = self.upload()
        initial = self.ingest(resume_id=resume_id)
        confirmed = self.review(initial["candidates"][0]["id"], initial["revision"], "confirm").json()
        with Session(self.engine) as session:
            object_key = session.get(ResumeDocument, resume_id).object_key
        file_path = self.settings.upload_dir / object_key
        original = file_path.read_bytes()
        file_path.unlink()
        file_path.mkdir()
        (file_path / "synthetic-lock").write_text("synthetic blocking entry", encoding="utf-8")
        try:
            response = self.owner.delete(f"/api/v1/resumes/{resume_id}")
            self.assertEqual(response.status_code, 503)
            after_intent = self.profile()
            self.assertEqual(after_intent["candidates"], [])
            self.assertEqual(after_intent["skills"][0]["source"]["kind"], "resume_deleted")
            self.assertFalse(after_intent["skills"][0]["source"]["evidence"])
            self.assertGreater(after_intent["revision"], confirmed["revision"])
        finally:
            (file_path / "synthetic-lock").unlink()
            file_path.rmdir()
            file_path.write_bytes(original)
        self.assertEqual(self.owner.delete(f"/api/v1/resumes/{resume_id}").status_code, 204)

    def test_editing_confirmed_field_marks_user_modified_and_keeps_unedited_sources(self) -> None:
        initial = self.ingest()
        confirmed = self.review(initial["candidates"][0]["id"], initial["revision"], "confirm").json()
        unchanged = self.save(editable(confirmed)).json()
        self.assertEqual(unchanged["skills"][0]["source"]["kind"], "resume_confirmed")
        modified = editable(unchanged)
        modified["skills"][0]["name"] = "User supplied synthetic C++"
        changed = self.save(modified).json()
        self.assertEqual(changed["skills"][0]["source"]["kind"], "user_modified")
        self.assertTrue(changed["skills"][0]["source"]["evidence"])
        self.assertTrue(all(not item["supports_current_value"]
                            for item in changed["skills"][0]["source"]["evidence"]))

    def test_invalid_dates_duplicate_ids_empty_entries_and_extra_values_leave_database_unchanged(self) -> None:
        initial = self.profile()
        invalid = [
            {"education": [{"school": "Synthetic", "end_date": "2027-13"}]},
            {"projects": [{"name": "Synthetic", "start_date": "2026-09", "end_date": "2025-01"}]},
            {"projects": [{"name": "Synthetic", "end_date": "2026-10", "is_current": True}]},
            {"education": [{}]}, {"skills": [{"name": "   "}]},
            {"skills": [{"name": "Synthetic", "confidence": 1.0}]},
            {"experiences": [{"organization": "Synthetic", "experience_type": "fabricated"}]},
            {"revision": 10 ** 100}, {"revision": -1}, {"revision": True}, {"revision": "0"},
        ]
        for fields in invalid:
            with self.subTest(group=list(fields)):
                self.save({**editable(initial), **fields}, expected=422)
        self.assertEqual(self.profile(), initial)
        valid = editable(initial)
        valid["skills"] = [{"name": "Synthetic"}]
        saved = self.save(valid).json()
        duplicate = editable(saved)
        duplicate["skills"].append(copy.deepcopy(duplicate["skills"][0]))
        self.save(duplicate, expected=422)
        self.assertEqual(self.profile(), saved)

    def test_validation_and_conflict_responses_do_not_echo_private_content_or_internal_paths(self) -> None:
        marker = "PRIVATE_SYNTHETIC_FACT_SHOULD_NOT_LEAK"
        payload = editable(self.profile())
        response = self.save({**payload, "skills": [{"name": marker, "forged_secret": marker}]}, expected=422)
        self.assertNotIn(marker, response.text)
        payload["skills"] = [{"name": marker}]
        self.save(payload)
        conflict = self.save(payload, expected=409)
        for response in (response, conflict):
            self.assertNotIn(marker, response.text)
            self.assertNotIn(str(self.root), response.text)
            self.assertNotIn("Traceback", response.text)
            self.assertNotIn("INSERT INTO", response.text)


if __name__ == "__main__":
    unittest.main()
