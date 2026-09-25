"""Provider-neutral contract for JSON-generating language models."""

from typing import Protocol


class LLMProviderError(Exception):
    """A model call failed or produced no complete JSON response."""


class LLMProvider(Protocol):
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return one complete JSON object as text, or raise LLMProviderError."""
        ...
