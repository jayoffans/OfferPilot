"""Resume intelligence persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, String, Text, Uuid, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from offerpilot_api.db.base import Base


class ResumeDocument(Base):
    __tablename__ = "resume_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    file_type: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)

    profile: Mapped[Profile | None] = relationship(
        back_populates="resume_document",
        cascade="all, delete-orphan",
        single_parent=True,
    )


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
