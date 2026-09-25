"""In-memory extraction contract checks with simulated model responses."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.agent_runtime.providers.deepseek import DeepSeekProvider
from offerpilot_api.resume_ingestion.extractor import ExtractionError, extract_student_profile


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

        profile = extract_student_profile(RESUME_TEXT, provider)

        self.assertEqual(profile.name.value, "测试学生")
        self.assertEqual(profile.education[0].value, "示例大学")
        self.assertEqual(profile.skills[0].source_text, "技能：Python")
        self.assertEqual(profile.projects[0].confidence, 0.85)
        self.assertEqual(provider.calls, 1)
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

        with self.assertRaises(ExtractionError):
            extract_student_profile(RESUME_TEXT, provider)

        self.assertEqual(provider.calls, 2)

    def test_confidence_outside_zero_to_one_fails(self) -> None:
        for confidence in (-0.01, 1.01):
            with self.subTest(confidence=confidence):
                provider = FakeProvider(candidate_json(confidence=confidence))

                with self.assertRaises(ExtractionError):
                    extract_student_profile(RESUME_TEXT, provider)

                self.assertEqual(provider.calls, 2)

    def test_unsupported_evidence_fails(self) -> None:
        response = json.loads(candidate_json())
        response["skills"][0]["source_text"] = "技能：Java"
        provider = FakeProvider(json.dumps(response, ensure_ascii=False))

        with self.assertRaises(ExtractionError):
            extract_student_profile(RESUME_TEXT, provider)

        self.assertEqual(provider.calls, 2)

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


if __name__ == "__main__":
    unittest.main()
