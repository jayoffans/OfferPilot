"""DeepSeek JSON Output adapter; no resume or persistence logic belongs here."""

import logging
import re

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI

from offerpilot_api.agent_runtime.llm_provider import LLMProviderError

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
logger = logging.getLogger(__name__)
_SAFE_RESPONSE_LABEL = re.compile(r"[A-Za-z0-9._/-]{1,100}\Z")

_SAFE_HTTP_ERROR_MESSAGES = {
    400: "DeepSeek rejected the request format; provider details were withheld.",
    401: "DeepSeek authentication failed; provider details were withheld.",
    402: "DeepSeek reported insufficient account balance; provider details were withheld.",
    422: "DeepSeek rejected request parameters; provider details were withheld.",
    429: "DeepSeek rate limit was reached; provider details were withheld.",
    500: "DeepSeek returned a server error; provider details were withheld.",
    503: "DeepSeek service was overloaded; provider details were withheld.",
}


def _safe_api_error_metadata(error: APIError) -> tuple[int | None, str, str]:
    status = getattr(error, "status_code", None)
    status_code = status if type(status) is int and 100 <= status <= 599 else None
    provider_error_type = type(error).__name__

    if status_code in _SAFE_HTTP_ERROR_MESSAGES:
        safe_message = _SAFE_HTTP_ERROR_MESSAGES[status_code]
    elif status_code is not None:
        safe_message = (
            f"DeepSeek returned HTTP {status_code}; provider details were withheld."
        )
    elif isinstance(error, APITimeoutError):
        safe_message = "DeepSeek request timed out; no HTTP status was received."
    elif isinstance(error, APIConnectionError):
        safe_message = "Could not connect to DeepSeek; no HTTP status was received."
    else:
        safe_message = "DeepSeek API request failed; provider details were withheld."

    return status_code, provider_error_type, safe_message


def _safe_response_label(value: object) -> str:
    """Keep untrusted response metadata from injecting arbitrary text into logs."""

    if isinstance(value, str) and _SAFE_RESPONSE_LABEL.fullmatch(value):
        return value
    return "unknown"


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
                extra_body={"thinking": {"type": "disabled"}},
            )
        except APIError as exc:
            status_code, error_type, safe_message = _safe_api_error_metadata(exc)
            raise LLMProviderError(
                "DeepSeek request failed",
                http_status_code=status_code,
                provider_error_type=error_type,
                provider_error_message=safe_message,
            ) from None

        choices_count = len(response.choices) if response.choices else 0
        logger.info(
            "DeepSeek response choices_count=%d finish_reason=%s response_model=%s",
            choices_count,
            (
                _safe_response_label(response.choices[0].finish_reason)
                if choices_count
                else "unknown"
            ),
            _safe_response_label(getattr(response, "model", None)),
        )
        if not response.choices:
            raise LLMProviderError("DeepSeek returned no response")
        choice = response.choices[0]
        if choice.finish_reason != "stop" or not choice.message.content or not choice.message.content.strip():
            raise LLMProviderError("DeepSeek returned an incomplete response")
        return choice.message.content
