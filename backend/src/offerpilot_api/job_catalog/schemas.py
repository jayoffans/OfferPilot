"""Versioned job contracts; provenance and tenant ownership are server-authored."""

import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_REVISION = 2_147_483_646
Revision = Annotated[int, Field(ge=1, le=MAX_REVISION, strict=True)]
WorkType = Literal["unknown", "internship", "campus", "full_time"]
RequirementType = Literal["education", "major", "graduation", "experience", "location", "internship_duration", "technical_skill", "project", "internship_experience", "language", "other", "salary", "work_type", "responsibility"]
Priority = Literal["must", "preferred", "uncertain"]
ReviewStatus = Literal["pending", "confirmed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobRequirementInput(StrictModel):
    id: UUID | None = None
    type: RequirementType
    content: str = Field(min_length=1, max_length=2000, strict=True)
    priority: Priority
    review_status: ReviewStatus = "pending"
    evidence_text: str | None = Field(default=None, min_length=1, max_length=3000, strict=True)
    source_start: int | None = Field(default=None, ge=0, le=30000, strict=True)
    # Keeping an old snapshot binding preserves an invalidated requirement;
    # rebinding to the current JD is an explicit user review/edit action.
    source_jd_version: Revision | None = None

    @field_validator("content")
    @classmethod
    def visible_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Requirement must contain visible text")
        return value

    @model_validator(mode="after")
    def evidence_position_requires_quote(self):
        if self.source_start is not None and self.evidence_text is None:
            raise ValueError("Position requires a quote")
        if self.evidence_text is not None and not self.evidence_text.strip():
            raise ValueError("Evidence must contain visible text")
        return self


class JobRequirementResponse(JobRequirementInput):
    id: UUID
    source_jd_version: int
    source_end: int | None
    line_start: int | None
    line_end: int | None
    source_kind: Literal["rule", "manual", "user_modified"]
    stale: bool
    revision: int
    schema_version: Literal["1.0"] = "1.0"
    parser_version: str | None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None


class JobFields(StrictModel):
    title: str = Field(min_length=1, max_length=200, strict=True)
    company: str | None = Field(default=None, max_length=200, strict=True)
    location: str | None = Field(default=None, max_length=200, strict=True)
    salary: str | None = Field(default=None, max_length=200, strict=True)
    work_type: WorkType = "unknown"
    source_url: str | None = Field(default=None, max_length=2048, strict=True)
    jd_text: str = Field(default="", max_length=30000, strict=True)

    @field_validator("title", "company", "location", "salary", mode="before")
    @classmethod
    def trim_basic_text(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("source_url")
    @classmethod
    def public_http_link_only(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if re.search(r"[\x00-\x20\x7f\\]", value):
            raise ValueError("Unsafe URL characters")
        try:
            url = urlsplit(value)
            host = (url.hostname or "").rstrip(".").lower()
            if "%" in host:
                raise ValueError("Encoded host is not allowed")
            host = host.encode("idna").decode("ascii").lower()
            port = url.port
        except (ValueError, UnicodeError):
            raise ValueError("Invalid URL") from None
        if url.scheme not in {"https", "http"} or not host or url.username is not None or url.password is not None:
            raise ValueError("Only public HTTP(S) links are allowed")
        if port is not None and port not in {80, 443}:
            raise ValueError("Nonstandard ports are not allowed")
        if host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
            raise ValueError("Private host is not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Reject ambiguous browser IP notations such as 127.1 or 0x7f.0.0.1.
            if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host):
                raise ValueError("Ambiguous IP notation") from None
        else:
            if not address.is_global:
                raise ValueError("Nonpublic address is not allowed")
        return value


class JobCreateRequest(JobFields):
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    requirements: list[JobRequirementInput] | None = Field(default=None, max_length=100)


class JobSaveRequest(JobFields):
    revision: Revision
    requirements: list[JobRequirementInput] = Field(max_length=100)


class JobRevisionRequest(StrictModel):
    revision: Revision


class JobFavoriteRequest(JobRevisionRequest):
    is_favorite: bool = Field(strict=True)


class JobRequirementsSaveRequest(JobRevisionRequest):
    requirements: list[JobRequirementInput] = Field(max_length=100)


class JobRequirementWriteRequest(JobRevisionRequest):
    requirement: JobRequirementInput


class JobRequirementReviewRequest(JobRevisionRequest):
    action: Literal["confirm", "reopen"]


class JobReviewSummary(StrictModel):
    total: int
    pending: int
    confirmed: int
    stale: int


class JobListItem(StrictModel):
    id: UUID
    title: str
    company: str | None
    location: str | None
    salary: str | None
    work_type: WorkType
    source_url: str | None
    source_kind: Literal["pasted", "manual"]
    revision: int
    jd_version: int
    is_favorite: bool
    created_at: datetime
    updated_at: datetime
    review_summary: JobReviewSummary


class JobDetailResponse(JobListItem):
    schema_version: Literal["1.0"] = "1.0"
    jd_text: str
    requirements: list[JobRequirementResponse]
    parser_version: str
    jd_snapshots: list["JobJDSnapshotResponse"]


class JobJDSnapshotResponse(StrictModel):
    version: int
    jd_text: str
    created_at: datetime


class JobListResponse(StrictModel):
    items: list[JobListItem]
    total: int
    page: int
    page_size: int


class JobParsePreviewRequest(StrictModel):
    jd_text: str = Field(min_length=1, max_length=30000, strict=True)


class JobParsePreviewResponse(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    parser_version: str
    requirements: list[JobRequirementInput]
    warnings: list[str]
