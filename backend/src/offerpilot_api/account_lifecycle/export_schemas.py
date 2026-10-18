"""Explicit account export API contracts; no credentials leave this boundary."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from offerpilot_api.identity.schemas import Password

EXPORT_SCHEMA_VERSION = "offerpilot-user-export/1.0"


class ExportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    password: Password = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ExportStatus(BaseModel):
    id: UUID
    status: Literal["queued", "running", "ready", "failed", "expired", "cancelled"]
    schema_version: Literal["offerpilot-user-export/1.0"] = EXPORT_SCHEMA_VERSION
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None
    byte_size: int | None
    sha256: str | None
    error_code: str | None
    attempt_count: int
    download_available: bool

    @field_validator("created_at", "updated_at", "completed_at", "expires_at")
    @classmethod
    def explicit_timezone(cls, value):
        from datetime import timezone
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
