"""Free checks for evaluation accounting and aborts; no real SDK transport."""

import copy
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.evaluations import evaluate_deepseek_v2 as evaluation
from backend.evaluations.deepseek_v2_cases import CASE_IDS, evaluation_cases
from offerpilot_api.agent_runtime.llm_provider import LLMProviderError, ProviderUsage
from offerpilot_api.agent_runtime.providers.deepseek import DeepSeekProvider
from offerpilot_api.profile.schemas import CandidateBatchV1
from offerpilot_api.resume_ingestion.extraction_schema import StudentProfileExtractionV2
from offerpilot_api.resume_ingestion.extractor_v2 import map_extraction_v2
from offerpilot_api.resume_ingestion.synthetic_fixtures import (
    default_output, empty_output, leaf,
)


class DeepSeekEvaluationQualityTests(unittest.TestCase):
    def setUp(self):
        # Authorization tests must remain reproducible across execution dates.
        clock = patch.object(evaluation, "_execution_date", return_value=date(2026, 10, 12))
        clock.start()
        self.addCleanup(clock.stop)

    def result(self, values, statuses=None):
        batch = CandidateBatchV1.model_validate({
            "schema_version": "1.0", "extraction_version": "synthetic-test",
            "candidates": [{"category": "skill", "value": value, "evidence": []} for value in values],
        })
        return SimpleNamespace(batch=batch, candidate_metadata=[
            {"evidence_status": status, "fields": []}
            for status in (statuses or ["supported"] * len(values))
        ])

    def gold(self, values, status="supported"):
        return {"candidates": [{"category": "skill", "value": value, "evidence_status": status} for value in values]}

    def run_cli(self, response, *, usage=True, error=None, case_limit=2):
        provider = Mock(last_usage=None)

        def generate(**kwargs):
            provider.last_usage = ProviderUsage(1, 1, 2) if usage else None
            if error:
                raise error
            return response

        provider.generate_json.side_effect = generate
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-ledger.json"
            argv = ["eval", "--authorize-paid-calls", "--authorization-record", "synthetic-owner-approval",
                    "--price-audit-date", "2026-10-12", "--max-calls", "5", "--case-limit", str(case_limit),
                    "--max-budget-cny", "1", "--input-price-per-million-cny", "2",
                    "--output-price-per-million-cny", "8", "--output", str(path)]
            with (patch.object(evaluation, "DeepSeekProvider", return_value=provider),
                  patch.object(sys, "argv", argv), patch("builtins.input", return_value="同意付费评测"),
                  patch.object(evaluation.getpass, "getpass", return_value="synthetic-private-key"),
                  redirect_stdout(StringIO())):
                code = evaluation.main()
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("synthetic-private-key", text)
            report = json.loads(text)
        provider.close.assert_called_once()
        return code, report, provider.generate_json.call_count

    def test_normalized_alias_duplicates_count_as_extra_fields_and_edits(self):
        profile = StudentProfileExtractionV2.model_validate({
            **empty_output(), "skills": [
                {"name": leaf("JavaScript", "JavaScript"), "level": None},
                {"name": leaf("JS", "JS"), "level": None},
            ],
        })
        raw = "技能：JavaScript、JS。"
        result = map_extraction_v2(profile, raw)
        report = evaluation.quality(result, self.gold([{"name": "JavaScript", "level": None}]), raw)
        self.assertEqual(report["candidates"], 2)
        self.assertEqual(report["duplicate_candidates"], 1)
        self.assertEqual(report["extra_candidates"], 1)
        self.assertEqual(report["false_positive_fields"], 1)
        self.assertEqual((report["precision"], report["recall"]), (0.5, 1.0))
        self.assertEqual(report["candidate_modification_ratio"], 0.5)
        self.assertEqual(report["severe_errors"], 0)

    def test_same_identity_gold_is_matched_once_regardless_of_order(self):
        expected = [{"name": "Python", "level": "熟练"}, {"name": "Python", "level": "了解"}]
        report = evaluation.quality(self.result(list(reversed(expected))), self.gold(expected), "")
        self.assertEqual(report["correct"], 4)
        self.assertEqual((report["precision"], report["recall"]), (1.0, 1.0))
        self.assertEqual(report["candidate_modification_ratio"], 0)
        self.assertEqual(report["duplicate_candidates"], 0)

    def test_incorrect_field_counts_false_positive_and_false_negative(self):
        report = evaluation.quality(self.result([{"name": "Python", "level": "未知"}], ["unsupported"]),
                                    self.gold([{"name": "Python", "level": "熟练"}]), "")
        self.assertEqual((report["correct"], report["false_positive_fields"], report["false_negative_fields"]), (1, 1, 1))
        self.assertEqual((report["precision"], report["recall"]), (0.5, 0.5))
        self.assertEqual(report["candidate_modification_ratio"], 1)

    def test_report_paths_do_not_include_untrusted_model_identity(self):
        marker = "PRIVATE_GENERATED_MARKER"
        report = evaluation.quality(self.result([{"name": marker, "level": None}], ["unsupported"]),
                                    self.gold([{"name": "Python", "level": None}]), "")
        self.assertNotIn(marker, json.dumps(report))
        self.assertEqual(report["field_errors"][0]["path"], "skill[0].name")

    def test_empty_result_stops_after_one_simulated_call_with_nonzero_exit(self):
        code, report, calls = self.run_cli(json.dumps(empty_output()))
        self.assertEqual((code, calls, report["paid_calls"]), (1, 1, 1))
        self.assertEqual(report["cases"][0]["failure_code"], "empty_extraction")

    def test_schema_failure_and_billing_unknown_stop_with_nonzero_exit(self):
        for response, usage, error, failure in (
            ("{}", True, None, "schema_validation_failed"),
            (json.dumps(default_output()), False, None, None),
            ("", False, LLMProviderError("safe timeout", code="provider_timeout"), "provider_timeout"),
        ):
            with self.subTest(failure=failure, usage=usage):
                code, report, calls = self.run_cli(response, usage=usage, error=error)
                self.assertEqual((code, calls), (1, 1))
                if failure:
                    self.assertEqual(report["cases"][0]["failure_code"], failure)
                if not usage:
                    self.assertEqual(report["cases"][0]["state"], "billing_unknown")

    def test_severe_supported_gold_error_stops_further_simulated_calls(self):
        response = copy.deepcopy(default_output())
        response["skills"][0]["level"] = leaf("Python", "技能：Python。")
        code, report, calls = self.run_cli(json.dumps(response, ensure_ascii=False))
        self.assertEqual((code, calls), (1, 1))
        self.assertGreater(report["cases"][0]["severe_errors"], 0)
        self.assertEqual(report["cases"][0]["state"], "severe_quality_error")

    def test_correct_synthetic_output_can_finish_successfully(self):
        code, report, calls = self.run_cli(json.dumps(evaluation_cases()[0]["response"], ensure_ascii=False), case_limit=1)
        self.assertEqual((code, calls), (0, 1))
        self.assertEqual(report["cases"][0]["state"], "validated")
        self.assertEqual(report["cases"][0]["precision"], 1)
        self.assertEqual(report["cases"][0]["recall"], 1)
        self.assertEqual(report["actual_calls"], 1)
        self.assertIsNone(report["actual_cost_cny"])
        self.assertEqual(report["estimated_usage_cost_cny"], "0.00001")

    def test_nan_budget_is_rejected_before_provider_or_key_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            argv = ["eval", "--authorize-paid-calls", "--authorization-record", "synthetic-owner-approval",
                    "--price-audit-date", "2026-10-12", "--max-calls", "5", "--max-budget-cny", "NaN",
                    "--input-price-per-million-cny", "2", "--output-price-per-million-cny", "8",
                    "--output", str(Path(directory) / "synthetic-ledger.json")]
            with (patch.object(evaluation, "DeepSeekProvider") as provider, patch.object(sys, "argv", argv),
                  patch.object(evaluation.getpass, "getpass") as key_prompt, redirect_stderr(StringIO())):
                with self.assertRaises(SystemExit) as raised:
                    evaluation.main()
            self.assertEqual(raised.exception.code, 2)
            provider.assert_not_called()
            key_prompt.assert_not_called()

    def test_sdk_usage_above_output_budget_is_rejected_but_metering_remains(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=5000, total_tokens=5001),
        )
        create = Mock(return_value=completion)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        provider = DeepSeekProvider(api_key="synthetic-private-key", client=client, max_output_tokens=4096)
        with self.assertRaises(LLMProviderError) as raised:
            provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
        self.assertEqual(raised.exception.code, "provider_usage_exceeded_bounds")
        self.assertEqual(raised.exception.billing_status, "usage_reported")
        self.assertEqual(provider.last_usage.total_tokens, 5001)
        self.assertEqual(create.call_count, 1)

    def test_five_protocol_cases_are_free_and_keep_independent_unknowns(self):
        with (patch.object(sys, "argv", ["eval", "--mode", "mock"]),
              patch.object(evaluation.getpass, "getpass") as key_prompt,
              patch("offerpilot_api.agent_runtime.providers.deepseek.OpenAI") as sdk,
              redirect_stdout(StringIO()) as output):
            self.assertEqual(evaluation.main(), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(tuple(row["case_id"] for row in report["cases"]), CASE_IDS)
        self.assertEqual((report["paid_calls"], report["actual_calls"], report["protocol_calls"]), (0, 0, 5))
        self.assertEqual((report["summary"]["correct"], report["summary"]["expected_nonnull_fields"]), (112, 112))
        self.assertEqual((report["summary"]["located_fields"], report["summary"]["proposed_fields"]), (127, 127))
        self.assertEqual(report["summary"]["severe_errors"], 0)
        self.assertTrue(all(row["evidence_attribution_accuracy"] == 1 for row in report["cases"]))
        self.assertTrue(all(row["attempt_count"] == 1 for row in report["cases"]))
        self.assertEqual(report["cases"][3]["unsupported_nonnull_for_unknown"], 0)
        self.assertTrue(report["test_result"])
        sdk.assert_not_called()
        key_prompt.assert_not_called()

    def test_independent_gold_detects_cross_experience_attribution(self):
        case = evaluation_cases()[2]
        response = copy.deepcopy(case["response"])
        response["projects"][0]["work_items"] = response["projects"][1]["work_items"]
        result = map_extraction_v2(StudentProfileExtractionV2.model_validate(response), case["text"])
        metrics = evaluation.quality(result, case["gold"], case["text"])
        self.assertLess(metrics["evidence_attribution_accuracy"], 1)
        self.assertGreater(metrics["false_negative_fields"], 0)
        self.assertTrue(metrics["candidate_errors"])

    def test_injection_candidate_is_recorded_as_error_without_confirmation(self):
        case = evaluation_cases()[4]
        response = copy.deepcopy(case["response"])
        attack = case["text"].splitlines()[-1]
        response["skills"].append({"name": leaf("Rust", attack), "level": None})
        result = map_extraction_v2(StudentProfileExtractionV2.model_validate(response), case["text"])
        metrics = evaluation.quality(result, case["gold"], case["text"])
        self.assertEqual(metrics["extra_candidates"], 1)
        self.assertEqual(metrics["false_positive_fields"], 1)
        self.assertEqual(metrics["severe_errors"], 0)
        self.assertEqual(metrics["candidate_errors"][0]["evidence_status"], "needs_review")

    def test_unknown_year_cannot_be_reported_as_correct_graduation_month(self):
        expected = {"candidates": [{"category": "education", "value": {
            "school": "示例大学", "degree": "本科", "major": None,
            "start_date": None, "end_date": None, "end_date_kind": None}, "evidence_status": "needs_review"}]}
        batch = CandidateBatchV1.model_validate({"schema_version": "1.0", "extraction_version": "synthetic",
            "candidates": [{"category": "education", "value": {**expected["candidates"][0]["value"], "end_date": "2026-06"}, "evidence": []}]})
        result = SimpleNamespace(batch=batch, candidate_metadata=[{"evidence_status": "needs_review", "fields": []}])
        metrics = evaluation.quality(result, expected, "")
        self.assertEqual(metrics["unsupported_nonnull_for_unknown"], 1)
        self.assertEqual(metrics["false_positive_fields"], 1)

    def test_real_gates_reject_before_key_or_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ["eval", "--mode", "real", "--authorize-paid-calls", "--authorization-record", "synthetic-owner-approval",
                    "--price-audit-date", "2026-10-12", "--max-calls", "1", "--max-budget-cny", "1",
                    "--input-price-per-million-cny", "2", "--output-price-per-million-cny", "8",
                    "--output", str(Path(directory) / "synthetic-ledger.json")]
            mutations = [
                ("--authorization-record", None), ("--price-audit-date", None), ("--max-calls", "0"),
                ("--max-budget-cny", "0.000001"), ("--input-price-per-million-cny", "Infinity"),
                ("--output-price-per-million-cny", "0"), ("--price-audit-date", "invalid"),
                ("--price-audit-date", "2026-10-11"), ("--price-audit-date", "2026-10-13"),
            ]
            for flag, value in mutations:
                argv = list(base)
                index = argv.index(flag)
                argv[index:index + 2] = [] if value is None else [flag, value]
                with (self.subTest(flag=flag, value=value), patch.object(sys, "argv", argv),
                      patch.object(evaluation, "DeepSeekProvider") as provider,
                      patch.object(evaluation.getpass, "getpass") as key_prompt,
                      redirect_stderr(StringIO())):
                    with self.assertRaises(SystemExit):
                        evaluation.main()
                    provider.assert_not_called()
                    key_prompt.assert_not_called()

    def test_confirmation_and_existing_ledger_reject_zero_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-ledger.json"
            argv = ["eval", "--authorize-paid-calls", "--authorization-record", "synthetic-owner-approval",
                    "--price-audit-date", "2026-10-12", "--max-calls", "1", "--max-budget-cny", "1",
                    "--input-price-per-million-cny", "2", "--output-price-per-million-cny", "8", "--output", str(path)]
            with (patch.object(sys, "argv", argv), patch.object(evaluation, "DeepSeekProvider") as provider,
                  patch("builtins.input", return_value="no"), patch.object(evaluation.getpass, "getpass") as key_prompt,
                  redirect_stdout(StringIO())):
                self.assertEqual(evaluation.main(), 2)
                provider.assert_not_called()
                key_prompt.assert_not_called()
            path.write_text("interrupted-ledger", encoding="utf-8")
            with (patch.object(sys, "argv", argv), patch.object(evaluation, "DeepSeekProvider") as provider,
                  patch("builtins.input") as consent, redirect_stderr(StringIO())):
                with self.assertRaises(SystemExit):
                    evaluation.main()
                provider.assert_not_called()
                consent.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), "interrupted-ledger")

    def test_whitelist_and_mode_cannot_be_bypassed(self):
        for argv in (["eval", "--case-id", "personal_resume.txt"],
                     ["eval", "--case-limit", "6"], ["eval", "--case-id", "normal_chinese", "--case-limit", "1"],
                     ["eval", "--case-id", "normal_chinese", "--case-id", "normal_chinese"],
                     ["eval", "--mode", "real"], ["eval", "--mode", "mock", "--authorize-paid-calls"]):
            with (self.subTest(argv=argv), patch.object(sys, "argv", argv),
                  patch.object(evaluation, "DeepSeekProvider") as provider, redirect_stderr(StringIO())):
                with self.assertRaises(SystemExit):
                    evaluation.main()
                provider.assert_not_called()

    def test_default_plan_is_one_case_without_provider_or_environment_key(self):
        with (patch.object(sys, "argv", ["eval"]), patch.object(evaluation, "DeepSeekProvider") as provider,
              patch.dict("os.environ", {"DEEPSEEK_API_KEY": "synthetic-environment-key"}),
              patch.object(evaluation.getpass, "getpass") as key_prompt, redirect_stdout(StringIO()) as output):
            self.assertEqual(evaluation.main(), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(len(report["planned_cases"]), 1)
        self.assertEqual(report["planned_cases"][0]["case_id"], "normal_chinese")
        self.assertEqual(report["paid_calls"], 0)
        self.assertNotIn("synthetic-environment-key", output.getvalue())
        provider.assert_not_called()
        key_prompt.assert_not_called()

    def test_hostile_model_metadata_is_never_saved(self):
        self.assertEqual(evaluation._safe_response_metadata(SimpleNamespace(last_response_model="PRIVATE_MODEL_DATA")), {"response_model": None})
        self.assertEqual(evaluation._safe_response_metadata(SimpleNamespace(last_response_model="deepseek-flash")), {"response_model": "deepseek-flash"})

    def test_pre_call_ledger_failure_never_invokes_provider(self):
        original = evaluation._write_report
        def fail_reservation(path, report, **kwargs):
            if report["reserved_calls"]:
                self.assertEqual(report["actual_calls"], 0)
                raise OSError("synthetic storage failure")
            return original(path, report, **kwargs)
        with patch.object(evaluation, "_write_report", side_effect=fail_reservation), patch.object(evaluation, "extract_student_profile_v2") as extract:
            with self.assertRaises(OSError):
                self.run_cli(json.dumps(evaluation_cases()[0]["response"]), case_limit=1)
        extract.assert_not_called()

    def test_input_rejection_does_not_count_reserved_call_as_invocation(self):
        cases = evaluation_cases()
        cases[0]["text"] = ""
        with patch.object(evaluation, "evaluation_cases", return_value=cases):
            code, report, calls = self.run_cli("{}", usage=False, case_limit=1)
        self.assertEqual((code, calls, report["reserved_calls"], report["actual_calls"], report["paid_calls"]), (1, 0, 1, 0, 0))

    def test_counted_boundary_never_exceeds_authorized_calls(self):
        provider = Mock()
        report = {"actual_calls": 0, "paid_calls": 0}
        counted = evaluation._CountedProvider(provider, report, 1)
        counted.generate_json(system_prompt="JSON", user_prompt="synthetic")
        with self.assertRaises(LLMProviderError):
            counted.generate_json(system_prompt="JSON", user_prompt="synthetic")
        self.assertEqual((provider.generate_json.call_count, report["actual_calls"], report["paid_calls"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
