"""DeepSeek JSON Output adapter; no resume or persistence logic belongs here."""

from openai import APIError, OpenAI

from offerpilot_api.agent_runtime.llm_provider import LLMProviderError

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-flash",
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 4096,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("A DeepSeek API key is required")
        if not model or timeout_seconds <= 0 or max_output_tokens <= 0:
            raise ValueError("Invalid DeepSeek provider configuration")
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=self._max_output_tokens,
                stream=False,
            )
        except APIError:
            raise LLMProviderError("DeepSeek request failed") from None

        if not response.choices:
            raise LLMProviderError("DeepSeek returned no response")
        choice = response.choices[0]
        if choice.finish_reason != "stop" or not choice.message.content or not choice.message.content.strip():
            raise LLMProviderError("DeepSeek returned an incomplete response")
        return choice.message.content
