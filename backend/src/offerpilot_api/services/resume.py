"""Validate, store and extract text from uploaded PDF resumes."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymupdf
from fastapi import UploadFile
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from offerpilot_api.db.models import ResumeDocument, ResumeFileOperation
from offerpilot_api.resume_ingestion.lifecycle import (
    ResumeLifecycleError, abort_resume_upload, complete_resume_upload, lock_active_upload,
)
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, ResumeStorage, StorageError

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_TEXT_CHARS = 1_000_000


class ResumeUploadError(Exception):
    def __init__(self, detail: str, status_code: int = 422, *, code: str | None = None, resume_id: uuid.UUID | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code
        self.resume_id = resume_id


@dataclass(frozen=True)
class StoredResume:
    id: uuid.UUID
    file_name: str
    file_type: str
    created_at: datetime


def normalize_pdf_filename(file_name: str) -> str:
    """Keep a display basename; never use the submitted name as a storage path."""

    file_name = file_name.replace("\\", "/").split("/")[-1]
    if (
        not file_name
        or len(file_name) > 255
        or any(ord(char) < 32 or ord(char) == 127 for char in file_name)
        or Path(file_name).suffix.lower() != ".pdf"
    ):
        raise ResumeUploadError("Only .pdf files are accepted", 415)
    try:
        file_name.encode("utf-8")
    except UnicodeEncodeError:
        raise ResumeUploadError("File name must contain valid Unicode", 415) from None
    return file_name


def _validated_filename(file: UploadFile) -> str:
    file_name = normalize_pdf_filename(file.filename or "")
    if file.content_type != "application/pdf":
        raise ResumeUploadError("File content type must be application/pdf", 415)
    return file_name


def _extract_pdf_text(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise ResumeUploadError("File content is not a PDF", 422)
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if not pdf.is_pdf:
                raise ResumeUploadError("File content is not a PDF")
            if pdf.needs_pass:
                raise ResumeUploadError("Password-protected PDFs are not supported")
            if pdf.page_count > MAX_PDF_PAGES:
                raise ResumeUploadError("PDF exceeds the 50-page limit", 413)
            pages: list[str] = []
            char_count = 0
            for page in pdf:
                text = page.get_text("text", sort=True)
                char_count += len(text)
                if char_count > MAX_TEXT_CHARS:
                    raise ResumeUploadError("Extracted PDF text exceeds the limit", 413)
                pages.append(text)
    except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
        raise ResumeUploadError("PDF could not be parsed") from exc

    raw_text = "\n".join(pages).strip()
    if not raw_text:
        raise ResumeUploadError("PDF contains no selectable text")
    return raw_text


def save_resume_document(
    file: UploadFile,
    db: Session,
    upload_dir: Path,
    *,
    user_id: uuid.UUID,
    storage: ResumeStorage | None = None,
) -> StoredResume:
    """Journal before writing, then commit verified content or compensate.

    A process interruption leaves the initial durable upload row/journal. File
    I/O and completion hold a DB write lock so cleanup cannot race a late save.
    """

    if not isinstance(user_id, uuid.UUID):
        raise ResumeUploadError("An authenticated resume owner is required", 401)
    file_name = _validated_filename(file)
    storage = storage if storage is not None else LocalResumeStorage(upload_dir, max_bytes=MAX_PDF_BYTES)
    resume_id = uuid.uuid4()
    object_key = f"{uuid.uuid4().hex}.pdf"
    try:
        document = ResumeDocument(
            id=resume_id, user_id=user_id, file_name=file_name, file_path="",
            file_type="application/pdf", object_key=object_key,
            processing_status="uploading", lifecycle_status="active",
        )
        db.add(document)
        db.add(ResumeFileOperation(
            resume_id=resume_id, user_id=user_id, object_key=object_key,
            reason="upload", status="uploading",
        ))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        # No file operation occurred before the durable journal commit.
        # A connection error may also mean the commit succeeded but its
        # acknowledgement was lost. Keep the generated ID queryable either way.
        raise ResumeUploadError("Could not confirm the upload start", 503, code="resume_upload_pending", resume_id=resume_id) from None

    try:
        lock_active_upload(db, resume_id=resume_id, user_id=user_id)
        stored = storage.save(object_key, file.file, claim_token=resume_id.hex)
        db.execute(update(ResumeDocument).where(ResumeDocument.id == resume_id).values(processing_status="uploaded"))
        raw_text = _extract_pdf_text(storage.read(object_key, claim_token=resume_id.hex))
        complete_resume_upload(
            db, resume_id=resume_id, user_id=user_id, raw_text=raw_text,
            byte_size=stored.byte_size, sha256=stored.sha256,
        )
        db.refresh(document)
        result = StoredResume(
            id=document.id,
            file_name=document.file_name,
            file_type=document.file_type,
            created_at=document.created_at,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        if isinstance(exc, StorageError) and exc.code == "object_exists":
            # Never compensate a collided key by deleting somebody else's
            # pre-existing object. Keep a visible, manually recoverable journal.
            try:
                db.execute(update(ResumeDocument).where(
                    ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
                ).values(processing_status="failed", lifecycle_status="delete_failed", raw_text=None))
                db.execute(update(ResumeFileOperation).where(
                    ResumeFileOperation.resume_id == resume_id, ResumeFileOperation.user_id == user_id,
                ).values(status="delete_failed", error_code="object_key_collision"))
                db.commit()
            except SQLAlchemyError:
                db.rollback()
            raise ResumeUploadError("Storage reference conflict; contact the maintainer", 503, code="resume_cleanup_pending", resume_id=resume_id) from None
        try:
            aborted = abort_resume_upload(db, resume_id=resume_id, user_id=user_id, storage=storage)
            if not aborted:
                # Final commit acknowledgement can fail after a real commit.
                # Never compensate a parsed resource or invite a blind retry.
                confirmed = db.scalar(select(ResumeDocument).where(
                    ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
                    ResumeDocument.lifecycle_status == "active",
                    ResumeDocument.processing_status == "parsed",
                ))
                if confirmed is not None:
                    result = StoredResume(
                        id=confirmed.id, file_name=confirmed.file_name,
                        file_type=confirmed.file_type, created_at=confirmed.created_at,
                    )
                    db.rollback()
                    return result
                db.rollback()
                if not isinstance(exc, ResumeLifecycleError):
                    raise ResumeUploadError("Upload result could not be confirmed; inspect its lifecycle before retrying", 503, code="resume_upload_pending", resume_id=resume_id) from None
        except (SQLAlchemyError, ResumeLifecycleError):
            db.rollback()
            raise ResumeUploadError("Upload failed and file cleanup is pending; retry or contact the maintainer", 503, code="resume_cleanup_pending", resume_id=resume_id) from None
        if isinstance(exc, ResumeUploadError):
            raise exc from None
        if isinstance(exc, ResumeLifecycleError):
            raise ResumeUploadError(exc.message, exc.status_code, code=exc.code, resume_id=resume_id) from None
        if isinstance(exc, StorageError):
            if exc.code == "size_limit":
                raise ResumeUploadError("PDF exceeds the 10 MiB upload limit", 413) from None
            if exc.code == "empty_file":
                raise ResumeUploadError("PDF file is empty", 422) from None
        raise ResumeUploadError("Could not save the PDF", 503) from None
