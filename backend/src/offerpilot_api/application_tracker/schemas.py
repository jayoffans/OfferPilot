"""Explicit manual application contracts, with date-only deadlines preserved."""

import re
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from offerpilot_api.job_catalog.schemas import Revision

ApplicationStatus = Literal["preparing", "submitted", "assessment", "interview", "offer", "rejected", "withdrawn", "closed"]
ApplicationSort = Literal["updated_desc", "created_desc", "submitted_desc", "next_action_asc"]
EventType = Literal["application_created", "status_changed", "notes_changed", "next_action_changed", "submitted_at_changed"]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$", strict=True)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _safe_text(value: str) -> str:
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]", value):
        raise ValueError("Text contains unsupported control characters")
    return value


class NextActionInput(StrictModel):
    title: str = Field(min_length=1, max_length=300, strict=True)
    notes: str = Field(default="", max_length=2000, strict=True)
    due_date: date | None = None
    due_time: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", strict=True)
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    status: Literal["pending", "completed", "cancelled"] = "pending"

    @field_validator("title")
    @classmethod
    def visible_title(cls, value):
        value = _safe_text(value).strip()
        if not value:
            raise ValueError("An action title is required")
        return value

    @field_validator("notes")
    @classmethod
    def safe_notes(cls, value):
        return _safe_text(value)

    @field_validator("due_date", mode="before")
    @classmethod
    def explicit_date(cls, value):
        if value is not None and not (type(value) is date or isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise ValueError("Use an explicit calendar date")
        return value

    @model_validator(mode="after")
    def time_requires_date(self):
        if self.due_time is not None and self.due_date is None:
            raise ValueError("A specific time requires a calendar date")
        return self


class ApplicationCreateRequest(StrictModel):
    job_id: UUID
    job_revision: Revision
    idempotency_key: IdempotencyKey
    reapply: bool = Field(default=False, strict=True)
    match_id: UUID | None = None


class ApplicationUpdateRequest(StrictModel):
    revision: Revision
    idempotency_key: IdempotencyKey
    status: ApplicationStatus
    submitted_at: AwareDatetime | None
    notes: str = Field(max_length=10000, strict=True)
    next_action: NextActionInput | None
    occurred_at: AwareDatetime | None = None

    @field_validator("notes")
    @classmethod
    def safe_notes(cls, value):
        return _safe_text(value)

    @field_validator("submitted_at", "occurred_at", mode="before")
    @classmethod
    def explicit_datetime(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Use an explicit datetime with a UTC offset")
        return value

    @field_validator("submitted_at", "occurred_at")
    @classmethod
    def utc_instants(cls, value):
        if value is None:
            return None
        try:
            return value.astimezone(timezone.utc)
        except OverflowError:
            # A valid local year 0001/9999 can fall outside datetime's range
            # after applying its offset. Keep this a safe field error (422).
            raise ValueError("The UTC datetime is outside the supported range") from None


class ApplicationDeleteRequest(StrictModel):
    revision: Revision
    idempotency_key: IdempotencyKey
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Deletion must be explicitly confirmed")
        return value


class ApplicationMatchReference(StrictModel):
    id: UUID
    created_at: datetime
    profile_revision: int
    job_revision: int
    jd_version: int
    rule_version: str
    stale: bool
    stale_reasons: list[Literal["profile_changed", "job_changed", "rule_changed"]]


class ApplicationResponse(StrictModel):
    id: UUID
    job_id: UUID
    job_title: str
    company: str | None
    location: str | None
    attempt_number: int
    status: ApplicationStatus
    revision: int
    submitted_at: datetime | None
    notes: str
    next_action: NextActionInput | None
    created_at: datetime
    updated_at: datetime
    is_overdue: bool
    match: ApplicationMatchReference | None


class ApplicationListResponse(StrictModel):
    items: list[ApplicationResponse]
    total: int
    page: int
    page_size: int


class ApplicationEventChanges(StrictModel):
    before: Any
    after: Any


class ApplicationEventResponse(StrictModel):
    id: UUID
    type: EventType
    revision: int
    changes: ApplicationEventChanges
    occurred_at: datetime
    recorded_at: datetime


class ApplicationEventListResponse(StrictModel):
    items: list[ApplicationEventResponse]
    total: int
    page: int
    page_size: int
