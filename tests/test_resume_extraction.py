"""In-memory extraction contract checks with simulated model responses."""

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openai import BadRequestError

from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.agent_runtime.providers.deepseek import DeepSeekProvider
from offerpilot_api.resume_ingestion.extractor import (
    ExtractionDiagnostics,
    ExtractionError,
    extract_student_profile,
)

EVALUATION_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "evaluations"
    / "evaluate_resume_extraction.py"
)
EVALUATION_SPEC = spec_from_file_location("offerpilot_evaluation_script", EVALUATION_SCRIPT_PATH)
if EVALUATION_SPEC is None or EVALUATION_SPEC.loader is None:
    raise RuntimeError("Could not load the resume evaluation script for tests")
evaluate_resume_extraction = module_from_spec(EVALUATION_SPEC)
EVALUATION_SPEC.loader.exec_module(evaluate_resume_extraction)


class FakeProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.system_prompt = ""
        self.user_prompt = ""

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


class FailingProvider:
    def __init__(self, unsafe_message: str = "synthetic provider failure") -> None:
        self.calls = 0
        self.unsafe_message = unsafe_message

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        raise LLMProviderError(
            self.unsafe_message,
            http_status_code=400,
            provider_error_type="BadRequestError",
            provider_error_message=(
                "DeepSeek rejected the request format; provider details were withheld."
            ),
        )


def candidate_json(*, confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "name": {"value": "测试学生", "source_text": "测试学生", "confidence": confidence},
            "education": [
                {"value": "示例大学", "source_text": "毕业于示例大学", "confidence": 0.8}
            ],
            "skills": [{"value": "Python", "source_text": "技能：Python", "confidence": 0.95}],
            "projects": [
                {"value": "选课系统", "source_text": "课程项目：选课系统", "confidence": 0.85}
            ],
        },
        ensure_ascii=False,
    )


RESUME_TEXT = "测试学生\n毕业于示例大学\n技能：Python\n课程项目：选课系统"


class ResumeExtractionTests(unittest.TestCase):
    def test_valid_json_produces_evidenced_candidates(self) -> None:
        provider = FakeProvider(candidate_json())
        diagnostics = ExtractionDiagnostics()

        profile = extract_student_profile(RESUME_TEXT, provider, diagnostics=diagnostics)

        self.assertEqual(profile.name.value, "测试学生")
        self.assertEqual(profile.education[0].value, "示例大学")
        self.assertEqual(profile.skills[0].source_text, "技能：Python")
        self.assertEqual(profile.projects[0].confidence, 0.85)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(diagnostics.to_dict()["llm_call_statuses"], ["success"])
        self.assertEqual(diagnostics.retry_count, 0)
        self.assertIn("不可信数据", provider.system_prompt)
        self.assertIn("resume_text", provider.user_prompt)

    def test_missing_required_field_fails_after_bounded_retry(self) -> None:
        response = json.loads(candidate_json())
        del response["education"]
        provider = FakeProvider(json.dumps(response, ensure_ascii=False))

        with self.assertRaises(ExtractionError):
            extract_student_profile(RESUME_TEXT, provider)

        self.assertEqual(provider.calls, 2)

    def test_missing_source_text_on_a_skill_fails(self) -> None:
        response = json.loads(candidate_json())
        del response["skills"][0]["source_text"]
        provider = FakeProvider(json.dumps(response, ensure_ascii=False))
        diagnostics = ExtractionDiagnostics()

        with self.assertRaises(ExtractionError):
            extract_student_profile(RESUME_TEXT, provider, diagnostics=diagnostics)

        self.assertEqual(provider.calls, 2)
        self.assertIn(
            "skills[0].source_text",
            diagnostics.to_dict()["schema_validation_error_paths"],
        )

    def test_confidence_outside_zero_to_one_fails(self) -> None:
        for confidence in (-0.01, 1.01):
            with self.subTest(confidence=confidence):
                provider = FakeProvider(candidate_json(confidence=confidence))
                diagnostics = ExtractionDiagnostics()

                with self.assertRaises(ExtractionError):
                    extract_student_profile(RESUME_TEXT, provider, diagnostics=diagnostics)

                self.assertEqual(provider.calls, 2)
                self.assertIn(
                    "name.confidence",
                    diagnostics.to_dict()["schema_validation_error_paths"],
                )
                self.assertIn(
                    "name.confidence",
                    diagnostics.to_dict()["confidence_out_of_range_fields"],
                )
                self.assertEqual(diagnostics.retry_count, 1)

    def test_unsupported_evidence_fails(self) -> None:
        response = json.loads(candidate_json())
        response["skills"][0]["source_text"] = "技能：Java"
        provider = FakeProvider(json.dumps(response, ensure_ascii=False))
        diagnostics = ExtractionDiagnostics()

        with self.assertRaises(ExtractionError):
            extract_student_profile(RESUME_TEXT, provider, diagnostics=diagnostics)

        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            diagnostics.to_dict()["evidence_validation_failed_fields"], ["skills[0]"]
        )
        self.assertEqual(diagnostics.failure_reason, "source_text_not_in_resume")

    def test_llm_failure_status_and_retry_count_are_reported(self) -> None:
        provider = FailingProvider()
        diagnostics = ExtractionDiagnostics()

        with self.assertRaises(ExtractionError):
            extract_student_profile(RESUME_TEXT, provider, diagnostics=diagnostics)

        result = diagnostics.to_dict()
        self.assertEqual(result["llm_call_statuses"], ["failure", "failure"])
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(result["failure_reason"], "llm_call_failed")
        self.assertEqual(result["attempts"][0]["http_status_code"], 400)
        self.assertEqual(result["attempts"][0]["provider_error_type"], "BadRequestError")

    def test_deepseek_adapter_redacts_api_error_message(self) -> None:
        private_resume_marker = "PRIVATE_RESUME_MARKER"
        private_key_marker = "sk-test-secret-marker"
        sdk_error = BadRequestError(
            f"Error code: 400 - {private_resume_marker} {private_key_marker}",
            response=SimpleNamespace(status_code=400, headers={}, request=object()),
            body={
                "type": "invalid_request_error",
                "message": f"{private_resume_marker} {private_key_marker}",
            },
        )
        create = Mock(side_effect=sdk_error)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        provider = DeepSeekProvider(api_key="synthetic-test-key", client=client)

        with self.assertRaises(LLMProviderError) as raised:
            provider.generate_json(system_prompt="private system prompt", user_prompt=private_resume_marker)

        error = raised.exception
        self.assertEqual(error.http_status_code, 400)
        self.assertEqual(error.provider_error_type, "BadRequestError")
        self.assertIn("request format", error.provider_error_message)
        self.assertNotIn(private_resume_marker, str(error))
        self.assertNotIn(private_key_marker, str(error))
        self.assertNotIn(private_resume_marker, error.provider_error_message)
        self.assertNotIn(private_key_marker, error.provider_error_message)

    def test_evaluation_cli_reports_sanitized_llm_error_details(self) -> None:
        private_resume_marker = "PRIVATE_RESUME_MARKER"
        private_key_marker = "synthetic-test-key"
        provider = FailingProvider(f"{private_resume_marker} {private_key_marker}")
        stdout = StringIO()
        stderr = StringIO()

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "resume.pdf"
            pdf_path.write_bytes(b"synthetic pdf placeholder")
            with (
                patch.object(sys, "argv", ["evaluate_resume_extraction.py", str(pdf_path)]),
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": private_key_marker}),
                patch.object(
                    evaluate_resume_extraction,
                    "_extract_pdf_text",
                    return_value=private_resume_marker,
                ),
                patch.object(
                    evaluate_resume_extraction,
                    "DeepSeekProvider",
                    return_value=provider,
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = evaluate_resume_extraction.main()

        output = stderr.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn('"http_status_code": 400', output)
        self.assertIn('"provider_error_type": "BadRequestError"', output)
        self.assertIn("request format", output)
        self.assertNotIn(private_resume_marker, output)
        self.assertNotIn(private_key_marker, output)
        self.assertEqual(stdout.getvalue(), "")

    def test_evaluation_cli_reports_redacted_schema_failure(self) -> None:
        private_response_marker = "MODEL_PRIVATE_RESPONSE_MARKER"
        provider = FakeProvider(json.dumps({"unexpected": private_response_marker}))
        stdout = StringIO()
        stderr = StringIO()

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "resume.pdf"
            pdf_path.write_bytes(b"synthetic pdf placeholder")
            with (
                patch.object(sys, "argv", ["evaluate_resume_extraction.py", str(pdf_path)]),
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-test-key"}),
                patch.object(
                    evaluate_resume_extraction,
                    "_extract_pdf_text",
                    return_value=RESUME_TEXT,
                ),
                patch.object(
                    evaluate_resume_extraction,
                    "DeepSeekProvider",
                    return_value=provider,
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = evaluate_resume_extraction.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("schema_validation_failed", stderr.getvalue())
        self.assertIn("schema_version", stderr.getvalue())
        self.assertIn('"retry_count": 1', stderr.getvalue())
        self.assertNotIn("synthetic-test-key", stderr.getvalue())
        self.assertNotIn(private_response_marker, stderr.getvalue())
        self.assertNotIn(RESUME_TEXT, stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_invalid_first_response_can_recover_once(self) -> None:
        provider = FakeProvider("{}", candidate_json())

        profile = extract_student_profile(RESUME_TEXT, provider)

        self.assertEqual(profile.schema_version, "1.0")
        self.assertEqual(provider.calls, 2)

    def test_deepseek_adapter_requests_json_without_live_network(self) -> None:
        completion = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=candidate_json()),
                )
            ]
        )
        create = Mock(return_value=completion)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        provider = DeepSeekProvider(api_key="synthetic-test-key", client=client)

        result = provider.generate_json(system_prompt="JSON only", user_prompt="synthetic resume")

        self.assertEqual(result, candidate_json())
        self.assertEqual(create.call_args.kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(create.call_args.kwargs["max_tokens"], 4096)
        self.assertEqual(
            create.call_args.kwargs["extra_body"], {"thinking": {"type": "disabled"}}
        )

    def test_deepseek_adapter_rejects_truncated_response(self) -> None:
        completion = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="length",
                    message=SimpleNamespace(content=candidate_json()),
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=completion)))
        )
        provider = DeepSeekProvider(api_key="synthetic-test-key", client=client)

        with self.assertRaises(LLMProviderError):
            provider.generate_json(system_prompt="JSON only", user_prompt="synthetic resume")

    def test_deepseek_response_log_contains_only_safe_metadata(self) -> None:
        private_marker = "PRIVATE_RESUME_AND_KEY_MARKER"
        completion = SimpleNamespace(
            model="deepseek-chat",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=private_marker),
                )
            ],
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=completion)))
        )
        provider = DeepSeekProvider(api_key=private_marker, client=client)

        with self.assertLogs(
            "offerpilot_api.agent_runtime.providers.deepseek", level="INFO"
        ) as captured:
            result = provider.generate_json(
                system_prompt=private_marker, user_prompt=private_marker
            )

        self.assertEqual(result, private_marker)
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(
            captured.records[0].getMessage(),
            "DeepSeek response choices_count=1 finish_reason=stop response_model=deepseek-chat",
        )
        self.assertNotIn(private_marker, captured.output[0])

    def test_deepseek_empty_choices_log_is_safe_and_existing_error_remains(self) -> None:
        completion = SimpleNamespace(model="deepseek-chat", choices=[])
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=completion)))
        )
        provider = DeepSeekProvider(api_key="synthetic-test-key", client=client)

        with self.assertLogs(
            "offerpilot_api.agent_runtime.providers.deepseek", level="INFO"
        ) as captured:
            with self.assertRaisesRegex(LLMProviderError, "DeepSeek returned no response"):
                provider.generate_json(system_prompt="private prompt", user_prompt="private resume")

        self.assertEqual(
            captured.records[0].getMessage(),
            "DeepSeek response choices_count=0 finish_reason=unknown response_model=deepseek-chat",
        )
        self.assertNotIn("private", captured.output[0])


if __name__ == "__main__":
    unittest.main()
