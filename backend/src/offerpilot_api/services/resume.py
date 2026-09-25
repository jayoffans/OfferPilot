"""Validate, store and extract text from uploaded PDF resumes."""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymupdf
from fastapi import UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from offerpilot_api.db.models import ResumeDocument

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_TEXT_CHARS = 1_000_000
READ_CHUNK_BYTES = 1024 * 1024


class ResumeUploadError(Exception):
    def __init__(self, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class StoredResume:
    id: uuid.UUID
    file_name: str
    file_type: str
    created_at: datetime


def _validated_filename(file: UploadFile) -> str:
    file_name = (file.filename or "").replace("\\", "/").split("/")[-1]
    if (
        not file_name
        or len(file_name) > 255
        or any(ord(char) < 32 for char in file_name)
        or Path(file_name).suffix.lower() != ".pdf"
    ):
        raise ResumeUploadError("Only .pdf files are accepted", 415)
    if file.content_type != "application/pdf":
        raise ResumeUploadError("File content type must be application/pdf", 415)
    return file_name


def _write_pdf(file: UploadFile, target: Path) -> None:
    byte_count = 0
    file_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(file_descriptor, "wb") as output:
        while chunk := file.file.read(READ_CHUNK_BYTES):
            byte_count += len(chunk)
            if byte_count > MAX_PDF_BYTES:
                raise ResumeUploadError("PDF exceeds the 10 MiB upload limit", 413)
            if byte_count == len(chunk) and not chunk.startswith(b"%PDF-"):
                raise ResumeUploadError("File content is not a PDF", 422)
            output.write(chunk)
    if byte_count == 0:
        raise ResumeUploadError("PDF file is empty")


def _extract_pdf_text(path: Path) -> str:
    try:
        with pymupdf.open(path) as pdf:
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


def save_resume_document(file: UploadFile, db: Session, upload_dir: Path) -> StoredResume:
    """Persist the original PDF and its extracted text as one logical operation."""

    file_name = _validated_filename(file)
    target = upload_dir / f"{uuid.uuid4().hex}.pdf"
    try:
        upload_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_pdf(file, target)
        raw_text = _extract_pdf_text(target)
        document = ResumeDocument(
            file_name=file_name,
            file_path=str(target),
            file_type="application/pdf",
            raw_text=raw_text,
        )
        db.add(document)
        db.flush()
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
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, (OSError, SQLAlchemyError)):
            raise ResumeUploadError("Could not save the PDF", 500) from exc
        raise
