"""Versioned, evidence-carrying deterministic match result contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MatchStatus = Literal["met", "partial", "unknown", "unmet"]
MatchConclusion = Literal["hard_conflict", "verify_first", "consider"]
MatchGroup = Literal["education", "skills", "experience", "work_conditions", "preferences"]


class MatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileMatchEvidence(MatchModel):
    entry_id: UUID | None
    category: Literal["education", "skill", "project", "experience", "preference"]
    field: str
    value: str
    source_kind: str
    resume_id: UUID | None = None
    source_text: str | None = None


class JDMatchEvidence(MatchModel):
    evidence_text: str | None = None
    source_jd_version: int
    source_start: int | None = None
    source_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    source_kind: Literal["rule", "manual", "user_modified", "job_field"]


class MatchItem(MatchModel):
    requirement_id: UUID | None
    type: str
    content: str
    priority: Literal["must", "preferred", "uncertain"]
    group: MatchGroup
    scope: Literal["qualification", "preference"] = "qualification"
    status: MatchStatus
    reason: str
    profile_evidence: list[ProfileMatchEvidence] = Field(default_factory=list)
    jd_evidence: JDMatchEvidence
    rule_version: str
    verification: list[str] = Field(default_factory=list)


class ExcludedRequirement(MatchModel):
    requirement_id: UUID
    content: str
    reason: Literal["pending_review", "stale_jd"]


class MatchEvaluation(MatchModel):
    schema_version: Literal["1.0"] = "1.0"
    rule_version: str
    conclusion: MatchConclusion
    reasons: list[str]
    items: list[MatchItem]
    excluded_requirements: list[ExcludedRequirement]
    next_steps: list[str]
