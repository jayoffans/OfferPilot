"""Real API/session/SQLite checks for privacy, revisions and repeat calculations."""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import test_job_catalog as harness
import test_trusted_profile as profile_harness
from offerpilot_api.db.models import CareerProfile, CareerProfileEntry, JobMatch, JobMatchReceipt, ProfileCandidate, ResumeDocument
from offerpilot_api.job_intelligence import matching
from offerpilot_api.job_intelligence.match_api_schemas import JobMatchCreateRequest
from offerpilot_api.job_intelligence.match_services import create_match
from offerpilot_api.profile.schemas import ProfileSaveRequest
from offerpilot_api.profile.services import ingest_profile_candidates, remove_resume_profile_evidence, save_profile


class JobMatchTests(unittest.TestCase):
    setUp = harness.JobCatalogTests.setUp
    client = harness.JobCatalogTests.client
    account = harness.JobCatalogTests.account
    login = harness.JobCatalogTests.login
    payload = harness.JobCatalogTests.payload
    create = harness.JobCatalogTests.create
    save = harness.JobCatalogTests.save

    def profile(self, skills=None, education=None):
        revision = self.owner.get("/api/v1/profile").json()["revision"]
        response = self.owner.put("/api/v1/profile", json={"revision": revision,
            "education": education or [], "skills": skills if skills is not None else [{"name": "Python 3", "level": "熟练掌握"}],
            "projects": [], "experiences": []})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def job(self, **changes):
        values = {"jd_text": "必须掌握 Python", "requirements": [{
            "type": "technical_skill", "content": "必须掌握 Python", "priority": "must",
            "review_status": "confirmed", "evidence_text": "必须掌握 Python"}]}
        return self.create(self.payload(**{**values, **changes}))

    def calculate(self, job, profile, **changes):
        payload = {"job_revision": job["revision"], "profile_revision": profile["revision"],
                   "idempotency_key": uuid4().hex, **changes}
        return self.owner.post(f"/api/v1/jobs/{job['id']}/matches", json=payload)

    def test_real_confirmed_match_has_both_sides_evidence_and_no_score(self):
        profile, job = self.profile(), self.job()
        response = self.calculate(job, profile)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertFalse(result["stale"])
        self.assertEqual(result["evaluation"]["items"][0]["status"], "met")
        evidence = result["evaluation"]["items"][0]
        self.assertEqual(evidence["profile_evidence"][0]["entry_id"], profile["skills"][0]["id"])
        self.assertEqual(evidence["jd_evidence"]["evidence_text"], "必须掌握 Python")
        self.assertEqual(evidence["jd_evidence"]["source_start"], 0)
        self.assertNotIn("score", str(result.keys()))
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_no_result_is_null_and_empty_history_is_useful(self):
        job = self.job()
        self.assertIsNone(self.owner.get(f"/api/v1/jobs/{job['id']}/matches/latest").json())
        self.assertEqual(self.owner.get(f"/api/v1/jobs/{job['id']}/matches").json(), {"items": [], "total": 0})

    def test_same_key_and_new_key_same_basis_share_one_result(self):
        profile, job = self.profile(), self.job()
        first = self.calculate(job, profile, idempotency_key="repeat").json()
        second = self.calculate(job, profile, idempotency_key="repeat").json()
        third = self.calculate(job, profile).json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["id"], third["id"])
        self.assertEqual(first["created_at"], third["created_at"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(JobMatch)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(JobMatchReceipt)), 2)

    def test_key_cannot_be_reused_for_new_basis_or_different_job(self):
        profile, job = self.profile(), self.job()
        self.assertEqual(self.calculate(job, profile, idempotency_key="bound").status_code, 200)
        changed = self.profile(skills=[{"name": "Java"}])
        conflict = self.calculate(job, changed, idempotency_key="bound")
        self.assertEqual(conflict.status_code, 409)
        other = self.job(title="另一个合成岗位")
        self.assertEqual(self.calculate(other, changed, idempotency_key="bound").status_code, 409)

    def test_profile_update_invalidates_then_recompute_and_old_replay_cannot_replace_latest(self):
        profile, job = self.profile(), self.job()
        original = self.calculate(job, profile, idempotency_key="old-basis").json()
        changed = self.profile(skills=[{"name": "JavaScript"}])
        url = f"/api/v1/jobs/{job['id']}/matches"
        stale = self.owner.get(url + "/latest").json()
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["stale_reasons"], ["profile_changed"])
        self.assertEqual(self.calculate(job, profile).status_code, 409)
        new = self.calculate(job, changed).json()
        self.assertNotEqual(original["id"], new["id"])
        self.assertEqual(new["evaluation"]["items"][0]["status"], "unknown")
        replay = self.calculate(job, profile, idempotency_key="old-basis").json()
        self.assertEqual(replay["id"], original["id"])
        self.assertTrue(replay["stale"])
        self.assertEqual(self.owner.get(url + "/latest").json()["id"], new["id"])
        self.assertEqual(self.owner.get(url + "?limit=1").json()["total"], 2)
        self.assertEqual(len(self.owner.get(url + "?limit=1").json()["items"]), 1)

    def test_jd_and_review_edit_invalidates_and_old_revision_fails(self):
        profile, job = self.profile(), self.job()
        original = self.calculate(job, profile).json()
        changed = self.save(job, {"jd_text": "必须掌握 JavaScript"}).json()
        stale = self.owner.get(f"/api/v1/jobs/{job['id']}/matches/{original['id']}").json()
        self.assertIn("job_changed", stale["stale_reasons"])
        self.assertEqual(self.calculate(job, profile).status_code, 409)
        self.assertEqual(self.calculate(changed, profile).json()["detail"]["code"], "reviewed_requirements_required")

    def test_rule_version_change_is_stale(self):
        profile, job = self.profile(), self.job()
        self.calculate(job, profile)
        with patch.object(matching, "RULE_VERSION", "match-test-next"):
            self.assertIn("rule_changed", self.owner.get(f"/api/v1/jobs/{job['id']}/matches/latest").json()["stale_reasons"])

    def test_missing_profile_and_only_pending_jd_are_actionable_conflicts(self):
        job = self.job()
        result = self.calculate(job, {"revision": 0})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["detail"]["code"], "profile_not_ready")
        profile = self.profile()
        pending = self.create(self.payload(title="未审核岗位", jd_text="必须掌握 Java"))
        result = self.calculate(pending, profile)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["detail"]["code"], "reviewed_requirements_required")

    def test_user_isolation_all_routes_and_unknown_resources_have_same_404(self):
        profile, job = self.profile(), self.job()
        result = self.calculate(job, profile).json()
        urls = [f"/api/v1/jobs/{job['id']}/matches", f"/api/v1/jobs/{job['id']}/matches/latest",
                f"/api/v1/jobs/{job['id']}/matches/{result['id']}"]
        for url in urls:
            self.assertEqual(self.other.get(url).status_code, 404)
            self.assertEqual(self.anonymous.get(url).status_code, 401)
        payload = {"job_revision": job["revision"], "profile_revision": profile["revision"], "idempotency_key": "isolation"}
        self.assertEqual(self.other.post(urls[0], json=payload).status_code, 404)
        own = self.job(title="第二个本人职位")
        self.assertEqual(self.owner.get(f"/api/v1/jobs/{own['id']}/matches/{result['id']}").status_code, 404)
        self.assertEqual(self.owner.get(f"/api/v1/jobs/{uuid4()}/matches").status_code, 404)

    def test_csrf_invalid_ids_bounds_and_forged_profile_owner_are_rejected_without_echo(self):
        profile, job = self.profile(), self.job()
        url = f"/api/v1/jobs/{job['id']}/matches"
        payload = {"job_revision": job["revision"], "profile_revision": profile["revision"], "idempotency_key": "invalids"}
        for changes in ({"profile": {"skills": ["SECRET-FAKE-SKILL"]}}, {"user_id": str(self.other_id)},
                        {"profile_revision": True}, {"job_revision": 0}, {"job_revision": 2**100},
                        {"idempotency_key": "../secret"}):
            result = self.owner.post(url, json={**payload, **changes})
            self.assertEqual(result.status_code, 422, result.text)
            self.assertNotIn("SECRET-FAKE-SKILL", result.text)
        client = self.client()
        client.cookies.update(self.owner.cookies)
        self.assertEqual(client.post(url, json=payload).status_code, 403)
        self.assertEqual(self.owner.get("/api/v1/jobs/not-a-uuid/matches/latest").status_code, 422)
        self.assertEqual(self.owner.get(url + "/not-a-uuid").status_code, 422)
        self.assertEqual(self.owner.get(url + "?limit=500").status_code, 422)

    def test_unconfirmed_ai_candidate_is_never_a_fact(self):
        profile = self.profile(skills=[{"name": "JavaScript"}])
        with Session(self.engine) as db:
            current = db.scalar(select(CareerProfile).where(CareerProfile.user_id == self.owner_id))
            resume = ResumeDocument(user_id=self.owner_id, file_name="合成.pdf", file_path="", file_type="application/pdf", raw_text="Python")
            db.add(resume)
            db.flush()
            db.add(ProfileCandidate(profile_id=current.id, resume_id=resume.id, batch_key="untrusted", item_index=0,
                batch_digest="a" * 64, category="skill", value={"name": "Python"}, evidence=[{"field": "name", "source_text": "Python"}],
                evidence_status="supported", status="pending", schema_version="1.0", extraction_version="synthetic"))
            db.commit()
        result = self.calculate(self.job(), profile).json()
        self.assertEqual(result["evaluation"]["items"][0]["status"], "unknown")
        self.assertEqual(result["evaluation"]["items"][0]["profile_evidence"], [])

    def test_unreviewed_conditions_are_explicitly_excluded(self):
        profile = self.profile()
        job = self.job(requirements=[{"type": "technical_skill", "content": "Python", "priority": "must", "review_status": "confirmed"},
                                    {"type": "education", "content": "博士毕业", "priority": "must", "review_status": "pending"}])
        value = self.calculate(job, profile).json()["evaluation"]
        self.assertEqual(len(value["items"]), 1)
        self.assertEqual(len(value["excluded_requirements"]), 1)
        self.assertNotEqual(value["conclusion"], "hard_conflict")

    def test_preferences_save_preserves_facts_uses_same_revision_and_stales_matches(self):
        profile, job = self.profile(), self.job()
        self.calculate(job, profile)
        prefs = {"locations": ["上海"], "work_types": ["internship"], "salary": {"min": 200, "max": 300, "period": "day", "tax": "pre_tax"}}
        response = self.owner.put("/api/v1/profile/preferences", json={"revision": profile["revision"], "preferences": prefs})
        self.assertEqual(response.status_code, 200, response.text)
        changed = response.json()
        self.assertEqual(changed["skills"], profile["skills"])
        self.assertEqual(changed["revision"], profile["revision"] + 1)
        self.assertEqual(changed["preferences"]["locations"], ["上海"])
        self.assertEqual(self.owner.put("/api/v1/profile/preferences", json={"revision": profile["revision"], "preferences": {}}).status_code, 409)
        self.assertTrue(self.owner.get(f"/api/v1/jobs/{job['id']}/matches/latest").json()["stale"])
        result = self.calculate(job, changed)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(any(item["scope"] == "preference" for item in result.json()["evaluation"]["items"]))

    def test_preferences_validation_and_mutation_csrf(self):
        response = self.owner.get("/api/v1/profile").json()
        self.assertEqual(response["preferences"]["locations"], [])
        for prefs in ({"locations": ["  "]}, {"work_types": ["internship", "internship"]}, {"weekly_days": 8},
                      {"internship_months": 0}, {"salary": {"min": 400, "max": 100, "period": "day"}},
                      {"salary": {"min": 100, "period": "hour"}}, {"user_id": str(self.other_id)}):
            self.assertEqual(self.owner.put("/api/v1/profile/preferences", json={"revision": 0, "preferences": prefs}).status_code, 422)
        client = self.client()
        client.cookies.update(self.owner.cookies)
        self.assertEqual(client.put("/api/v1/profile/preferences", json={"revision": 0, "preferences": {}}).status_code, 403)

    def concurrent(self, same_key):
        profile, job = self.profile(), self.job()
        barrier = threading.Barrier(2)
        def worker(index):
            with Session(self.engine) as db:
                barrier.wait(timeout=5)
                return create_match(db, user_id=self.owner_id, job_id=UUID(job["id"]), payload=JobMatchCreateRequest(
                    profile_revision=profile["revision"], job_revision=job["revision"], idempotency_key="concurrent" if same_key else f"concurrent-{index}"))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(worker, range(2)))
        self.assertEqual(results[0].id, results[1].id)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(JobMatch)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(JobMatchReceipt)), 1 if same_key else 2)

    def test_parallel_same_key_returns_one_committed_match(self):
        self.concurrent(True)

    def test_parallel_distinct_keys_share_one_basis(self):
        self.concurrent(False)

    def test_concurrent_profile_edit_serializes_after_evaluation_and_stales_old_basis(self):
        profile, job = self.profile(), self.job()
        entered, release, edit_started = threading.Event(), threading.Event(), threading.Event()
        original = matching.evaluate_match
        def evaluate(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Synthetic gate timeout")
            return original(*args)
        def calculate():
            with Session(self.engine) as db:
                return create_match(db, user_id=self.owner_id, job_id=UUID(job["id"]), payload=JobMatchCreateRequest(
                    profile_revision=profile["revision"], job_revision=job["revision"], idempotency_key="fenced"))
        def edit():
            edit_started.set()
            with Session(self.engine) as db:
                return save_profile(db, user_id=self.owner_id, payload=ProfileSaveRequest(revision=profile["revision"],
                    education=[], skills=[{"name": "Java"}], projects=[], experiences=[]))
        with patch.object(matching, "evaluate_match", side_effect=evaluate), ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(calculate)
            self.assertTrue(entered.wait(5))
            changed = executor.submit(edit)
            self.assertTrue(edit_started.wait(5))
            release.set()
            result, _ = future.result(10), changed.result(10)
        latest = self.owner.get(f"/api/v1/jobs/{job['id']}/matches/latest").json()
        self.assertEqual(latest["id"], str(result.id))
        self.assertTrue(latest["stale"])

    def test_original_source_is_erased_from_history_even_after_entry_removed(self):
        profile, job = self.profile(), self.job()
        with Session(self.engine) as db:
            resume = ResumeDocument(user_id=self.owner_id, file_name="合成.pdf", file_path="", file_type="application/pdf", raw_text="熟悉 Python 3")
            db.add(resume)
            db.flush()
            entry = db.get(CareerProfileEntry, UUID(profile["skills"][0]["id"]))
            entry.resume_id, entry.source_kind = resume.id, "resume_confirmed"
            entry.field_sources = {"name": "resume"}
            entry.evidence = [{"field": "name", "source_text": "熟悉 Python 3", "supports_current_value": True}]
            db.commit()
            resume_id = resume.id
        result = self.calculate(job, profile).json()
        self.assertEqual(result["evaluation"]["items"][0]["profile_evidence"][0]["source_text"], "熟悉 Python 3")
        self.profile(skills=[{"name": "Java"}])
        with Session(self.engine) as db:
            remove_resume_profile_evidence(db, user_id=self.owner_id, resume_id=resume_id)
            db.commit()
            stored = db.get(JobMatch, UUID(result["id"]))
            self.assertNotIn("熟悉 Python 3", str(stored.evaluation))
        history = self.owner.get(f"/api/v1/jobs/{job['id']}/matches/{result['id']}").json()
        evidence = history["evaluation"]["items"][0]["profile_evidence"][0]
        self.assertIsNone(evidence["source_text"])
        self.assertIsNone(evidence["resume_id"])
        self.assertEqual(evidence["source_kind"], "resume_deleted")

    def test_resume_deletion_preserves_user_corrected_field_provenance_in_real_match_history(self):
        upload = self.owner.post("/api/v1/resumes", files={
            "file": ("synthetic.pdf", profile_harness.synthetic_pdf("Synthetic resume: Python"), "application/pdf")})
        self.assertEqual(upload.status_code, 201, upload.text)
        resume_id = UUID(upload.json()["id"])
        with Session(self.engine) as db:
            proposed = ingest_profile_candidates(db, user_id=self.owner_id, resume_id=resume_id,
                batch_key="mixed-field-provenance", payload={"schema_version": "1.0", "extraction_version": "synthetic",
                    "candidates": [{"category": "skill", "value": {"name": "Python"},
                                    "evidence": [{"field": "name", "source_text": "Python"}]}]})
        candidate = proposed.candidates[0]
        confirmed = self.owner.post(f"/api/v1/profile/candidates/{candidate.id}/review",
            json={"revision": proposed.revision, "action": "confirm"})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        formal = confirmed.json()
        skill = formal["skills"][0]
        corrected = self.owner.put("/api/v1/profile", json={"revision": formal["revision"],
            "education": [], "skills": [{"id": skill["id"], "name": "Python", "level": "熟悉"}],
            "projects": [], "experiences": []})
        self.assertEqual(corrected.status_code, 200, corrected.text)
        formal = corrected.json()
        self.assertEqual(formal["skills"][0]["source"]["field_sources"], {"name": "resume", "level": "manual"})
        job = self.job()
        calculation = self.calculate(job, formal)
        self.assertEqual(calculation.status_code, 200, calculation.text)
        result = calculation.json()
        before = {fact["field"]: fact for fact in result["evaluation"]["items"][0]["profile_evidence"]}
        self.assertEqual(before["name"]["source_kind"], "resume")
        self.assertEqual(before["name"]["source_text"], "Python")
        self.assertEqual(before["level"]["source_kind"], "manual")
        self.assertEqual(before["level"]["resume_id"], str(resume_id))
        deletion = self.owner.delete(f"/api/v1/resumes/{resume_id}")
        self.assertEqual(deletion.status_code, 204, deletion.text)
        current = self.owner.get("/api/v1/profile").json()
        self.assertEqual(current["skills"][0]["source"]["field_sources"], {"name": "resume_deleted", "level": "manual"})
        self.assertEqual(current["skills"][0]["level"], "熟悉")
        history = self.owner.get(f"/api/v1/jobs/{job['id']}/matches/{result['id']}").json()
        after = {fact["field"]: fact for fact in history["evaluation"]["items"][0]["profile_evidence"]}
        self.assertEqual(after["name"]["source_kind"], "resume_deleted")
        self.assertEqual(after["level"]["source_kind"], "manual")
        self.assertTrue(history["stale"])
        self.assertTrue(all(fact["resume_id"] is None and fact["source_text"] is None for fact in after.values()))
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ResumeDocument, resume_id))
            stored = db.get(JobMatch, UUID(result["id"]))
            self.assertTrue(all(fact.get("source_text") is None and fact.get("resume_id") is None
                for item in stored.evaluation["items"] for fact in item["profile_evidence"]))

    def test_delete_job_cascades_match_history_and_request_receipts(self):
        profile, job = self.profile(), self.job()
        self.calculate(job, profile)
        response = self.owner.request("DELETE", f"/api/v1/jobs/{job['id']}", json={"revision": job["revision"]})
        self.assertEqual(response.status_code, 204, response.text)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(JobMatch)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(JobMatchReceipt)), 0)

    def test_invalid_rule_schema_fails_safely_without_saving_or_echo(self):
        profile, job = self.profile(), self.job()
        with patch.object(matching, "evaluate_match", return_value={"private": "SECRET-FIELD"}):
            response = self.calculate(job, profile)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("SECRET-FIELD", response.text)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(JobMatch)), 0)


if __name__ == "__main__":
    unittest.main()
