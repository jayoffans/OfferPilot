"""Safe error contracts shared by API documentation and handlers."""

from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    fields: list[str] | None = None


class ErrorResponse(BaseModel):
    # Preserve the existing upload/resource HTTPException string contract.
    detail: ErrorDetail | str


def error_responses(*status_codes: int) -> dict:
    return {code: {"model": ErrorResponse} for code in status_codes}
