"""No-network provider for explicitly identified, fixed synthetic resumes only."""

import json

from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.resume_ingestion.synthetic_fixtures import fixture_cases, fixture_response


class MockResumeProvider:
    provider_name = "mock"
    model_name = "synthetic-fixture-v2"

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        try:
            text = json.loads(user_prompt[user_prompt.index("{"):])["resume_text"]
        except (ValueError, KeyError, TypeError):
            raise LLMProviderError("Mock input is invalid") from None
        normalized = "".join(text.split())
        # Only healthy, distinct-text fixtures are available through the API.
        for case in fixture_cases()[:5]:
            if normalized == "".join(case["text"].split()):
                return fixture_response(case)
        error = LLMProviderError("No approved synthetic fixture matches this resume")
        error.code = "mock_fixture_not_found"
        raise error
