"""HTTP contracts keep server-owned matching evidence out of write requests."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from offerpilot_api.job_intelligence.match_schemas import MatchEvaluation
from offerpilot_api.profile.schemas import MAX_PROFILE_REVISION


class JobMatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_revision: int = Field(ge=0, le=MAX_PROFILE_REVISION, strict=True)
    job_revision: int = Field(ge=1, le=MAX_PROFILE_REVISION, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$", strict=True)


class JobMatchResponse(BaseModel):
    id: UUID
    job_id: UUID
    profile_revision: int
    job_revision: int
    jd_version: int
    rule_version: str
    schema_version: str
    created_at: datetime
    stale: bool
    stale_reasons: list[Literal["profile_changed", "job_changed", "rule_changed"]]
    evaluation: MatchEvaluation


class JobMatchHistoryResponse(BaseModel):
    items: list[JobMatchResponse]
    total: int
