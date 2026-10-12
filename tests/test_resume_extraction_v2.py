"""Synthetic V2 contracts, attribution fences and existing-profile integration."""

import copy
import json
import tempfile
import unittest
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from offerpilot_api.agent_runtime.providers.mock import MockResumeProvider
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import CareerProfile, CareerProfileEntry, ProfileCandidate, ResumeDocument, ResumeExtractionRun, User
from offerpilot_api.db.session import build_engine
from offerpilot_api.profile.schemas import ProfileCandidateReviewRequest
from offerpilot_api.profile.services import ProfileError, get_profile, ingest_profile_candidates, review_profile_candidate
from offerpilot_api.resume_ingestion.evidence import build_text_layout, normalize_month
from offerpilot_api.resume_ingestion.extraction_schema import StudentProfileExtractionV2
from offerpilot_api.resume_ingestion.extractor import ExtractionDiagnostics, ExtractionError
from offerpilot_api.resume_ingestion.extractor_v2 import extract_student_profile_v2, map_extraction_v2
from offerpilot_api.resume_ingestion.synthetic_fixtures import SYNTHETIC_RESUME_TEXT, default_output, empty_output, fixture_cases, fixture_response, leaf


class FixtureProvider:
    def __init__(self, response):
        self.response = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        self.calls = 0

    def generate_json(self, *, system_prompt, user_prompt):
        self.calls += 1
        self.system_prompt, self.user_prompt = system_prompt, user_prompt
        return self.response


class ResumeExtractionV2Tests(unittest.TestCase):
    def extract(self, payload, text=SYNTHETIC_RESUME_TEXT, **kwargs):
        return extract_student_profile_v2(text, FixtureProvider(payload), **kwargs)

    def test_normal_chinese_maps_all_groups_without_confirming(self):
        result = extract_student_profile_v2(SYNTHETIC_RESUME_TEXT, MockResumeProvider())
        self.assertEqual([item.category for item in result.batch.candidates], ["education", "skill", "skill", "skill", "skill", "project", "experience"])
        self.assertEqual(result.batch.candidates[0].value.end_date, "2026-06")
        self.assertTrue(all(item["test_result"] for item in result.candidate_metadata))
        self.assertTrue(all(item["confidence_interpretation"] == "model_reported_not_truth_probability" for item in result.candidate_metadata))

    def test_mock_does_not_accept_unknown_resume_or_call_network(self):
        diagnostic = ExtractionDiagnostics()
        with patch("offerpilot_api.agent_runtime.providers.deepseek.OpenAI") as sdk:
            with self.assertRaises(ExtractionError):
                extract_student_profile_v2("unapproved synthetic text", MockResumeProvider(), diagnostics=diagnostic)
            sdk.assert_not_called()
        self.assertEqual(diagnostic.failure_reason, "mock_fixture_not_found")

    def test_missing_required_fields_invalid_json_and_strict_types_have_bounded_retry(self):
        for case in fixture_cases()[-3:]:
            with self.subTest(case=case["id"]):
                provider, diagnostic = FixtureProvider(fixture_response(case)), ExtractionDiagnostics()
                with self.assertRaises(ExtractionError):
                    extract_student_profile_v2(case["text"], provider, diagnostics=diagnostic)
                self.assertEqual(provider.calls, 2)
                self.assertEqual(diagnostic.failure_reason, "schema_validation_failed")

    def test_extra_model_locations_are_forbidden(self):
        payload = default_output()
        payload["skills"][0]["name"]["page"] = 99
        with self.assertRaises(ExtractionError):
            self.extract(payload)

    def test_invalid_unicode_leaf_fails_schema_without_echoing_input(self):
        payload = default_output()
        payload["skills"][0]["name"]["value"] = "\ud800"
        diagnostics = ExtractionDiagnostics()
        provider = FixtureProvider(json.dumps(payload, ensure_ascii=True))
        with self.assertRaises(ExtractionError) as error:
            extract_student_profile_v2(SYNTHETIC_RESUME_TEXT, provider, diagnostics=diagnostics)
        self.assertEqual(diagnostics.failure_reason, "schema_validation_failed")
        self.assertNotIn("\ud800", str(error.exception))
        self.assertEqual(provider.calls, 2)

    def test_model_confidence_cannot_override_missing_evidence(self):
        case = fixture_cases()[5]
        result = self.extract(case["response"], case["text"])
        metadata = result.candidate_metadata[1]
        self.assertEqual(metadata["evidence_status"], "unsupported")
        self.assertEqual(metadata["fields"][0]["reported_confidence"], 0.99)
        self.assertEqual(metadata["fields"][0]["locations"], [])

    def test_negation_and_learning_plan_remain_review_only_even_if_model_affirms(self):
        payload = default_output()
        payload["skills"][2]["name"]["assertion"] = "affirmed"
        payload["skills"][3]["name"]["assertion"] = "affirmed"
        result = self.extract(payload)
        self.assertEqual([result.candidate_metadata[index]["evidence_status"] for index in (3, 4)], ["needs_review", "needs_review"])

    def test_shortened_negation_quote_unicode_and_long_context_are_conservative(self):
        for text in ("Not proficient in Java", "n\u200bot proficient in Java", "未" + "合成说明" * 180 + "Java" + "合成说明" * 180, "Java。" * 21):
            with self.subTest(text_length=len(text)):
                payload = empty_output()
                payload["skills"] = [{"name": leaf("Java"), "level": None}]
                metadata = self.extract(payload, text).candidate_metadata[0]
                self.assertEqual(metadata["evidence_status"], "needs_review")

    def test_java_javascript_alias_boundaries(self):
        case = fixture_cases()[3]
        result = self.extract(case["response"], case["text"])
        self.assertEqual(result.candidate_metadata[0]["evidence_status"], "unsupported")
        self.assertEqual(result.batch.candidates[1].value.name, "JavaScript")
        self.assertEqual(result.candidate_metadata[1]["evidence_status"], "supported")
        for source in ("JavaScript", "JavaＳｃｒｉｐｔ", "Java\u200bScript", "Java\u0301Script"):
            payload = empty_output()
            payload["skills"] = [{"name": leaf("Java", "Java"), "level": None}]
            self.assertEqual(self.extract(payload, source).candidate_metadata[0]["evidence_status"], "unsupported")

    def test_multiple_education_projects_and_chinese_date_formats(self):
        case = fixture_cases()[2]
        result = self.extract(case["response"], case["text"])
        education = [item for item in result.batch.candidates if item.category == "education"]
        project = [item for item in result.batch.candidates if item.category == "project"]
        self.assertEqual(education[1].value.start_date, "2026-09")
        self.assertEqual(education[1].value.end_date, "2029-06")
        self.assertEqual(project[1].value.end_date, "2024-12")
        self.assertEqual(normalize_month("2024年12月"), "2024-12")
        self.assertIsNone(normalize_month("2024"))

    def test_missing_graduation_does_not_infer_from_attendance_end(self):
        case = fixture_cases()[1]
        payload = copy.deepcopy(case["response"])
        payload["education"][0]["end_date"] = leaf("2022年9月")
        result = self.extract(payload, case["text"])
        self.assertIsNone(result.batch.candidates[0].value.end_date)
        self.assertTrue(any(trace["path"].endswith(".end_date") and trace["mapped_field"] is None for trace in result.candidate_metadata[0]["fields"]))

    def test_cross_project_evidence_is_not_transferable(self):
        case = fixture_cases()[4]
        result = self.extract(case["response"], case["text"])
        project = next(metadata for metadata in result.candidate_metadata if metadata["item_path"] == "projects[0]")
        self.assertEqual(project["evidence_status"], "unsupported")
        self.assertIn("evidence_outside_entry_scope", project["review_reasons"])

    def test_overlapping_scope_requires_review(self):
        case = fixture_cases()[2]
        payload = copy.deepcopy(case["response"])
        first, last = case["text"].index(payload["projects"][0]["scope_text"]), case["text"].index(payload["projects"][1]["scope_text"])
        payload["projects"][0]["scope_text"] = case["text"][first:last + len(payload["projects"][1]["scope_text"])]
        result = self.extract(payload, case["text"])
        for metadata in result.candidate_metadata:
            if metadata["category"] == "project":
                self.assertNotEqual(metadata["evidence_status"], "supported")

    def test_unique_scopes_disambiguate_shared_degrees_dates_and_stacks(self):
        case = fixture_cases()[2]
        payload, text = copy.deepcopy(case["response"]), case["text"]
        payload["education"][1]["degree"] = leaf("本科")
        payload["education"][1]["scope_text"] = payload["education"][1]["scope_text"].replace("硕士", "本科")
        payload["education"][1]["graduation_status"] = leaf("预计毕业")
        text = text.replace("合成学院；硕士", "合成学院；本科")
        payload["projects"][1]["technology_stack"] = [leaf("Python", "技术栈：Python")]
        payload["projects"][1]["scope_text"] = payload["projects"][1]["scope_text"].replace("技术栈：JS", "技术栈：Python")
        text = text.replace("技术栈：JS", "技术栈：Python")
        result = self.extract(payload, text)
        self.assertTrue(all(item["evidence_status"] == "supported" for item in result.candidate_metadata if item["category"] in ("education", "project")))
        for item in result.candidate_metadata:
            if item["category"] in ("education", "project"):
                self.assertTrue(all(len(trace["locations"]) == 1 for trace in item["fields"]))

    def test_prompt_injection_stays_data_and_blocks_confirmation_eligibility(self):
        case = fixture_cases()[6]
        provider = FixtureProvider(case["response"])
        result = extract_student_profile_v2(case["text"], provider)
        self.assertEqual(result.candidate_metadata[0]["evidence_status"], "needs_review")
        self.assertIn("instruction_like_source", result.candidate_metadata[0]["review_reasons"])
        self.assertNotIn(case["text"], provider.system_prompt)

    def test_positive_negative_skill_conflicts_block_both_proposals(self):
        payload = empty_output()
        payload["skills"] = [{"name": leaf("Python", "技能：Python。"), "level": None}, {"name": leaf("Python", "尚未掌握 Python。", "negated"), "level": None}]
        result = self.extract(payload, "技能：Python。\n尚未掌握 Python。")
        self.assertEqual([item["evidence_status"] for item in result.candidate_metadata], ["needs_review", "needs_review"])
        self.assertTrue(all("conflicting_proposals" in item["review_reasons"] for item in result.candidate_metadata))

    def test_duplicate_model_items_do_not_create_duplicate_candidates(self):
        case = fixture_cases()[7]
        result = self.extract(case["response"], case["text"])
        self.assertEqual(len(result.batch.candidates), 7)
        self.assertEqual(result.candidate_metadata[1]["duplicate_proposals_removed"], 1)

    def test_duplicate_scoped_items_ignore_confidence_and_keep_one_supported_candidate(self):
        payload = default_output()
        duplicate = copy.deepcopy(payload["education"][0])
        duplicate["school"]["confidence"] = 0.4
        payload["education"].append(duplicate)
        result = self.extract(payload)
        self.assertEqual(len(result.batch.candidates), 7)
        self.assertEqual(result.candidate_metadata[0]["evidence_status"], "supported")
        self.assertEqual(result.candidate_metadata[0]["duplicate_proposals_removed"], 1)

    def test_real_server_page_and_paragraph_locations_are_returned(self):
        split = SYNTHETIC_RESUME_TEXT.index("项目：")
        layout = build_text_layout(SYNTHETIC_RESUME_TEXT, [{"page": 1, "start": 0, "end": split}, {"page": 2, "start": split, "end": len(SYNTHETIC_RESUME_TEXT)}])
        result = self.extract(default_output(), text_layout=layout)
        for metadata in result.candidate_metadata:
            for trace in metadata["fields"]:
                for location in trace["locations"]:
                    self.assertEqual(SYNTHETIC_RESUME_TEXT[location["start"]:location["end"]], trace["source_text"])
        self.assertEqual(result.candidate_metadata[5]["scope_locations"][0]["page"], 2)
        legacy = self.extract(default_output())
        self.assertTrue(all(loc["page"] is None for item in legacy.candidate_metadata for trace in item["fields"] for loc in trace["locations"]))

    def test_unmappable_leaf_fails_instead_of_silently_discarding(self):
        payload = empty_output()
        payload["education"] = [{"scope_text": "预计毕业", **{name: None for name in ("school", "degree", "major", "start_date", "end_date", "graduation_date")}, "graduation_status": leaf("预计毕业")}]
        with self.assertRaises(ExtractionError):
            self.extract(payload, "预计毕业")

    def test_constructed_invalid_models_do_not_emit_private_serializer_warning(self):
        model = StudentProfileExtractionV2.model_construct(schema_version="2.0", education=[], skills=["PRIVATE_MARKER"], projects=[], experiences=[])
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            with self.assertRaises(ValidationError):
                map_extraction_v2(model, "PRIVATE_MARKER")
        self.assertEqual(captured, [])

    def test_evaluation_has_independent_gold_and_exposes_injected_errors(self):
        import importlib.util
        import sys
        evaluation_dir = Path(__file__).resolve().parents[1] / "backend" / "evaluations"
        specification = importlib.util.spec_from_file_location("synthetic_v2_evaluation", evaluation_dir / "evaluate_resume_extraction_v2.py")
        module = importlib.util.module_from_spec(specification)
        sys.path.insert(0, str(evaluation_dir))
        try:
            specification.loader.exec_module(module)
            report = module.evaluate()
        finally:
            sys.path.remove(str(evaluation_dir))
        self.assertEqual(report["guard_failures"], 0)
        self.assertEqual(report["nonnull_expected_wrong"], 1)
        self.assertEqual(report["nonnull_expected_missing"], 1)
        self.assertEqual(report["healthy_case_supported_but_blocked"], 0)
        self.assertEqual(report["adversarial_supported_truth_but_faulty_output_blocked"], 1)
        self.assertEqual(report["paid_calls"], 0)


class ExtractionV2PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="offerpilot-v2-core-")
        self.engine = build_engine(f"sqlite:///{(Path(self.temp.name) / 'test.db').as_posix()}")
        Base.metadata.create_all(self.engine)
        self.user_id, self.resume_id = uuid4(), uuid4()
        with Session(self.engine) as db:
            db.add(User(id=self.user_id, email_normalized="synthetic-v2@example.com", password_hash="synthetic-only"))
            db.flush()
            db.add(ResumeDocument(id=self.resume_id, user_id=self.user_id, file_name="synthetic.pdf", file_path="", file_type="application/pdf", raw_text=SYNTHETIC_RESUME_TEXT, text_layout=build_text_layout(SYNTHETIC_RESUME_TEXT)))
            db.commit()
        self.result = extract_student_profile_v2(SYNTHETIC_RESUME_TEXT, MockResumeProvider())

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def ingest(self, db, **kwargs):
        return ingest_profile_candidates(db, user_id=self.user_id, resume_id=self.resume_id, batch_key="synthetic-core-v2", payload=self.result.batch, candidate_metadata=self.result.candidate_metadata, **kwargs)

    def test_outer_rollback_keeps_success_and_candidates_atomic(self):
        with Session(self.engine) as db:
            self.ingest(db, commit=False)
            self.assertEqual(db.scalar(select(func.count()).select_from(ProfileCandidate)), 7)
            db.rollback()
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ProfileCandidate)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(CareerProfile)), 0)

    def test_pending_confirm_modify_reject_reuses_only_existing_formal_profile(self):
        with Session(self.engine) as db:
            profile = self.ingest(db)
            self.assertEqual(profile.education, [])
            self.assertEqual(profile.skills, [])
            education = profile.candidates[0]
            profile = review_profile_candidate(db, user_id=self.user_id, candidate_id=education.id, payload=ProfileCandidateReviewRequest(revision=profile.revision, action="confirm"))
            self.assertEqual(profile.education[0].end_date, "2026-06")
            self.assertTrue(profile.education[0].source.test_result)
            java = next(item for item in profile.candidates if item.category == "skill" and item.value.name == "Java")
            with self.assertRaises(ProfileError):
                review_profile_candidate(db, user_id=self.user_id, candidate_id=java.id, payload=ProfileCandidateReviewRequest(revision=profile.revision, action="confirm"))
            profile = review_profile_candidate(db, user_id=self.user_id, candidate_id=java.id, payload=ProfileCandidateReviewRequest.model_validate({"revision": profile.revision, "action": "modify", "value": {"name": "本人明确提供的合成技能"}}))
            go = next(item for item in profile.candidates if item.category == "skill" and item.value.name == "Go")
            profile = review_profile_candidate(db, user_id=self.user_id, candidate_id=go.id, payload=ProfileCandidateReviewRequest(revision=profile.revision, action="reject"))
            self.assertEqual(db.scalar(select(func.count()).select_from(CareerProfileEntry)), 2)
            self.assertEqual(self.ingest(db).revision, profile.revision)
            self.assertEqual(db.scalar(select(func.count()).select_from(ProfileCandidate)), 7)

    def test_source_text_layout_or_file_version_change_blocks_old_confirmation(self):
        for changed in ("raw_text", "text_layout", "sha256"):
            with self.subTest(changed=changed), Session(self.engine) as db:
                profile = self.ingest(db)
                document = db.get(ResumeDocument, self.resume_id)
                original = getattr(document, changed)
                setattr(document, changed, SYNTHETIC_RESUME_TEXT + "\n已变化" if changed == "raw_text" else build_text_layout(SYNTHETIC_RESUME_TEXT, [{"page": 2, "start": 0, "end": len(SYNTHETIC_RESUME_TEXT)}]) if changed == "text_layout" else "a" * 64)
                db.commit()
                current = get_profile(db, user_id=self.user_id)
                self.assertEqual(current.candidates[0].evidence_status, "needs_review")
                with self.assertRaises(ProfileError) as error:
                    review_profile_candidate(db, user_id=self.user_id, candidate_id=profile.candidates[0].id, payload=ProfileCandidateReviewRequest(revision=profile.revision, action="confirm"))
                self.assertEqual(error.exception.code, "candidate_source_version_conflict")
                document = db.get(ResumeDocument, self.resume_id)
                setattr(document, changed, original)
                db.commit()

    def test_ingestion_recomputes_locations_and_rejects_forged_mapping(self):
        with Session(self.engine) as db:
            metadata = copy.deepcopy(self.result.candidate_metadata)
            metadata[0]["fields"][0]["raw_value"] = "伪造学校"
            with self.assertRaises(ProfileError) as error:
                ingest_profile_candidates(db, user_id=self.user_id, resume_id=self.resume_id, batch_key="forged", payload=self.result.batch, candidate_metadata=metadata)
            self.assertEqual(error.exception.code, "invalid_candidate_mapping")
            self.assertEqual(db.scalar(select(func.count()).select_from(ProfileCandidate)), 0)

    def test_ingestion_rejects_run_links_to_other_users_or_resumes_and_missing_metadata(self):
        from offerpilot_api.agent_runtime.extraction_runs import source_version
        with Session(self.engine) as db:
            other_user = uuid4()
            db.add(User(id=other_user, email_normalized="synthetic-other-v2@example.com", password_hash="synthetic-only"))
            db.flush()
            document = db.get(ResumeDocument, self.resume_id)
            for wrong_user, wrong_resume in ((other_user, self.resume_id), (self.user_id, uuid4())):
                run = ResumeExtractionRun(user_id=wrong_user, resume_id=wrong_resume, idempotency_key=uuid4().hex,
                    source_version=source_version(document), status="succeeded", max_attempts=2, max_calls=4, timeout_seconds=30,
                    schema_version="2.0", extraction_version=uuid4().hex, prompt_version="resume_extract_v2", provider_version="mock-synthetic-fixture-v2")
                db.add(run)
                db.commit()
                with self.assertRaises(ProfileError) as error:
                    self.ingest(db, run_id=run.id)
                self.assertEqual(error.exception.status_code, 404)
            with self.assertRaises(ProfileError) as error:
                ingest_profile_candidates(db, user_id=self.user_id, resume_id=self.resume_id, batch_key="no-metadata", payload=self.result.batch, run_id=uuid4())
            self.assertEqual(error.exception.code, "invalid_candidate_run")
            self.assertEqual(db.scalar(select(func.count()).select_from(ProfileCandidate)), 0)

    def test_trace_mapping_ownership_is_strictly_validated(self):
        metadata = copy.deepcopy(self.result.candidate_metadata)
        metadata[0]["fields"][0]["mapped_field"] = "major"
        with Session(self.engine) as db, self.assertRaises(ProfileError) as error:
            ingest_profile_candidates(db, user_id=self.user_id, resume_id=self.resume_id, batch_key="bad-ownership", payload=self.result.batch, candidate_metadata=metadata)
        self.assertEqual(error.exception.code, "invalid_candidate_metadata")

    def test_successful_run_cannot_add_new_batch_and_existing_run_batch_is_idempotent(self):
        from offerpilot_api.agent_runtime.extraction_runs import source_version
        with Session(self.engine) as db:
            document = db.get(ResumeDocument, self.resume_id)
            run = ResumeExtractionRun(user_id=self.user_id, resume_id=self.resume_id, idempotency_key=uuid4().hex,
                source_version=source_version(document), status="succeeded", max_attempts=2, max_calls=4, timeout_seconds=30,
                schema_version="2.0", extraction_version=uuid4().hex, prompt_version="resume_extract_v2", provider_version="mock-synthetic-fixture-v2")
            db.add(run)
            db.commit()
            with self.assertRaises(ProfileError) as error:
                ingest_profile_candidates(db, user_id=self.user_id, resume_id=self.resume_id, batch_key=str(run.id), payload=self.result.batch, candidate_metadata=self.result.candidate_metadata, run_id=run.id)
            self.assertEqual(error.exception.code, "invalid_candidate_run")
            run = db.get(ResumeExtractionRun, run.id)
            run.status, run.execution_token, run.lease_deadline = "running", uuid4(), datetime.now(timezone.utc) + timedelta(seconds=30)
            db.commit()
            profile = ingest_profile_candidates(db, user_id=self.user_id, resume_id=self.resume_id, batch_key=str(run.id), payload=self.result.batch, candidate_metadata=self.result.candidate_metadata, run_id=run.id)
            run = db.get(ResumeExtractionRun, run.id)
            run.status, run.execution_token, run.lease_deadline, run.candidate_count = "succeeded", None, None, 7
            db.commit()
            repeated = ingest_profile_candidates(db, user_id=self.user_id, resume_id=self.resume_id, batch_key=str(run.id), payload=self.result.batch, candidate_metadata=self.result.candidate_metadata, run_id=run.id)
            self.assertEqual(repeated.revision, profile.revision)
            with self.assertRaises(ProfileError) as error:
                ingest_profile_candidates(db, user_id=self.user_id, resume_id=self.resume_id, batch_key="extra-batch", payload=self.result.batch, candidate_metadata=self.result.candidate_metadata, run_id=run.id)
            self.assertEqual(error.exception.code, "invalid_candidate_run")
            self.assertEqual(db.scalar(select(func.count()).select_from(ProfileCandidate)), 7)


if __name__ == "__main__":
    unittest.main()
