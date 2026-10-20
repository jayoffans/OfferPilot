"""User-scoped resume access, including profile and fact ownership chains."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from offerpilot_api.db.models import Profile, ProfileFact, ResumeDocument
from offerpilot_api.services.resume import ResumeUploadError, normalize_pdf_filename


class ResumeAccessError(Exception):
    def __init__(self, detail: str, status_code: int = 404) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _require_owner(user_id: UUID) -> None:
    # Type hints alone cannot prevent a future caller from querying NULL-owned
    # legacy records. Fail before constructing any ownership query.
    if not isinstance(user_id, UUID):
        raise ResumeAccessError("Authenticated owner required", 401)


def list_owned_resumes(db: Session, *, user_id: UUID) -> list[ResumeDocument]:
    _require_owner(user_id)
    return list(
        db.scalars(
            select(ResumeDocument)
            .where(
                ResumeDocument.user_id == user_id,
                ResumeDocument.lifecycle_status == "active",
                ResumeDocument.processing_status == "parsed",
            )
            .order_by(ResumeDocument.created_at.desc(), ResumeDocument.id.desc())
        )
    )


def get_owned_resume(db: Session, *, resume_id: UUID, user_id: UUID) -> ResumeDocument:
    _require_owner(user_id)
    document = db.scalar(
        select(ResumeDocument).where(
            ResumeDocument.id == resume_id,
            ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status == "active",
            ResumeDocument.processing_status == "parsed",
        )
    )
    if document is None:
        raise ResumeAccessError("Resume resource not found")
    return document


def get_owned_profile(db: Session, *, resume_id: UUID, user_id: UUID) -> Profile:
    _require_owner(user_id)
    profile = db.scalar(
        select(Profile)
        .join(ResumeDocument, Profile.resume_id == ResumeDocument.id)
        .where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status == "active", ResumeDocument.processing_status == "parsed",
        )
    )
    if profile is None:
        raise ResumeAccessError("Resume resource not found")
    return profile


def list_owned_profile_facts(
    db: Session, *, resume_id: UUID, user_id: UUID
) -> list[ProfileFact]:
    # Distinguish an owned profile with no facts from an inaccessible parent.
    get_owned_profile(db, resume_id=resume_id, user_id=user_id)
    return list(
        db.scalars(
            select(ProfileFact)
            .join(Profile, ProfileFact.profile_id == Profile.id)
            .join(ResumeDocument, Profile.resume_id == ResumeDocument.id)
            .where(
                ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
                ResumeDocument.lifecycle_status == "active", ResumeDocument.processing_status == "parsed",
            )
            .order_by(ProfileFact.created_at, ProfileFact.id)
        )
    )


def rename_owned_resume(
    db: Session, *, resume_id: UUID, user_id: UUID, file_name: str
) -> ResumeDocument:
    _require_owner(user_id)
    try:
        normalized_name = normalize_pdf_filename(file_name)
    except ResumeUploadError:
        raise ResumeAccessError("File name must be a valid .pdf basename", 422) from None
    try:
        changed = db.execute(update(ResumeDocument).where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status == "active",
            ResumeDocument.processing_status == "parsed",
        ).values(file_name=normalized_name).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            db.rollback()
            raise ResumeAccessError("Resume resource not found")
        document = db.get(ResumeDocument, resume_id, populate_existing=True)
        # Return the authorized snapshot while the write lock is still held;
        # a deletion immediately after commit cannot turn refresh into a 500.
        db.expunge(document)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise ResumeAccessError("Could not update resume metadata", 500) from None
    return document
