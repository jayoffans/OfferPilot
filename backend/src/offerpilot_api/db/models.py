"""Identity and resume intelligence persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, Uuid, false, func, text, true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from offerpilot_api.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email_normalized", name="uq_users_email_normalized"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)


class RegistrationInvitation(Base):
    __tablename__ = "registration_invitations"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_registration_invitations_token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthThrottle(Base):
    __tablename__ = "auth_throttles"
    __table_args__ = (CheckConstraint("attempt_count >= 0", name="ck_auth_throttles_attempt_count"),)

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)


class ResumeDocument(Base):
    __tablename__ = "resume_documents"
    __table_args__ = (
        Index("uq_resume_documents_object_key", "object_key", unique=True),
        CheckConstraint("byte_size >= 0", name="ck_resume_documents_byte_size"),
        CheckConstraint(
            "processing_status IN ('uploading', 'uploaded', 'parsed', 'failed')",
            name="ck_resume_documents_processing_status",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'deleting', 'delete_failed')",
            name="ck_resume_documents_lifecycle_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_resume_documents_user_id_users", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    file_type: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Server parser offsets into raw_text; no model-authored page coordinates.
    text_layout: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(24), default="parsed", server_default=text("'parsed'"), nullable=False
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(24), default="active", server_default=text("'active'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)

    profile: Mapped[Profile | None] = relationship(
        back_populates="resume_document",
        cascade="all, delete-orphan",
        single_parent=True,
    )


class ResumeFileOperation(Base):
    """Minimal cleanup/idempotency record that survives deletion of the resume."""

    __tablename__ = "resume_file_operations"
    __table_args__ = (
        CheckConstraint("reason IN ('upload', 'delete')", name="ck_resume_file_operations_reason"),
        CheckConstraint(
            "status IN ('uploading', 'deleting', 'delete_failed', 'deleted')",
            name="ck_resume_file_operations_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_resume_file_operations_attempt_count"),
    )

    # Deliberately no resume FK: retain the tombstone after hard deletion.
    resume_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    object_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    legacy_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("resume_id", name="uq_profiles_resume_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resume_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    education: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    projects: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    internships: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)

    resume_document: Mapped[ResumeDocument] = relationship(back_populates="profile")
    facts: Mapped[list[ProfileFact]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
    )


class ProfileFact(Base):
    __tablename__ = "profile_facts"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_profile_facts_confidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="facts")


class CareerProfile(Base):
    """The sole current, user-reviewed career profile; legacy Profile is a candidate snapshot."""

    __tablename__ = "career_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_career_profiles_user_id"),
        CheckConstraint("revision >= 0", name="ck_career_profiles_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0", server_default=text("'1.0'"), nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default=text("'{}'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)


class CareerProfileEntry(Base):
    __tablename__ = "career_profile_entries"
    __table_args__ = (
        CheckConstraint("category IN ('education', 'skill', 'project', 'experience')", name="ck_career_profile_entries_category"),
        CheckConstraint("source_kind IN ('manual', 'resume_confirmed', 'user_modified', 'resume_deleted')", name="ck_career_profile_entries_source_kind"),
        CheckConstraint("position >= 0", name="ck_career_profile_entries_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    resume_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("resume_documents.id", ondelete="SET NULL"), nullable=True, index=True)
    field_sources: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default=text("'{}'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)


class ProfileCandidate(Base):
    """Untrusted extraction proposals never queried as formal profile entries."""

    __tablename__ = "profile_candidates"
    __table_args__ = (
        UniqueConstraint("resume_id", "batch_key", "item_index", name="uq_profile_candidates_batch_item"),
        CheckConstraint("category IN ('education', 'skill', 'project', 'experience')", name="ck_profile_candidates_category"),
        CheckConstraint("status IN ('pending', 'confirmed', 'modified', 'rejected')", name="ck_profile_candidates_status"),
        CheckConstraint("evidence_status IN ('supported', 'unsupported', 'needs_review')", name="ck_profile_candidates_evidence_status"),
        CheckConstraint("item_index >= 0", name="ck_profile_candidates_item_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    resume_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("resume_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_key: Mapped[str] = mapped_column(String(100), nullable=False)
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default=text("'pending'"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(100), nullable=False)
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default=text("'{}'"), nullable=False)
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("resume_extraction_runs.id", name="fk_profile_candidates_extraction_run_id_runs", ondelete="SET NULL"), nullable=True, index=True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("career_profile_entries.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResumeExtractionRun(Base):
    """Durable, bounded Mock extraction; a minimal tombstone survives deletion.

    No resume FK deliberately: deleting a resume cancels active runs, clears
    derived evidence, and leaves only an owner-scoped terminal status.
    """

    __tablename__ = "resume_extraction_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_resume_extraction_runs_request"),
        UniqueConstraint("resume_id", "source_version", "extraction_version", name="uq_resume_extraction_runs_source"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_resume_extraction_runs_status"),
        CheckConstraint("(mode = 'mock' AND test_result = true) OR (mode = 'deepseek' AND test_result = false AND max_calls = 1 AND max_attempts = 1 AND session_id IS NOT NULL AND key_reference IS NOT NULL AND authorization_id IS NOT NULL AND sent_text_sha256 IS NOT NULL AND consent_version IS NOT NULL AND model = 'deepseek-flash')", name="ck_resume_extraction_runs_provider"),
        CheckConstraint("billing_status IN ('not_sent', 'unknown', 'usage_reported')", name="ck_resume_extraction_runs_billing"),
        CheckConstraint("(prompt_tokens IS NULL OR prompt_tokens >= 0) AND (completion_tokens IS NULL OR completion_tokens >= 0) AND (total_tokens IS NULL OR total_tokens >= 0)", name="ck_resume_extraction_runs_tokens"),
        CheckConstraint("revision >= 0 AND attempt_count >= 0 AND attempt_count <= max_attempts", name="ck_resume_extraction_runs_attempts"),
        CheckConstraint("call_count >= 0 AND call_count <= max_calls AND max_attempts BETWEEN 1 AND 2 AND max_calls BETWEEN 1 AND 4", name="ck_resume_extraction_runs_calls"),
        CheckConstraint("candidate_count >= 0 AND timeout_seconds BETWEEN 1 AND 60", name="ck_resume_extraction_runs_limits"),
        CheckConstraint("(status = 'running' AND execution_token IS NOT NULL AND lease_deadline IS NOT NULL) OR (status <> 'running' AND execution_token IS NULL AND lease_deadline IS NULL)", name="ck_resume_extraction_runs_lease"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    resume_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default=text("'queued'"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="mock", server_default=text("'mock'"), nullable=False)
    test_result: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    execution_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # Opaque references only. The credential and preview live in the API's
    # bounded in-memory vault, never this business database or task messages.
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    key_reference: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    authorization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    consent_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sent_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    billing_status: Mapped[str] = mapped_column(String(24), default="not_sent", server_default=text("'not_sent'"), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SavedJob(Base):
    """A private, user-provided job; never a public recruitment catalog."""

    __tablename__ = "saved_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "content_digest", name="uq_saved_jobs_owner_content"),
        CheckConstraint("revision >= 1 AND jd_version >= 1", name="ck_saved_jobs_versions"),
        CheckConstraint("work_type IN ('unknown', 'internship', 'campus', 'full_time')", name="ck_saved_jobs_work_type"),
        CheckConstraint("source_kind IN ('pasted', 'manual')", name="ck_saved_jobs_source_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    salary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    work_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    jd_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


class JobJDSnapshot(Base):
    __tablename__ = "job_jd_snapshots"
    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_job_jd_snapshots_version"),
        CheckConstraint("version >= 1", name="ck_job_jd_snapshots_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


class JobRequirement(Base):
    __tablename__ = "job_requirements"
    __table_args__ = (
        CheckConstraint("type IN ('education', 'major', 'graduation', 'experience', 'location', 'internship_duration', 'technical_skill', 'project', 'internship_experience', 'language', 'other', 'salary', 'work_type', 'responsibility')", name="ck_job_requirements_type"),
        CheckConstraint("priority IN ('must', 'preferred', 'uncertain')", name="ck_job_requirements_priority"),
        CheckConstraint("review_status IN ('pending', 'confirmed')", name="ck_job_requirements_review_status"),
        CheckConstraint("source_kind IN ('rule', 'manual', 'user_modified')", name="ck_job_requirements_source_kind"),
        CheckConstraint("position >= 0 AND revision >= 1", name="ck_job_requirements_versions"),
        CheckConstraint("(evidence_text IS NULL AND source_start IS NULL AND source_end IS NULL) OR (evidence_text IS NOT NULL AND source_start IS NOT NULL AND source_end IS NOT NULL AND source_start >= 0 AND source_end > source_start)", name="ck_job_requirements_evidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("job_jd_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobCreateReceipt(Base):
    """Minimal request digest/tombstone prevents replay recreating a deleted job."""

    __tablename__ = "job_create_receipts"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_job_create_receipts_request"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("saved_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


class JobMatch(Base):
    """Immutable evaluation for one owner-scoped pair of reviewed revisions."""

    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", "profile_revision", "job_revision", "rule_version", name="uq_job_matches_basis"),
        CheckConstraint("profile_revision >= 1 AND job_revision >= 1 AND jd_version >= 1", name="ck_job_matches_versions"),
        Index("ix_job_matches_owner_job_created", "user_id", "job_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    job_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    jd_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())


class JobMatchReceipt(Base):
    """Request hash only; historical replays never become the latest result."""

    __tablename__ = "job_match_receipts"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_job_match_receipts_request"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    match_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("job_matches.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())
