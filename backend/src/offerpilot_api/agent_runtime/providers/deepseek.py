"""DeepSeek JSON Output adapter; no resume or persistence logic belongs here."""

import json
import logging
import re
import time
import httpx

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, DefaultHttpxClient

from offerpilot_api.agent_runtime.llm_provider import LLMProviderError, ProviderUsage

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
logger = logging.getLogger(__name__)
_SAFE_RESPONSE_MODEL = re.compile(
    r"deepseek-(?:flash|v[0-9]+(?:\.[0-9]+)?-(?:flash|pro)(?:-[0-9]{4})?(?:-preview)?)\Z",
    re.IGNORECASE,
)


class _BoundedStream(httpx.SyncByteStream):
    def __init__(self, stream, *, deadline, request):
        self.stream, self.deadline, self.request = stream, deadline, request

    def __iter__(self):
        size = 0
        for chunk in self.stream:
            size += len(chunk)
            if time.monotonic() > self.deadline:
                raise httpx.ReadTimeout("Provider response deadline exceeded", request=self.request)
            if size > 1_000_000:
                raise httpx.ReadError("Provider response exceeds bounds", request=self.request)
            yield chunk

    def close(self):
        self.stream.close()


class _BoundedTransport(httpx.BaseTransport):
    def __init__(self):
        self.transport = httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request):
        deadline = time.monotonic() + 40
        response = self.transport.handle_request(request)
        # Reject unexpected compression before HTTPX can decode an unbounded
        # body. The 1 MB stream cap then measures uncompressed response bytes.
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding not in ("", "identity"):
            response.close()
            raise httpx.ReadError("Provider returned unsupported content encoding", request=request)
        response.stream = _BoundedStream(response.stream, deadline=deadline, request=request)
        return response

    def close(self):
        self.transport.close()

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


def _safe_response_model(value: object) -> str | None:
    """Record known model naming shapes, never arbitrary provider response text."""
    if isinstance(value, str) and len(value) <= 100 and _SAFE_RESPONSE_MODEL.fullmatch(value):
        return value
    return None


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
        self.last_usage = None
        self.last_response_model = None
        self._max_output_tokens = max_output_tokens
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=timeout_seconds,
            max_retries=0,
            http_client=DefaultHttpxClient(
                follow_redirects=False, trust_env=False,
                headers={"Accept-Encoding": "identity"}, transport=_BoundedTransport(),
            ),
        )
        for name in ("openai", "openai._base_client", "httpx", "httpcore"):
            logging.getLogger(name).disabled = True
        for name in list(logging.root.manager.loggerDict):
            if name.startswith(("openai.", "httpx.", "httpcore.")):
                logging.getLogger(name).disabled = True

    def close(self):
        if hasattr(self._client, "close"):
            self._client.close()

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.last_usage = None
        self.last_response_model = None
        raw_usage = None
        raw_response_available = False
        try:
            parameters = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": self._max_output_tokens,
                "stream": False,
                "extra_body": {"thinking": {"type": "disabled"}},
            }
            completions = self._client.chat.completions
            raw_api = getattr(completions, "with_raw_response", None)
            if raw_api is not None:
                raw_response = raw_api.create(**parameters)
                payload = raw_response.http_response.json()
                raw_usage = payload.get("usage") if isinstance(payload, dict) else None
                response = raw_response.parse()
                raw_response_available = True
            else:
                # Existing in-memory protocol stubs have no raw HTTP envelope.
                response = completions.create(**parameters)
        except APIError as exc:
            status_code, error_type, safe_message = _safe_api_error_metadata(exc)
            raise LLMProviderError(
                "DeepSeek request failed",
                http_status_code=status_code,
                provider_error_type=error_type,
                provider_error_message=safe_message,
                code=({401: "provider_unauthorized", 402: "provider_insufficient_balance", 429: "provider_rate_limited",
                       400: "provider_invalid_request", 422: "provider_invalid_request"}.get(status_code)
                      or ("provider_timeout" if isinstance(exc, APITimeoutError) else
                          "provider_connection_error" if isinstance(exc, APIConnectionError) else "provider_server_error")),
            ) from None
        except json.JSONDecodeError:
            # A 200 response can still contain invalid JSON. It may already be
            # billable, so fail once with no raw response or implicit repair.
            raise LLMProviderError(
                "DeepSeek returned an invalid response", code="provider_invalid_response"
            ) from None

        self.last_response_model = _safe_response_model(getattr(response, "model", None))

        names = ("prompt_tokens", "completion_tokens", "total_tokens")
        if raw_response_available:
            # SDK model parsing coerces strings, booleans and floats into ints.
            # Validate the original wire values before treating them as usage.
            values = tuple(raw_usage.get(name) if isinstance(raw_usage, dict) else None for name in names)
        else:
            usage = getattr(response, "usage", None)
            values = tuple(getattr(usage, name, None) for name in names)
        if all(type(value) is int and 0 <= value <= 10_000_000 for value in values) and values[2] == values[0] + values[1]:
            self.last_usage = ProviderUsage(*values)
            if self.last_usage.completion_tokens > self._max_output_tokens:
                # Metering must survive a rejected response. The already sent
                # request may have incurred charges; never retry it here.
                raise LLMProviderError(
                    "DeepSeek reported usage beyond the authorized output limit",
                    code="provider_usage_exceeded_bounds",
                    billing_status="usage_reported",
                ) from None
        logger.info("DeepSeek response received")
        choices = getattr(response, "choices", None)
        if choices is None or choices == []:
            raise LLMProviderError("DeepSeek returned no response", code="provider_empty_response")
        if not isinstance(choices, list) or len(choices) != 1:
            raise LLMProviderError("DeepSeek returned an invalid response", code="provider_invalid_response")
        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        content = getattr(getattr(choice, "message", None), "content", None)
        if finish_reason != "stop":
            raise LLMProviderError("DeepSeek returned an incomplete response", code="provider_truncated_response")
        if content is None or content == "":
            raise LLMProviderError("DeepSeek returned no response", code="provider_empty_response")
        if not isinstance(content, str):
            raise LLMProviderError("DeepSeek returned an invalid response", code="provider_invalid_response")
        if not content.strip():
            raise LLMProviderError("DeepSeek returned no response", code="provider_empty_response")
        return content
