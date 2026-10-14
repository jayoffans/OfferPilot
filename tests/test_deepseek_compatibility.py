"""Official SDK protocol checks through an in-memory HTTP transport; never network."""

import copy
import gzip
import json
import unittest
from contextlib import contextmanager
from importlib.resources import files
from unittest.mock import patch

import httpx

from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.agent_runtime.providers.deepseek import DeepSeekProvider
from offerpilot_api.resume_ingestion.extraction_schema import StudentProfileExtractionV2
from offerpilot_api.resume_ingestion.extractor import ExtractionDiagnostics, ExtractionError, PROMPT_PACKAGE
from offerpilot_api.resume_ingestion.extractor_v2 import extract_student_profile_v2
from offerpilot_api.resume_ingestion.synthetic_fixtures import fixture_cases, fixture_response


def completion(content="{}", **changes):
    result = {
        "id": "930c60df-bf64-41c9-a88e-3ec75f81e00e", "object": "chat.completion",
        "created": 1705651092, "model": "deepseek-flash",
        "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 16, "completion_tokens": 10, "total_tokens": 26,
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 16,
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }
    result.update(changes)
    return result


@contextmanager
def protocol_provider(handler):
    """Use the production OpenAI construction while replacing the socket transport."""
    requests = []

    def respond(request):
        requests.append(request)
        return handler(request)

    with patch("offerpilot_api.agent_runtime.providers.deepseek.httpx.HTTPTransport",
               return_value=httpx.MockTransport(respond)) as transport_constructor:
        provider = DeepSeekProvider(api_key="synthetic-private-key", timeout_seconds=10)
        try:
            yield provider, requests, transport_constructor
        finally:
            provider.close()


class DeepSeekCompatibilityTests(unittest.TestCase):
    def test_official_sdk_wire_payload_and_timeout_use_production_configuration(self):
        with protocol_provider(lambda request: httpx.Response(200, json=completion())) as (provider, requests, transport):
            self.assertEqual(provider.generate_json(system_prompt="JSON only", user_prompt="合成文本"), "{}")
            self.assertEqual(len(requests), 1)
            request = requests[0]
            self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertEqual((body["stream"], body["max_tokens"]), (False, 4096))
            self.assertNotIn("extra_body", body)
            self.assertNotIn("tools", body)
            self.assertEqual(body["messages"][1]["content"], "合成文本")
            self.assertEqual(request.extensions["timeout"], {"connect": 10, "read": 10, "write": 10, "pool": 10})
            self.assertEqual(request.headers["x-stainless-retry-count"], "0")
            self.assertEqual(request.headers["accept-encoding"], "identity")
            transport.assert_called_once_with(retries=0, trust_env=False)
            self.assertFalse(provider._client._client.follow_redirects)
            self.assertEqual(provider._client.max_retries, 0)

    def test_sdk_parses_official_usage_extensions_without_losing_metering(self):
        with protocol_provider(lambda request: httpx.Response(200, json=completion())) as (provider, _, _):
            provider.generate_json(system_prompt="JSON only", user_prompt="synthetic")
            self.assertEqual((provider.last_usage.prompt_tokens, provider.last_usage.completion_tokens,
                              provider.last_usage.total_tokens), (16, 10, 26))
            self.assertEqual(provider.last_response_model, "deepseek-flash")

    def test_official_nonstreaming_keepalive_blank_lines_do_not_break_json(self):
        payload = b"\n\r\n\n" + json.dumps(completion()).encode()
        with protocol_provider(lambda request: httpx.Response(200, content=payload,
                               headers={"content-type": "application/json"})) as (provider, requests, _):
            self.assertEqual(provider.generate_json(system_prompt="JSON", user_prompt="synthetic"), "{}")
            self.assertEqual((len(requests), provider.last_usage.total_tokens), (1, 26))

    def test_unexpected_compression_fails_before_decoding_without_resending(self):
        # Encoded data fits the byte cap while decoded output exceeds it.
        encoded = gzip.compress(b" " * 1_000_001)
        for encoding in ("gzip", "deflate", "br", "unrecognized"):
            with self.subTest(encoding=encoding):
                with protocol_provider(lambda request: httpx.Response(200, stream=httpx.ByteStream(encoded),
                                       headers={"content-encoding": encoding})) as (provider, requests, _):
                    with self.assertRaises(LLMProviderError) as caught:
                        provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(caught.exception.billing_status, "unknown")
                    self.assertIsNone(provider.last_usage)

    def test_response_model_is_allowlisted_and_cleared_before_a_failed_call(self):
        for model, expected in (("deepseek-flash", "deepseek-flash"), ("DeepSeek-V4.1-Flash", "DeepSeek-V4.1-Flash"),
                                ("DeepSeek-V4-Pro-0813", "DeepSeek-V4-Pro-0813"), ("private-resume-key", None),
                                ("deepseek-flash\nprivate", None), ("deepseek-flash/private", None)):
            with self.subTest(model=model):
                responses = [httpx.Response(200, json=completion(model=model)), httpx.Response(401, json={"error": "private"})]
                with protocol_provider(lambda request: responses.pop(0)) as (provider, requests, _):
                    provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
                    self.assertEqual(provider.last_response_model, expected)
                    with self.assertRaises(LLMProviderError):
                        provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
                    self.assertIsNone(provider.last_response_model)
                    self.assertIsNone(provider.last_usage)
                    self.assertEqual(len(requests), 2)

    def test_official_sdk_does_not_retry_retryable_statuses_or_follow_redirects(self):
        for status in (302, 401, 402, 408, 409, 429, 500, 503):
            with self.subTest(status=status):
                with protocol_provider(lambda request: httpx.Response(status, json={"error": "private resume"},
                                       headers={"retry-after": "0", "location": "http://127.0.0.1/private"})) as (provider, requests, _):
                    with self.assertRaises(LLMProviderError) as caught:
                        provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(caught.exception.billing_status, "unknown")
                    self.assertNotIn("private", str(caught.exception))

    def test_official_sdk_does_not_resend_after_transport_timeout_or_disconnect(self):
        for error_type, expected in ((httpx.ReadTimeout, "provider_timeout"), (httpx.ConnectError, "provider_connection_error")):
            with self.subTest(error_type=error_type):
                def fail(request):
                    raise error_type("private transport data", request=request)
                with protocol_provider(fail) as (provider, requests, _):
                    with self.assertRaises(LLMProviderError) as caught:
                        provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
                    self.assertEqual((caught.exception.code, len(requests)), (expected, 1))
                    self.assertNotIn("private", str(caught.exception))

    def test_malformed_200_responses_fail_safely_once_and_preserve_valid_usage(self):
        invalid_choices = ("private response", ["private response"],
                           [{"finish_reason": "stop", "message": None}],
                           [{"finish_reason": "stop", "message": {"content": ["private response"]}}],
                           [{"finish_reason": "stop", "message": {"content": {"private": "response"}}}],
                           completion()["choices"] * 2)
        for choices in invalid_choices:
            with self.subTest(choices_type=type(choices).__name__):
                with protocol_provider(lambda request: httpx.Response(200, json=completion(choices=choices))) as (provider, requests, _):
                    with self.assertRaises(LLMProviderError) as caught:
                        provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(provider.last_usage.total_tokens, 26)
                    self.assertNotIn("private", str(caught.exception))

    def test_invalid_http_json_or_html_is_a_safe_error_without_repair_call(self):
        for content_type, content in (("application/json", b"{private broken JSON"), ("text/html", b"private HTML")):
            with self.subTest(content_type=content_type):
                with protocol_provider(lambda request: httpx.Response(200, content=content,
                                       headers={"content-type": content_type})) as (provider, requests, _):
                    with self.assertRaises(LLMProviderError) as caught:
                        provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
                    self.assertEqual(len(requests), 1)
                    self.assertNotIn("private", str(caught.exception))
                    self.assertIsNone(provider.last_usage)

    def test_sdk_usage_wrong_types_or_inconsistent_totals_are_not_accepted(self):
        for values in (("16", 10, 26), (True, 10, 11), (16, 10, 25), (-1, 10, 9), (16, 10.0, 26)):
            with self.subTest(values=values):
                body = completion(usage=dict(zip(("prompt_tokens", "completion_tokens", "total_tokens"), values)))
                with protocol_provider(lambda request: httpx.Response(200, json=body)) as (provider, requests, _):
                    provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
                    self.assertIsNone(provider.last_usage)
                    self.assertEqual(len(requests), 1)

    def test_official_sdk_v2_extraction_retains_pending_evidence_and_exact_model_input(self):
        case = fixture_cases()[0]
        # This is the final user-preview text; only the envelope is added by the extractor.
        model_input = "****\n" + case["text"]
        with protocol_provider(lambda request: httpx.Response(200, json=completion(fixture_response(case)))) as (provider, requests, _):
            result = extract_student_profile_v2(case["text"], provider, max_attempts=1, model_input_text=model_input)
            self.assertEqual(len(result.batch.candidates), 7)
            self.assertEqual(len(requests), 1)
            body = json.loads(requests[0].content)
            user_json = body["messages"][1]["content"].split("\n", 1)[1]
            self.assertEqual(json.loads(user_json)["resume_text"], model_input)
            self.assertTrue(all(item["source_text_sha256"] for item in result.candidate_metadata))
            self.assertIn("negative_planned_or_uncertain_context", result.candidate_metadata[3]["review_reasons"])

    def test_v2_schema_faults_after_sdk_response_never_trigger_a_second_call(self):
        case = fixture_cases()[0]
        bad_leaf = copy.deepcopy(case["response"])
        del bad_leaf["skills"][0]["name"]["assertion"]
        extra = copy.deepcopy(case["response"])
        extra["auto_confirm"] = True
        responses = ("{invalid JSON", "{}", json.dumps(bad_leaf), json.dumps(extra),
                     json.dumps({"schema_version": "1.0", "name": None, "education": [], "skills": [], "projects": []}))
        for response in responses:
            with self.subTest(response_length=len(response)):
                with protocol_provider(lambda request: httpx.Response(200, json=completion(response))) as (provider, requests, _):
                    diagnostics = ExtractionDiagnostics()
                    with self.assertRaises(ExtractionError):
                        extract_student_profile_v2(case["text"], provider, max_attempts=1, diagnostics=diagnostics)
                    self.assertEqual((len(requests), diagnostics.retry_count), (1, 0))
                    self.assertEqual(provider.last_usage.total_tokens, 26)

    def test_full_prompt_example_obeys_strict_v2_and_fixed_prompt_budget(self):
        prompt = files(PROMPT_PACKAGE).joinpath("resume_extract_v2.txt").read_text(encoding="utf-8")
        example = prompt.split("EXAMPLE JSON OUTPUT:\n", 1)[1]
        profile = StudentProfileExtractionV2.model_validate_json(example)
        self.assertEqual((len(profile.education), len(profile.skills), len(profile.projects), len(profile.experiences)), (1, 1, 1, 1))
        self.assertLess(len(prompt.encode("utf-8")), 16000)
        self.assertIn("不得复制样例", prompt)


if __name__ == "__main__":
    unittest.main()
