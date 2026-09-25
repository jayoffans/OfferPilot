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
