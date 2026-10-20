"""Synthetic decision examples and counterexamples for trusted matching.

These tests assert user-visible judgments and evidence ownership, including
cases where rejecting a job or claiming a qualification would be unjustified.
"""

import copy
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from offerpilot_api.job_catalog.schemas import JobDetailResponse
from offerpilot_api.job_intelligence.matching import RULE_VERSION, evaluate_match
from offerpilot_api.job_intelligence.match_schemas import MatchEvaluation
from offerpilot_api.profile.schemas import CareerProfileResponse

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def entry(**value):
    return {"id": uuid4(), "source": {"kind": "manual", "resume_id": None, "resume_file_name": None, "field_sources": {}, "evidence": []}, **value}


def profile(**changes):
    return CareerProfileResponse.model_validate({
        "revision": 1, "updated_at": NOW, "education": [], "skills": [],
        "projects": [], "experiences": [], "candidates": [],
        "completion": {"populated_groups": [], "missing_fields": [], "pending_count": 0}, **changes,
    })


def requirement(content, kind="technical_skill", priority="must", **changes):
    return {
        "id": uuid4(), "type": kind, "content": content, "priority": priority,
        "review_status": "confirmed", "evidence_text": content, "source_start": 0,
        "source_end": len(content), "source_jd_version": 1, "line_start": 1, "line_end": 1,
        "source_kind": "manual", "stale": False, "revision": 1, "parser_version": None,
        "created_at": NOW, "updated_at": NOW, "reviewed_at": NOW, **changes,
    }


def job(*requirements, **changes):
    return JobDetailResponse.model_validate({
        "id": uuid4(), "title": "合成后端实习生", "company": "合成公司", "location": "上海",
        "salary": "税前200-300元/天", "work_type": "internship", "source_url": None,
        "source_kind": "manual", "revision": 1, "jd_version": 1, "is_favorite": False,
        "created_at": NOW, "updated_at": NOW, "review_summary": {"total": len(requirements), "confirmed": len(requirements), "pending": 0, "stale": 0},
        "jd_text": "\n".join(r["content"] for r in requirements), "requirements": list(requirements),
        "parser_version": "jd-rules-1.0", "jd_snapshots": [], **changes,
    })


class MatchingRulesTests(unittest.TestCase):
    def evaluate(self, person, text, kind="technical_skill", priority="must"):
        result = evaluate_match(person, job(requirement(text, kind, priority)))
        self.assertEqual(result.rule_version, RULE_VERSION)
        self.assertEqual(len(result.items), 1)
        return result, result.items[0]

    def test_acquired_undergraduate_satisfies_explicit_minimum(self):
        result, row = self.evaluate(profile(education=[entry(degree="本科", end_date="2025-06", end_date_kind="actual")]), "本科及以上学历", "education")
        self.assertEqual(row.status, "met")
        self.assertEqual(result.conclusion, "consider")
        self.assertEqual({e.field for e in row.profile_evidence}, {"degree", "end_date", "end_date_kind"})

    def test_explicit_highest_degree_can_conflict_but_missing_higher_degree_cannot(self):
        for degree, expected in (("最高学历：大专", "unmet"), ("大专", "partial")):
            with self.subTest(degree=degree):
                result, row = self.evaluate(profile(education=[entry(degree=degree, end_date="2025-06", end_date_kind="actual")]), "本科及以上学历", "education")
                self.assertEqual(row.status, expected)
                self.assertEqual(result.conclusion, "hard_conflict" if expected == "unmet" else "verify_first")

    def test_in_progress_is_not_an_acquired_bachelor_degree(self):
        result, row = self.evaluate(profile(education=[entry(degree="本科", end_date="2027-06", end_date_kind="expected")]), "本科及以上学历", "education")
        self.assertEqual(row.status, "partial")
        self.assertIn("在读", row.reason)
        self.assertEqual(result.conclusion, "verify_first")

    def test_explicit_enrolled_bachelor_requirement(self):
        _, row = self.evaluate(profile(education=[entry(degree="本科", end_date="2027-06", end_date_kind="expected")]), "本科在读", "education")
        self.assertEqual(row.status, "met")

    def test_graduated_person_is_not_automatically_current_student(self):
        _, row = self.evaluate(profile(education=[entry(degree="本科", end_date="2025-06", end_date_kind="actual")]), "本科在读", "education")
        self.assertEqual(row.status, "unknown")

    def test_degree_with_unknown_graduation_status_is_not_automatically_met(self):
        _, row = self.evaluate(profile(education=[entry(degree="本科")]), "本科及以上学历", "education")
        self.assertEqual(row.status, "unknown")

    def test_past_expected_date_never_becomes_actual_degree(self):
        _, row = self.evaluate(profile(education=[entry(degree="本科", end_date="2020-06", end_date_kind="expected")]), "已取得本科学历", "education")
        self.assertEqual(row.status, "partial")

    def test_unknown_education_never_proves_ineligibility(self):
        result, row = self.evaluate(profile(education=[entry(school="合成大学")]), "硕士及以上学历", "education")
        self.assertEqual(row.status, "unknown")
        self.assertNotEqual(result.conclusion, "hard_conflict")

    def test_education_additional_qualifications_not_discarded(self):
        person = profile(education=[entry(degree="本科", end_date="2025-06", end_date_kind="actual")])
        for text in ("全日制本科及以上学历", "985本科学历", "本科且熟悉Python", "本科及以上且有三年工作经验"):
            with self.subTest(text=text):
                _, row = self.evaluate(person, text, "education")
                self.assertEqual(row.status, "unknown")

    def test_degree_keyword_inside_denied_incomplete_or_vague_fact_is_not_a_degree(self):
        for degree in ("本科肄业", "本科结业", "本科未毕业", "本科未取得", "本科退学", "非本科", "本科及以上", "计划攻读本科"):
            with self.subTest(degree=degree):
                _, row = self.evaluate(profile(education=[entry(degree=degree, end_date="2025-06", end_date_kind="actual")]), "本科及以上学历", "education")
                self.assertEqual(row.status, "unknown")

    def test_contradictory_enrolled_and_actual_education_requires_review(self):
        person = profile(education=[entry(degree="本科在读", end_date="2027-06", end_date_kind="actual")])
        for text, kind in (("本科在读", "education"), ("2027届毕业生", "graduation")):
            with self.subTest(kind=kind):
                _, row = self.evaluate(person, text, kind)
                self.assertEqual(row.status, "unknown")

    def test_higher_degree_does_not_prove_specific_undergraduate_status(self):
        for kind in ("actual", "expected"):
            with self.subTest(kind=kind):
                person = profile(education=[entry(degree="硕士", end_date="2027-06", end_date_kind=kind)])
                _, specific = self.evaluate(person, "本科在读" if kind == "expected" else "本科学历", "education")
                _, minimum = self.evaluate(person, "本科及以上在读" if kind == "expected" else "本科及以上学历", "education")
                self.assertEqual(specific.status, "partial")
                self.assertEqual(minimum.status, "met")

    def test_highest_degree_conflict_cannot_be_hidden_by_another_education(self):
        person = profile(education=[entry(degree="最高学历：大专", end_date="2025-06", end_date_kind="actual"), entry(degree="本科", end_date="2024-06", end_date_kind="actual")])
        result, row = self.evaluate(person, "本科及以上学历", "education")
        self.assertEqual(row.status, "unknown")
        self.assertEqual(result.conclusion, "verify_first")
        self.assertEqual(len({e.entry_id for e in row.profile_evidence}), 2)

    def test_generic_postgraduate_cannot_be_used_to_reject_doctorate_requirement(self):
        result, row = self.evaluate(profile(education=[entry(degree="最高学历：研究生", end_date="2025-06", end_date_kind="actual")]), "博士学历", "education")
        self.assertEqual(row.status, "unknown")
        self.assertNotEqual(result.conclusion, "hard_conflict")

    def test_explicit_doctoral_postgraduate_degree_is_supported(self):
        _, row = self.evaluate(profile(education=[entry(degree="博士研究生", end_date="2025-06", end_date_kind="actual")]), "博士及以上学历", "education")
        self.assertEqual(row.status, "met")

    def test_expected_and_actual_cohort_years_are_explicitly_described(self):
        for kind in ("actual", "expected"):
            with self.subTest(kind=kind):
                _, row = self.evaluate(profile(education=[entry(degree="本科", end_date="2027-06", end_date_kind=kind)]), "2027届毕业生", "graduation")
                self.assertEqual(row.status, "met")
                self.assertIn("预计" if kind == "expected" else "实际", row.reason)

    def test_cohort_range_edges_and_conflict(self):
        for year, expected in ((2026, "met"), (2027, "met"), (2028, "met"), (2029, "unmet")):
            with self.subTest(year=year):
                result, row = self.evaluate(profile(education=[entry(end_date=f"{year}-06", end_date_kind="expected")]), "2026—2028届毕业生", "graduation")
                self.assertEqual(row.status, expected)
                self.assertEqual(result.conclusion, "hard_conflict" if expected == "unmet" else "consider")

    def test_graduation_unknown_or_undefined_recent_graduate(self):
        for text in ("2027届毕业生", "应届毕业生"):
            with self.subTest(text=text):
                _, row = self.evaluate(profile(education=[entry(degree="本科")]), text, "graduation")
                self.assertEqual(row.status, "unknown")
                self.assertTrue(row.verification)

    def test_actual_graduation_requirement_does_not_accept_expected(self):
        _, row = self.evaluate(profile(education=[entry(end_date="2027-06", end_date_kind="expected")]), "2027年实际毕业", "graduation")
        self.assertEqual(row.status, "partial")

    def test_already_graduated_wording_cannot_accept_expected_date(self):
        person = profile(education=[entry(end_date="2027-06", end_date_kind="expected")])
        for text in ("已于2027年毕业", "已经于2027年毕业", "实际2027年毕业"):
            with self.subTest(text=text):
                _, row = self.evaluate(person, text, "graduation")
                self.assertNotEqual(row.status, "met")

    def test_mixed_expected_actual_graduation_requirement_is_unknown(self):
        _, row = self.evaluate(profile(education=[entry(end_date="2027-06", end_date_kind="actual")]), "预计2027年实际毕业", "graduation")
        self.assertEqual(row.status, "unknown")

    def test_combined_degree_and_cohort_must_come_from_same_entry(self):
        person = profile(education=[entry(degree="本科", end_date="2028-06", end_date_kind="expected"), entry(degree="大专", end_date="2027-06", end_date_kind="actual")])
        _, row = self.evaluate(person, "2027届本科及以上毕业生", "education")
        self.assertNotEqual(row.status, "met")
        self.assertLessEqual(len({e.entry_id for e in row.profile_evidence}), 1)

    def test_explicit_cohort_undergraduate_recruitment_accepts_expected_without_claiming_degree_held(self):
        _, row = self.evaluate(profile(education=[entry(degree="本科", end_date="2027-06", end_date_kind="expected")]), "2027届本科及以上毕业生", "education")
        self.assertEqual(row.status, "met")
        self.assertIn("预计", row.reason)

    def test_undefined_recent_undergraduate_recruitment_is_unknown(self):
        _, row = self.evaluate(profile(education=[entry(degree="本科", end_date="2027-06", end_date_kind="expected")]), "应届本科毕业生", "education")
        self.assertEqual(row.status, "unknown")

    def test_exact_major_matches(self):
        _, row = self.evaluate(profile(education=[entry(major="计算机科学与技术")]), "计算机科学与技术专业", "major")
        self.assertEqual(row.status, "met")

    def test_related_major_never_uses_unverified_taxonomy(self):
        for major in ("软件工程", "计算机科学与技术", "数学"):
            with self.subTest(major=major):
                _, row = self.evaluate(profile(education=[entry(major=major)]), "计算机相关专业", "major")
                self.assertEqual(row.status, "unknown")

    def test_different_major_does_not_prove_missing_other_education(self):
        _, row = self.evaluate(profile(education=[entry(major="软件工程")]), "计算机科学与技术专业", "major")
        self.assertEqual(row.status, "unknown")

    def test_java_and_javascript_are_distinct(self):
        for actual, requested in (("Java", "JavaScript"), ("JavaScript", "Java")):
            with self.subTest(actual=actual):
                _, row = self.evaluate(profile(skills=[entry(name=actual, level="熟练")]), f"必须熟悉{requested}")
                self.assertEqual(row.status, "unknown")

    def test_controlled_alias_and_directed_version_relation(self):
        for actual, requested, expected in (("JS", "JavaScript", "met"), ("Python 3", "Python", "met"), ("Python3", "Python 3", "met"), ("Python", "Python 3", "unknown"), ("Python2", "Python3", "unknown")):
            with self.subTest(actual=actual, requested=requested):
                _, row = self.evaluate(profile(skills=[entry(name=actual, level="熟悉")]), f"必须熟悉{requested}")
                self.assertEqual(row.status, expected)

    def test_react_native_does_not_prove_react(self):
        for actual, requested in (("React", "React Native"), ("React Native", "React")):
            with self.subTest(actual=actual):
                _, row = self.evaluate(profile(skills=[entry(name=actual, level="熟悉")]), f"熟悉{requested}")
                self.assertEqual(row.status, "unknown")

    def test_skill_level_must_have_explicit_formal_evidence(self):
        for level, expected in ((None, "partial"), ("了解", "partial"), ("熟悉", "met"), ("精通", "met"), ("多年经验", "unknown")):
            with self.subTest(level=level):
                _, row = self.evaluate(profile(skills=[entry(name="Python", level=level)]), "必须熟悉Python")
                self.assertEqual(row.status, expected)

    def test_plan_and_negative_skill_are_not_positive_evidence(self):
        for name, level, expected in (("Python", "计划学习", "unknown"), ("计划学习Python", None, "unknown"), ("Python", "正在学习", "unknown"), ("Python", "不会", "unmet"), ("未掌握Python", None, "unmet")):
            with self.subTest(name=name, level=level):
                result, row = self.evaluate(profile(skills=[entry(name=name, level=level)]), "必须熟悉Python")
                self.assertEqual(row.status, expected)
                self.assertEqual(result.conclusion, "hard_conflict" if expected == "unmet" else "verify_first")

    def test_plain_skill_name_cannot_override_denial_or_learning_plan(self):
        for level in ("未接触", "尚未掌握", "零基础", "not proficient", "plan to learn", "learning", "从未学过", "not yet familiar", "不懂", "完全不懂", "没有掌握", "不太会", "没有用过", "cannot use", "unfamiliar", "none", "never used"):
            with self.subTest(level=level):
                _, row = self.evaluate(profile(skills=[entry(name="Python", level=level)]), "Python")
                self.assertNotEqual(row.status, "met")

    def test_denial_of_another_skill_cannot_reject_this_skill(self):
        for level in ("熟悉 Python；不会 Java", "熟悉Python但不会绘画", "熟悉Python，计划学习Java", "熟悉Python not proficient in Java"):
            with self.subTest(level=level):
                result, row = self.evaluate(profile(skills=[entry(name="Python", level=level)]), "熟悉Python")
                self.assertEqual(row.status, "unknown")
                self.assertNotEqual(result.conclusion, "hard_conflict")

    def test_conflicting_formal_skill_entries_are_not_resolved_by_cherry_picking(self):
        for negative in ("不会", "计划学习"):
            with self.subTest(negative=negative):
                person = profile(skills=[entry(name="Python3", level="熟悉"), entry(name="Python", level=negative)])
                result, row = self.evaluate(person, "熟悉Python")
                self.assertEqual(row.status, "unknown")
                self.assertEqual(result.conclusion, "verify_first")
                self.assertEqual(len({e.entry_id for e in row.profile_evidence}), 2)

    def test_unknown_provided_skill_level_cannot_be_hidden_by_positive_duplicate(self):
        for unknown_level in ("不懂", "cannot use", "none", "没有掌握", "未接触", "计划学习", "熟悉Python；不会Java", "多年经验"):
            for reverse in (False, True):
                for priority in ("must", "preferred"):
                    with self.subTest(level=unknown_level, reverse=reverse, priority=priority):
                        values = [entry(name="Python3", level="熟悉"), entry(name="Python", level=unknown_level)]
                        if reverse:
                            values.reverse()
                        _, row = self.evaluate(profile(skills=values), "熟悉Python", priority=priority)
                        self.assertEqual(row.status, "unknown")
                        self.assertTrue(row.verification)
                        self.assertEqual(len({e.entry_id for e in row.profile_evidence}), 2)

    def test_duplicate_missing_skill_level_does_not_negate_explicit_positive_fact(self):
        person = profile(skills=[entry(name="Python", level=None), entry(name="Python3", level="熟悉")])
        _, row = self.evaluate(person, "熟悉Python")
        self.assertEqual(row.status, "met")

    def test_missing_skills_does_not_reject_job(self):
        result, row = self.evaluate(profile(), "必须熟悉Python")
        self.assertEqual(row.status, "unknown")
        self.assertEqual(result.conclusion, "verify_first")
        self.assertIn("不代表不具备", row.reason)

    def test_no_skill_proficiency_is_inferred_from_project_text(self):
        _, row = self.evaluate(profile(projects=[entry(name="项目", description="使用Python开发接口")]), "熟悉Python")
        self.assertEqual(row.status, "unknown")
        self.assertEqual(row.profile_evidence, [])

    def test_unsupported_skill_compounds_never_claim_met(self):
        person = profile(skills=[entry(name="Python", level="精通")])
        for text in ("必须Python编程5年经验", "熟悉Python和分布式系统", "熟悉Python且有金融经验", "精通Python及其他相关工具", "Python3.12", "Python开发达到专家水平", "熟悉Python且英语流利", "Python2或Python3.12"):
            with self.subTest(text=text):
                _, row = self.evaluate(person, text)
                self.assertEqual(row.status, "unknown")

    def test_conjunction_partial_and_disjunction_met(self):
        person = profile(skills=[entry(name="Python3", level="熟悉")])
        _, both = self.evaluate(person, "熟悉Python和Java")
        _, either = self.evaluate(person, "熟悉Python或Java")
        self.assertEqual(both.status, "partial")
        self.assertEqual(either.status, "met")

    def test_mixed_and_or_scope_cannot_be_reduced_to_any_one_skill(self):
        person = profile(skills=[entry(name="Python", level="熟悉")])
        for text in ("熟悉Python和Java或Go", "熟悉Python或Java和Go", "熟悉Python、Java或Go", "熟悉Python以及Java或Go"):
            with self.subTest(text=text):
                _, row = self.evaluate(person, text)
                self.assertEqual(row.status, "unknown")

    def test_preferred_missing_or_negative_skill_never_causes_hard_rejection(self):
        for person in (profile(), profile(skills=[entry(name="Java", level="不会")])):
            with self.subTest(person=bool(person.skills)):
                result, row = self.evaluate(person, "熟悉Java优先", priority="preferred")
                self.assertNotEqual(row.status, "unmet")
                self.assertEqual(result.conclusion, "consider")

    def test_same_named_projects_cannot_splice_technology_evidence(self):
        first = entry(name="同名项目", description="使用Python编写接口")
        second = entry(name="同名项目", description="使用Redis缓存")
        _, row = self.evaluate(profile(projects=[first, second]), "具备Python和Redis项目经验", "project")
        # The compound is either wholly unsupported or requires both in one row.
        self.assertNotEqual(row.status, "met")
        self.assertLessEqual(len({e.entry_id for e in row.profile_evidence}), 1)

    def test_project_experience_is_bound_to_one_concrete_entry(self):
        first = entry(name="同名项目", description="使用Python编写接口")
        second = entry(name="同名项目", description="使用Java编写工具")
        _, row = self.evaluate(profile(projects=[first, second]), "有Python项目经验", "project")
        self.assertEqual(row.status, "met")
        self.assertEqual({e.entry_id for e in row.profile_evidence}, {first["id"]})
        self.assertNotIn(str(second["id"]), row.model_dump_json())

    def test_negated_technology_mentions_are_not_experience_evidence(self):
        for description in ("未使用 Python，使用 Java", "没有使用 Python", "did not use Python", "no Python experience", "负责Java开发，并非Python开发"):
            with self.subTest(description=description):
                _, row = self.evaluate(profile(projects=[entry(name="合成项目", description=description)]), "有Python项目经验", "project")
                self.assertEqual(row.status, "unknown")

    def test_other_people_technology_usage_is_not_users_experience(self):
        for description in ("仅旁观同学使用Python，本人使用Java", "同事负责Python接口，本人负责Java", "仅阅读Python项目代码"):
            with self.subTest(description=description):
                _, row = self.evaluate(profile(projects=[entry(name="合成项目", role="观察者", description=description)]), "有Python项目经验", "project")
                self.assertEqual(row.status, "unknown")

    def test_part_time_role_is_not_full_time_duration(self):
        for description in ("每周兼职一天，使用Python", "part-time Python developer", "非全职Python开发"):
            with self.subTest(description=description):
                _, row = self.evaluate(profile(experiences=[entry(organization="合成公司", role="工程师", experience_type="work", description=description, start_date="2024-01", end_date="2026-01")]), "具有1年全职工作经验", "experience")
                self.assertEqual(row.status, "unknown")

    def test_work_type_alone_does_not_assert_full_time(self):
        generic = entry(organization="合成公司", role="工程师", experience_type="work", description="使用Python开发", start_date="2024-01", end_date="2026-01")
        _, incomplete = self.evaluate(profile(experiences=[generic]), "具有1年全职工作经验", "experience")
        self.assertEqual(incomplete.status, "partial")
        full_time = copy.deepcopy(generic)
        full_time["description"] = "全职使用Python开发"
        _, complete = self.evaluate(profile(experiences=[full_time]), "具有1年全职工作经验", "experience")
        self.assertEqual(complete.status, "met")

    def test_technical_keyword_without_direct_usage_action_is_not_experience(self):
        for description in ("Python", "了解Python概念，使用Java开发", "Python；使用Java开发", "学习Python并开发Java"):
            with self.subTest(description=description):
                _, row = self.evaluate(profile(projects=[entry(name="合成项目", description=description)]), "有Python项目经验", "project")
                self.assertEqual(row.status, "unknown")

    def test_two_technologies_can_be_matched_only_when_used_in_one_entry(self):
        same = entry(name="合成项目", description="使用Python开发接口，使用Redis缓存数据")
        _, row = self.evaluate(profile(projects=[same]), "有Python和Redis项目经验", "project")
        self.assertEqual(row.status, "met")
        self.assertEqual({e.entry_id for e in row.profile_evidence}, {same["id"]})

    def test_generic_role_cannot_prove_unsupported_development_qualifier(self):
        _, row = self.evaluate(profile(experiences=[entry(organization="合成公司", role="摄影师", experience_type="work", description="拍摄照片")]), "有开发工作经验", "experience")
        self.assertEqual(row.status, "unknown")

    def test_course_project_and_internship_are_not_full_time_work(self):
        person = profile(projects=[entry(name="课程项目", description="使用Python实现系统")], experiences=[entry(organization="合成公司", role="实习工程师", experience_type="internship", description="使用Python维护接口", start_date="2024-01", end_date="2026-01")])
        _, row = self.evaluate(person, "具有1年工作经验", "experience")
        self.assertEqual(row.status, "unknown")
        self.assertEqual(row.profile_evidence, [])

    def test_internship_matches_internship_only(self):
        person = profile(experiences=[entry(organization="合成公司", role="实习生", experience_type="internship", description="维护接口", start_date="2026-01", end_date="2026-07")])
        _, row = self.evaluate(person, "具有3个月实习经历", "internship_experience")
        self.assertEqual(row.status, "met")
        self.assertIn("至少5个完整月", row.reason)

    def test_incomplete_or_ongoing_dates_do_not_invent_duration(self):
        for values in ({"start_date": "2024-01", "is_current": True}, {"start_date": "2024-01"}, {"end_date": "2026-01"}):
            with self.subTest(values=values):
                _, row = self.evaluate(profile(experiences=[entry(organization="合成公司", role="工程师", experience_type="work", **values)]), "具有1年工作经验", "experience")
                self.assertEqual(row.status, "partial")

    def test_short_work_entry_does_not_prove_total_experience_insufficient(self):
        _, row = self.evaluate(profile(experiences=[entry(organization="合成公司", role="工程师", experience_type="work", start_date="2026-01", end_date="2026-06")]), "具有1年工作经验", "experience")
        self.assertEqual(row.status, "partial")

    def test_same_month_is_not_invented_as_complete_month(self):
        _, row = self.evaluate(profile(experiences=[entry(organization="合成公司", role="实习生", experience_type="internship", start_date="2026-01", end_date="2026-01")]), "具有1个月实习经历", "internship_experience")
        self.assertEqual(row.status, "partial")
        self.assertIn("至少0个完整月", row.reason)

    def test_month_precision_does_not_claim_boundary_duration_as_fully_completed(self):
        for end_date, expected in (("2026-04", "partial"), ("2026-05", "met")):
            with self.subTest(end_date=end_date):
                person = profile(experiences=[entry(organization="合成公司", role="实习生", experience_type="internship", start_date="2026-01", end_date=end_date)])
                _, row = self.evaluate(person, "具有3个月实习经历", "internship_experience")
                self.assertEqual(row.status, expected)

    def test_different_work_entries_are_not_spliced_for_technology_and_duration(self):
        first = entry(organization="同名公司", role="工程师", experience_type="work", description="使用Python开发", start_date="2026-01", end_date="2026-06")
        second = entry(organization="同名公司", role="工程师", experience_type="work", description="使用Java开发", start_date="2023-01", end_date="2026-01")
        _, row = self.evaluate(profile(experiences=[first, second]), "具有1年Python工作经验", "experience")
        self.assertNotEqual(row.status, "met")
        self.assertLessEqual(len({e.entry_id for e in row.profile_evidence}), 1)

    def test_related_production_experience_is_not_assumed(self):
        _, row = self.evaluate(profile(projects=[entry(name="课程项目", description="Python开发")]), "有真实商业Python项目经验", "project")
        self.assertEqual(row.status, "unknown")

    def test_planned_or_name_only_project_does_not_claim_experience(self):
        for value in (entry(name="计划学习项目", description="准备使用Python开发"), entry(name="合成项目")):
            with self.subTest(value=value):
                _, row = self.evaluate(profile(projects=[value]), "有项目经验", "project")
                self.assertNotEqual(row.status, "met")

    def test_city_work_type_and_salary_preferences_are_separate_from_qualification(self):
        person = profile(preferences={"locations": ["北京"], "work_types": ["full_time"], "salary": {"min": 400, "max": 500, "currency": "CNY", "period": "day", "tax": "pre_tax"}})
        result = evaluate_match(person, job(requirement("具备SQL技能", priority="preferred")))
        prefs = [i for i in result.items if i.scope == "preference"]
        self.assertEqual(len(prefs), 3)
        self.assertTrue(all(i.status == "partial" for i in prefs))
        self.assertTrue(all(i.group == "preferences" and i.priority == "preferred" for i in prefs))
        self.assertEqual(result.conclusion, "consider")

    def test_city_exact_and_salary_overlap_match_without_conversion(self):
        person = profile(preferences={"locations": ["上海"], "salary": {"min": 250, "max": 350, "currency": "CNY", "period": "day", "tax": "pre_tax"}})
        result = evaluate_match(person, job(requirement("SQL", priority="preferred")))
        self.assertEqual([i.status for i in result.items if i.scope == "preference"], ["met", "met"])

    def test_salary_ambiguity_and_different_period_never_convert(self):
        person = profile(preferences={"salary": {"min": 200, "max": 300, "currency": "CNY", "period": "day", "tax": "pre_tax"}})
        for salary in ("200-300元/天", "税前10000-20000元/月", "税后200-300元/天", "税前20-30K/月", "税前200-300元/天另有奖金"):
            with self.subTest(salary=salary):
                result = evaluate_match(person, job(requirement("SQL", priority="preferred"), salary=salary))
                self.assertEqual([i.status for i in result.items if i.scope == "preference"], ["unknown"])

    def test_conflicting_salary_units_or_tax_bases_never_claim_overlap(self):
        person = profile(preferences={"salary": {"min": 200, "max": 300, "currency": "CNY", "period": "day", "tax": "pre_tax"}})
        for salary in ("税前税后200-300元/天", "税前200-300元/天/年", "税前200-300元/天400-500元/天"):
            with self.subTest(salary=salary):
                result = evaluate_match(person, job(requirement("SQL", priority="preferred"), salary=salary))
                self.assertEqual([i.status for i in result.items if i.scope == "preference"], ["unknown"])

    def test_no_unfilled_preferences_are_invented(self):
        result, _ = self.evaluate(profile(), "SQL", priority="preferred")
        self.assertFalse(any(i.scope == "preference" for i in result.items))

    def test_time_preference_uses_reviewed_current_requirements_only(self):
        person = profile(preferences={"weekly_days": 4, "internship_months": 6})
        current = requirement("每周至少3天", "internship_duration")
        pending = requirement("实习至少12个月", "internship_duration", review_status="pending")
        result = evaluate_match(person, job(current, pending))
        prefs = {i.type: i for i in result.items if i.scope == "preference"}
        self.assertEqual(prefs["weekly_days_preference"].status, "met")
        self.assertEqual(prefs["internship_months_preference"].status, "unknown")
        self.assertEqual(len(result.excluded_requirements), 1)

    def test_uncertain_reviewed_condition_is_unknown_even_with_matching_skill(self):
        result, row = self.evaluate(profile(skills=[entry(name="Python", level="精通")]), "Python", priority="uncertain")
        self.assertEqual(row.status, "unknown")
        self.assertEqual(result.conclusion, "verify_first")

    def test_pending_and_stale_requirements_are_explicitly_excluded(self):
        result = evaluate_match(profile(), job(requirement("Python", review_status="pending"), requirement("Java", stale=True), requirement("SQL", source_jd_version=2)))
        self.assertEqual(result.items, [])
        self.assertEqual([e.reason for e in result.excluded_requirements], ["pending_review", "stale_jd", "stale_jd"])
        self.assertEqual(result.conclusion, "verify_first")

    def test_unconfirmed_candidate_never_participates_even_if_supported(self):
        candidate = {"id": uuid4(), "category": "skill", "value": {"name": "Python", "level": "精通"}, "status": "pending", "evidence_status": "supported", "evidence": [{"field": "name", "source_text": "Python"}], "resume_id": uuid4(), "resume_file_name": "synthetic.pdf", "schema_version": "2.0", "extraction_version": "synthetic", "entry_id": None, "created_at": NOW, "reviewed_at": None}
        result, row = self.evaluate(profile(candidates=[candidate]), "熟悉Python")
        self.assertEqual(row.status, "unknown")
        self.assertEqual(row.profile_evidence, [])
        self.assertNotIn("精通", result.model_dump_json())

    def test_each_current_reviewed_requirement_has_one_item(self):
        conditions = [requirement("本科在读", "education"), requirement("2027届毕业生", "graduation"), requirement("熟悉Python"), requirement("英语流利", "language"), requirement("出差", "location"), requirement("保留未知字段", "other")]
        result = evaluate_match(profile(), job(*conditions))
        self.assertEqual([i.requirement_id for i in result.items], [r["id"] for r in conditions])
        self.assertEqual(len(result.items), 6)
        self.assertTrue(all(i.status == "unknown" for i in result.items))

    def test_jd_evidence_and_user_evidence_keep_field_and_source(self):
        resume_id = uuid4()
        value = entry(name="Python3", level="熟悉")
        value["source"] = {"kind": "resume_confirmed", "resume_id": resume_id, "resume_file_name": "synthetic.pdf", "field_sources": {"name": "resume", "level": "manual"}, "evidence": [{"field": "name", "source_text": "技能 Python3", "supports_current_value": True}, {"field": "level", "source_text": "旧的掌握程度", "supports_current_value": False}]}
        person = profile(skills=[value])
        condition = requirement("必须熟悉Python", source_start=12, source_end=20, line_start=3, line_end=3)
        result = evaluate_match(person, job(condition))
        row = result.items[0]
        self.assertEqual(row.jd_evidence.evidence_text, condition["content"])
        self.assertEqual((row.jd_evidence.source_start, row.jd_evidence.line_start), (12, 3))
        name = next(e for e in row.profile_evidence if e.field == "name")
        level = next(e for e in row.profile_evidence if e.field == "level")
        self.assertEqual(name.entry_id, value["id"])
        self.assertEqual(name.resume_id, resume_id)
        self.assertEqual(name.source_text, "技能 Python3")
        self.assertIsNone(level.source_text)

    def test_result_is_replayable_schema_valid_and_does_not_mutate_inputs(self):
        person = profile(skills=[entry(name="Python3", level="熟悉")])
        saved_job = job(requirement("熟悉Python"))
        before = copy.deepcopy((person.model_dump(), saved_job.model_dump()))
        first = evaluate_match(person, saved_job)
        second = evaluate_match(person, saved_job)
        self.assertEqual(first, second)
        self.assertEqual(before, (person.model_dump(), saved_job.model_dump()))
        self.assertEqual(first, MatchEvaluation.model_validate_json(first.model_dump_json()))

    def test_synthetic_decision_changes_only_after_confirmed_profile_fact_changes(self):
        person = profile(education=[entry(degree="本科", end_date="2027-06", end_date_kind="expected")], skills=[entry(name="Python3", level="熟悉")])
        saved_job = job(requirement("本科在读", "education"), requirement("2027届毕业生", "graduation"), requirement("必须熟悉Python"), requirement("Java优先", priority="preferred", review_status="pending"))
        before = evaluate_match(person, saved_job)
        self.assertEqual(before.conclusion, "consider")
        updated = person.model_copy(deep=True)
        updated.education[0].end_date = "2028-06"
        updated.revision += 1
        after = evaluate_match(updated, saved_job)
        self.assertEqual(after.conclusion, "hard_conflict")
        self.assertEqual([i.status for i in after.items], ["met", "unmet", "met"])
        self.assertEqual(len(before.excluded_requirements), 1)


if __name__ == "__main__":
    unittest.main()
