"""Provider-neutral contract for JSON-generating language models."""

from typing import Protocol


class LLMProviderError(Exception):
    """A model call failed or produced no complete JSON response."""

    def __init__(
        self,
        message: str,
        *,
        http_status_code: int | None = None,
        provider_error_type: str | None = None,
        provider_error_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status_code = http_status_code
        self.provider_error_type = provider_error_type
        self.provider_error_message = provider_error_message


class LLMProvider(Protocol):
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return one complete JSON object as text, or raise LLMProviderError."""
        ...
