"""No network: SDK faults, hostile metadata, deadlines and paid CLI gates."""
import json
import re
import sys
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import redirect_stdout, redirect_stderr
from datetime import date
from io import StringIO
import httpx
from openai import APIStatusError, APITimeoutError, APIConnectionError
from offerpilot_api.agent_runtime.providers.deepseek import DeepSeekProvider, _BoundedStream
from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from backend.evaluations import evaluate_deepseek_v2 as evaluation
from backend.evaluations.deepseek_v2_cases import evaluation_cases


class DeepSeekPaidBoundaries(unittest.TestCase):
    def setUp(self):
        network = patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("These tests prohibit external HTTP"))
        network.start()
        self.addCleanup(network.stop)

    def fake(self, response=None, error=None):
        create = Mock(return_value=response, side_effect=error)
        return DeepSeekProvider(api_key="synthetic-private-key", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))), create

    def test_http_matrix_has_safe_codes_no_sdk_retry(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        for status, code in ((401, "provider_unauthorized"), (402, "provider_insufficient_balance"), (429, "provider_rate_limited"), (400, "provider_invalid_request"), (422, "provider_invalid_request"), (500, "provider_server_error")):
            with self.subTest(status=status):
                error = APIStatusError("PRIVATE KEY AND RESUME", response=httpx.Response(status, request=request), body={"secret": "private"})
                provider, create = self.fake(error=error)
                with self.assertRaises(LLMProviderError) as raised:
                    provider.generate_json(system_prompt="private", user_prompt="private")
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertEqual(create.call_count, 1)
        for error, code in ((APITimeoutError(request=request), "provider_timeout"), (APIConnectionError(request=request), "provider_connection_error")):
            provider, create = self.fake(error=error)
            with self.assertRaises(LLMProviderError) as raised:
                provider.generate_json(system_prompt="private", user_prompt="private")
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(raised.exception.billing_status, "unknown")
            self.assertEqual(create.call_count, 1)

    def test_json_nonthinking_official_endpoint_zero_retries_and_no_redirect(self):
        with patch("offerpilot_api.agent_runtime.providers.deepseek.OpenAI") as constructor:
            provider = DeepSeekProvider(api_key="synthetic-private-key")
            args = constructor.call_args.kwargs
            self.assertEqual(args["base_url"], "https://api.deepseek.com")
            self.assertEqual(args["max_retries"], 0)
            self.assertFalse(args["http_client"].follow_redirects)
            args["http_client"].close()
        completion = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}"))])
        provider, create = self.fake(completion)
        provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
        args = create.call_args.kwargs
        self.assertEqual(args["model"], "deepseek-flash")
        self.assertEqual(args["response_format"], {"type": "json_object"})
        self.assertEqual(args["extra_body"], {"thinking": {"type": "disabled"}})

    def test_truncated_empty_hostile_metadata_and_token_validation(self):
        for reason, content, code in (("length", "{}", "provider_truncated_response"), ("aborted", "{}", "provider_truncated_response"), ("stop", "", "provider_empty_response")):
            provider, _ = self.fake(SimpleNamespace(model="synthetic-private-key", choices=[SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content))], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)))
            with self.assertLogs("offerpilot_api.agent_runtime.providers.deepseek", level="INFO") as logs:
                with self.assertRaises(LLMProviderError) as raised:
                    provider.generate_json(system_prompt="private", user_prompt="private")
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(provider.last_usage.total_tokens, 30)
            self.assertNotIn("synthetic-private-key", str(logs.output))
        provider, _ = self.fake(SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=-1, completion_tokens=20, total_tokens=19)))
        with self.assertRaises(LLMProviderError):
            provider.generate_json(system_prompt="JSON", user_prompt="synthetic")
        self.assertIsNone(provider.last_usage)

    def test_response_wall_deadline_and_byte_budget(self):
        request = httpx.Request("POST", "https://api.deepseek.com")
        stream = _BoundedStream(httpx.ByteStream(b"x"), deadline=0, request=request)
        with self.assertRaises(httpx.ReadTimeout): list(stream)
        stream = _BoundedStream(httpx.ByteStream(b"x" * 1_000_001), deadline=float("inf"), request=request)
        with self.assertRaises(httpx.ReadError): list(stream)

    def test_eval_default_dryrun_and_missing_budget_cannot_create_provider(self):
        with (patch.object(evaluation, "DeepSeekProvider") as provider,
              patch.object(evaluation, "read_api_key") as key_prompt,
              patch.object(sys, "argv", ["eval"]), redirect_stdout(StringIO())):
            self.assertEqual(evaluation.main(), 0)
        provider.assert_not_called()
        key_prompt.assert_not_called()
        with (patch.object(evaluation, "DeepSeekProvider") as provider,
              patch.object(evaluation, "read_api_key") as key_prompt,
              patch.object(sys, "argv", ["eval", "--authorize-paid-calls"]), redirect_stderr(StringIO())):
            with self.assertRaises(SystemExit): evaluation.main()
        provider.assert_not_called()
        key_prompt.assert_not_called()

    def test_authorized_eval_stops_after_valid_output_without_usage(self):
        provider = Mock(last_usage=None)
        provider.generate_json.return_value = json.dumps(evaluation_cases()[0]["response"], ensure_ascii=False)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "synthetic-ledger.json"
            argv = ["eval", "--mode", "real", "--authorize-paid-calls", "--authorization-record", "synthetic-owner-approval",
                    "--price-audit-date", "2026-10-12", "--case-id", "normal_chinese", "--max-calls", "1",
                    "--max-budget-cny", "1", "--input-price-per-million-cny", "2",
                    "--output-price-per-million-cny", "8", "--output", str(path)]
            output = StringIO()
            def consent(prompt):
                if "SHA256" in prompt:
                    return re.findall(r'"sent_text_sha256": "([a-f0-9]{64})"', output.getvalue())[-1]
                return "同意付费评测"
            with (patch.object(evaluation, "_execution_date", return_value=date(2026, 10, 12)),
                  patch.object(evaluation, "DeepSeekProvider", return_value=provider),
                  patch.object(sys, "argv", argv), patch("builtins.input", side_effect=consent),
                  patch.object(evaluation, "read_api_key", return_value="synthetic-private-key"), redirect_stdout(output)):
                self.assertEqual(evaluation.main(), 1)
            text = path.read_text(encoding="utf-8")
            report = json.loads(text)
            row = report["cases"][0]
            self.assertEqual((report["paid_calls"], report["actual_calls"], row["state"]), (1, 1, "billing_unknown"))
            self.assertEqual(row["run"]["billing_status"], "unknown")
            self.assertIsNone(row["token_usage"])
            self.assertIsNone(report["actual_cost_cny"])
            self.assertTrue(row["preview_matches_actual_provider_input"])
            self.assertEqual(row["run"]["max_calls"], 1)
            self.assertEqual(len(row["candidates"]), 5)
            self.assertTrue(all(item["status"] == "pending" for item in row["candidates"]))
            self.assertTrue(row["unconfirmed_candidates_not_in_profile"])
            self.assertNotIn("synthetic-private-key", text + output.getvalue())
        self.assertEqual(provider.generate_json.call_count, 1)
        provider.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
