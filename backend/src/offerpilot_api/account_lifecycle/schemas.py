from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from offerpilot_api.identity.schemas import Password


class AccountDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    password: Password = Field(min_length=1, max_length=128)
    confirmation: Literal["注销账户"]
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class AccountDeletionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: Literal["pending", "processing", "failed", "manual_review", "completed"]
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None
    attempt_count: int

    @field_validator("created_at", "completed_at")
    @classmethod
    def aware(cls, value):
        return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
