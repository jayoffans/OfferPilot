"""Versioned profile values: nullable means unknown; provenance is server-authored."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from offerpilot_api.resume_ingestion.evidence import CandidateExtractionMetadata

Category = Literal["education", "skill", "project", "experience"]
# SQLAlchemy Integer is a signed 32-bit column on PostgreSQL; leave one step
# for the transactional increment and reject oversized SQLite bindings safely.
MAX_PROFILE_REVISION = 2_147_483_646
Month = Annotated[str, Field(pattern=r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$")]
ShortText = Annotated[str, Field(max_length=255, strict=True)]
LongText = Annotated[str, Field(max_length=3000, strict=True)]


class ProfileValueBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def trim_text(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def validate_draft(self):
        values = self.model_dump(exclude={"id", "source"})
        if not any(value is not None for value in values.values()):
            raise ValueError("An entry must contain at least one provided field")
        start, end = values.get("start_date"), values.get("end_date")
        if start and end and start > end:
            raise ValueError("Start month must not follow end month")
        if values.get("is_current") is True and end is not None:
            raise ValueError("Current entries cannot specify an end month")
        if values.get("end_date_kind") is not None and end is None:
            raise ValueError("Graduation kind requires a graduation month")
        return self


class EducationValue(ProfileValueBase):
    school: ShortText | None = None
    degree: ShortText | None = None
    major: ShortText | None = None
    start_date: Month | None = None
    end_date: Month | None = None
    end_date_kind: Literal["expected", "actual"] | None = None


class SkillValue(ProfileValueBase):
    name: ShortText | None = None
    level: ShortText | None = None


class ProjectValue(ProfileValueBase):
    name: ShortText | None = None
    role: ShortText | None = None
    description: LongText | None = None
    start_date: Month | None = None
    end_date: Month | None = None
    is_current: Annotated[bool, Field(strict=True)] | None = None


class ExperienceValue(ProfileValueBase):
    organization: ShortText | None = None
    role: ShortText | None = None
    experience_type: Literal["internship", "work"] | None = None
    description: LongText | None = None
    start_date: Month | None = None
    end_date: Month | None = None
    is_current: Annotated[bool, Field(strict=True)] | None = None


ProfileValue = EducationValue | SkillValue | ProjectValue | ExperienceValue
VALUE_MODELS = {"education": EducationValue, "skill": SkillValue, "project": ProjectValue, "experience": ExperienceValue}


class EntryIdentity(BaseModel):
    id: UUID | None = None


class EducationInput(EducationValue, EntryIdentity):
    pass


class SkillInput(SkillValue, EntryIdentity):
    pass


class ProjectInput(ProjectValue, EntryIdentity):
    pass


class ExperienceInput(ExperienceValue, EntryIdentity):
    pass


class CandidateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    source_text: str = Field(min_length=1, max_length=500)

    @field_validator("source_text")
    @classmethod
    def visible_evidence(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence must contain visible text")
        return value


class CurrentEvidence(CandidateEvidence):
    supports_current_value: bool


class EntrySource(BaseModel):
    kind: Literal["manual", "resume_confirmed", "user_modified", "resume_deleted"]
    resume_id: UUID | None
    resume_file_name: str | None
    field_sources: dict[str, Literal["manual", "resume", "resume_deleted"]]
    evidence: list[CurrentEvidence]
    test_result: bool = False
    provider: Literal["mock", "deepseek"] | None = None
    model: str | None = None
    run_id: UUID | None = None


class EducationEntry(EducationValue):
    id: UUID
    source: EntrySource


class SkillEntry(SkillValue):
    id: UUID
    source: EntrySource


class ProjectEntry(ProjectValue):
    id: UUID
    source: EntrySource


class ExperienceEntry(ExperienceValue):
    id: UUID
    source: EntrySource


class ProfileSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, le=MAX_PROFILE_REVISION, strict=True)
    education: list[EducationInput] = Field(max_length=20)
    skills: list[SkillInput] = Field(max_length=100)
    projects: list[ProjectInput] = Field(max_length=30)
    experiences: list[ExperienceInput] = Field(max_length=40)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [item.id for items in (self.education, self.skills, self.projects, self.experiences) for item in items if item.id]
        if len(ids) != len(set(ids)):
            raise ValueError("Entry IDs must not repeat")
        return self


class ProfileCandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, le=MAX_PROFILE_REVISION, strict=True)
    action: Literal["confirm", "modify", "reject"]
    value: ProfileValue | None = None

    @model_validator(mode="after")
    def require_explicit_modified_value(self):
        if (self.action == "modify") != (self.value is not None):
            raise ValueError("Only modify requires a user-provided value")
        return self


class CandidateEducation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["education"]
    value: EducationValue
    evidence: list[CandidateEvidence] = Field(max_length=10)


class CandidateSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["skill"]
    value: SkillValue
    evidence: list[CandidateEvidence] = Field(max_length=10)


class CandidateProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["project"]
    value: ProjectValue
    evidence: list[CandidateEvidence] = Field(max_length=10)


class CandidateExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["experience"]
    value: ExperienceValue
    evidence: list[CandidateEvidence] = Field(max_length=10)


CandidateInput = Annotated[CandidateEducation | CandidateSkill | CandidateProject | CandidateExperience, Field(discriminator="category")]


class CandidateBatchV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    extraction_version: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    candidates: list[CandidateInput] = Field(min_length=1, max_length=100)


class ProfileCandidateResponse(BaseModel):
    id: UUID
    category: Category
    value: ProfileValue
    status: Literal["pending", "confirmed", "modified", "rejected"]
    evidence_status: Literal["supported", "unsupported", "needs_review"]
    evidence: list[CandidateEvidence]
    resume_id: UUID
    resume_file_name: str
    schema_version: str
    extraction_version: str
    entry_id: UUID | None
    created_at: datetime
    reviewed_at: datetime | None
    extraction_run_id: UUID | None = None
    extraction_metadata: CandidateExtractionMetadata | None = None


class ProfileCompletion(BaseModel):
    populated_groups: list[str]
    missing_fields: list[str]
    pending_count: int


class CareerProfileResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    revision: int
    updated_at: datetime | None
    education: list[EducationEntry]
    skills: list[SkillEntry]
    projects: list[ProjectEntry]
    experiences: list[ExperienceEntry]
    candidates: list[ProfileCandidateResponse]
    completion: ProfileCompletion
