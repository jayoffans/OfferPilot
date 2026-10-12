"""Explicit HTTP contracts for the opt-in synthetic Mock workflow."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from offerpilot_api.profile.schemas import ProfileCandidateResponse


class ExtractionRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    source_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["mock"]


class ExtractionRunMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, le=2_147_483_646, strict=True)


class ExtractionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    resume_id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    mode: Literal["mock"]
    test_result: Literal[True]
    revision: int
    source_version: str
    attempt_count: int
    max_attempts: int
    call_count: int
    max_calls: int
    candidate_count: int
    schema_version: str
    extraction_version: str
    prompt_version: str
    provider_version: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class ExtractionCapabilityResponse(BaseModel):
    enabled: bool
    mode: Literal["mock", "disabled"]
    test_result: bool
    source_version: str
    recent_runs: list[ExtractionRunResponse]


class ExtractionRunCandidatesResponse(BaseModel):
    run: ExtractionRunResponse
    candidates: list[ProfileCandidateResponse]
