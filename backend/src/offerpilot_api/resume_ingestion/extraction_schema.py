"""Versioned candidate schema for the first resume extraction slice."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvidenceField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=500)
    source_text: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("value", "source_text")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence fields must contain visible text")
        return value


class StudentProfileExtractionV1(BaseModel):
    """Unconfirmed profile candidates; all four top-level fields are required."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    name: EvidenceField | None
    education: list[EvidenceField] = Field(max_length=20)
    skills: list[EvidenceField] = Field(max_length=50)
    projects: list[EvidenceField] = Field(max_length=20)


class ExtractionV2Base(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceFieldV2(ExtractionV2Base):
    """Raw model proposal. Confidence is reported metadata, never a probability."""

    value: str = Field(min_length=1, max_length=500)
    source_text: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    assertion: Literal["affirmed", "negated", "planned", "uncertain"]

    @field_validator("value", "source_text")
    @classmethod
    def visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence fields must contain visible text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Evidence fields must be valid UTF-8") from None
        return value


class ScopedExtractionV2(ExtractionV2Base):
    # Models quote a scope, never authoritative page numbers or offsets.
    scope_text: str = Field(min_length=1, max_length=3000)


class EducationExtractionV2(ScopedExtractionV2):
    school: EvidenceFieldV2 | None
    degree: EvidenceFieldV2 | None
    major: EvidenceFieldV2 | None
    start_date: EvidenceFieldV2 | None
    end_date: EvidenceFieldV2 | None
    graduation_date: EvidenceFieldV2 | None
    graduation_status: EvidenceFieldV2 | None


class SkillExtractionV2(ExtractionV2Base):
    name: EvidenceFieldV2
    level: EvidenceFieldV2 | None


class ProjectExtractionV2(ScopedExtractionV2):
    name: EvidenceFieldV2 | None
    role: EvidenceFieldV2 | None
    start_date: EvidenceFieldV2 | None
    end_date: EvidenceFieldV2 | None
    is_current: EvidenceFieldV2 | None
    technology_stack: list[EvidenceFieldV2] = Field(max_length=20)
    work_items: list[EvidenceFieldV2] = Field(max_length=20)


class ExperienceExtractionV2(ScopedExtractionV2):
    organization: EvidenceFieldV2 | None
    role: EvidenceFieldV2 | None
    experience_type: EvidenceFieldV2 | None
    start_date: EvidenceFieldV2 | None
    end_date: EvidenceFieldV2 | None
    is_current: EvidenceFieldV2 | None
    responsibilities: list[EvidenceFieldV2] = Field(max_length=20)
    outcomes: list[EvidenceFieldV2] = Field(max_length=20)


class StudentProfileExtractionV2(ExtractionV2Base):
    """All keys required; missing facts use null or empty arrays."""

    schema_version: Literal["2.0"]
    education: list[EducationExtractionV2] = Field(max_length=20)
    skills: list[SkillExtractionV2] = Field(max_length=50)
    projects: list[ProjectExtractionV2] = Field(max_length=20)
    experiences: list[ExperienceExtractionV2] = Field(max_length=20)
