"""Deterministic JD proposals preserve source language and uncertainty."""

import unittest

from offerpilot_api.job_intelligence.parser import parse_jd


class JobParserTests(unittest.TestCase):
    def test_numeric_requirements_are_not_mistaken_for_bullet_numbers(self):
        text = "任职要求：\r\n2027届毕业生\r\n3年工作经验\r\n1. 本科及以上学历\r\n(2) 必须掌握 Python\r\n"
        items = parse_jd(text).requirements
        self.assertEqual([item.content for item in items], ["2027届毕业生", "3年工作经验", "本科及以上学历", "必须掌握 Python"])
        for item in items:
            self.assertEqual(text[item.source_start:item.source_start + len(item.evidence_text)], item.evidence_text)
            self.assertEqual(item.review_status, "pending")

    def test_preferred_technical_skill_is_never_upgraded_to_must(self):
        items = parse_jd("岗位要求：\n熟悉 Java 优先\n必须掌握 Python\n加分项：\n有 Redis 经验\n").requirements
        self.assertEqual([item.priority for item in items], ["preferred", "must", "preferred"])

    def test_related_major_is_verbatim_and_scope_remains_uncertain(self):
        item = parse_jd("任职要求：\n计算机相关专业\n").requirements[0]
        self.assertEqual((item.type, item.content, item.priority), ("major", "计算机相关专业", "uncertain"))

    def test_mixed_preferred_and_explicit_must_is_uncertain(self):
        text = "加分项：\n必须掌握 Python\n岗位要求：\n必须掌握 Java 优先\n"
        self.assertTrue(all(item.priority == "uncertain" for item in parse_jd(text).requirements))

    def test_negative_optional_or_question_language_stays_uncertain(self):
        text = "任职要求：\n无需工作经验\n不要求英语六级\n非必须掌握 Python\n工作地点上海，可否远程需核实\nnot required SQL\n"
        self.assertTrue(all(item.priority == "uncertain" for item in parse_jd(text).requirements))

    def test_other_heading_resets_preferred_scope(self):
        items = parse_jd("加分项：\n熟悉 React\n其他：\n工作地点：上海，可否远程需与招聘方核实\n").requirements
        self.assertEqual([item.priority for item in items], ["preferred", "uncertain"])

    def test_responsibilities_and_salary_are_information_not_qualification(self):
        items = parse_jd("加分项：\n薪资20-30K/月\n负责后台系统开发\n工作性质：全职\n").requirements
        self.assertEqual([item.type for item in items], ["salary", "responsibility", "work_type"])
        self.assertTrue(all(item.priority == "uncertain" for item in items))

    def test_internship_duration_is_explicit_and_english_language_has_type(self):
        items = parse_jd("岗位要求：\n不少于3个月实习\n每周到岗4天\n英语六级\n").requirements
        self.assertEqual([item.type for item in items], ["internship_duration", "internship_duration", "language"])

    def test_long_clause_is_skipped_with_warning_without_source_truncation(self):
        text = "岗位要求：\n" + "Python" * 400
        result = parse_jd(text)
        self.assertEqual(result.requirements, [])
        self.assertTrue(any("2000" in item for item in result.warnings))

    def test_proposals_have_bounded_count_and_explicit_limit_warning(self):
        result = parse_jd("岗位要求：\n" + "必须掌握 Python\n" * 101)
        self.assertEqual(len(result.requirements), 100)
        self.assertTrue(any("100" in item for item in result.warnings))

    def test_plain_company_prose_does_not_become_invented_requirement(self):
        self.assertEqual(parse_jd("合成公司致力于帮助用户管理资料\n欢迎加入团队\n").requirements, [])
