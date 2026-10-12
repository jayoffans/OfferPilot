"""Durable file cleanup, owner-scoped downloads, and fenced upload completion.

Small local file I/O is serialized with a database write transaction. This is
deliberately conservative for SQLite; no process-local lock can protect a second
API process. A cleanup journal survives deleting the document itself.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from offerpilot_api.db.models import Profile, ResumeDocument, ResumeFileOperation
from offerpilot_api.profile.services import remove_resume_profile_evidence
from offerpilot_api.resume_ingestion.storage import ResumeStorage, StorageError


class ResumeLifecycleError(Exception):
    def __init__(self, code: str, message: str, status_code: int, *, resume_id: UUID | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.resume_id = resume_id


@dataclass(frozen=True)
class DownloadedResume:
    file_name: str
    content: bytes


@dataclass(frozen=True)
class RecoveryResult:
    attempted: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _owner(user_id: UUID) -> None:
    if not isinstance(user_id, UUID):
        raise ResumeLifecycleError("authentication_required", "请先登录", 401)


def _not_found() -> ResumeLifecycleError:
    return ResumeLifecycleError("resume_not_found", "Resume resource not found", 404)


def _key(storage: ResumeStorage, object_key: str | None, legacy_path: str | None) -> str:
    if object_key:
        return object_key
    # Only trusted database history can reach this branch. Cloud adapters need
    # an explicit historical-file migration, never an arbitrary local path API.
    resolver = getattr(storage, "resolve_legacy_key", None)
    if not resolver or not legacy_path:
        raise StorageError("invalid_reference", "Stored file reference is unavailable")
    return resolver(legacy_path)


def _claim(object_key: str | None, resume_id: UUID) -> dict:
    # The journal/resume ID predates every managed write. The adapter's durable
    # claim sidecar proves creation even if a later database commit fails.
    return {"claim_token": resume_id.hex} if object_key else {}


def lock_active_upload(db: Session, *, resume_id: UUID, user_id: UUID) -> None:
    """Hold the row/write lock through save, parsing, and the final DB commit."""
    _owner(user_id)
    locked = db.execute(
        update(ResumeDocument)
        .where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status == "active",
            ResumeDocument.processing_status == "uploading",
        )
        .values(processing_status=ResumeDocument.processing_status)
        .execution_options(synchronize_session=False)
    )
    if locked.rowcount != 1:
        raise ResumeLifecycleError("processing_result_discarded", "简历已停止处理，结果已丢弃", 409)


def complete_resume_upload(
    db: Session, *, resume_id: UUID, user_id: UUID, raw_text: str,
    byte_size: int, sha256: str,
) -> None:
    """A late processor cannot recreate or update a hidden/deleted resource.

The caller owns the transaction and must commit once with its other work.
"""
    _owner(user_id)
    changed = db.execute(
        update(ResumeDocument)
        .where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status == "active",
            ResumeDocument.processing_status.in_(("uploading", "uploaded")),
            ResumeDocument.object_key.is_not(None),
        )
        .values(raw_text=raw_text, byte_size=byte_size, sha256=sha256, processing_status="parsed")
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise ResumeLifecycleError("processing_result_discarded", "简历已停止处理，结果已丢弃", 409)
    db.execute(
        delete(ResumeFileOperation).where(
            ResumeFileOperation.resume_id == resume_id,
            ResumeFileOperation.user_id == user_id,
            ResumeFileOperation.reason == "upload", ResumeFileOperation.status == "uploading",
        )
    )


def download_owned_resume(
    db: Session, *, resume_id: UUID, user_id: UUID, storage: ResumeStorage,
) -> DownloadedResume:
    _owner(user_id)
    try:
        # The same row is locked by deletion intent. An already buffered
        # download can finish, but a download after that intent is rejected.
        locked = db.execute(
            update(ResumeDocument)
            .where(
                ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
                ResumeDocument.lifecycle_status == "active",
                ResumeDocument.processing_status == "parsed",
            )
            .values(lifecycle_status=ResumeDocument.lifecycle_status)
            .execution_options(synchronize_session=False)
        )
        if locked.rowcount != 1:
            db.rollback()
            raise _not_found()
        document = db.get(ResumeDocument, resume_id, populate_existing=True)
        content = storage.read(_key(storage, document.object_key, document.file_path), **_claim(document.object_key, resume_id))
        if document.byte_size is not None and len(content) != document.byte_size:
            raise ResumeLifecycleError("resume_file_integrity_failed", "简历原件校验失败，请联系维护者", 409)
        if document.sha256 and not hmac.compare_digest(hashlib.sha256(content).hexdigest().encode("ascii"), document.sha256.encode("utf-8")):
            raise ResumeLifecycleError("resume_file_integrity_failed", "简历原件校验失败，请联系维护者", 409)
        result = DownloadedResume(file_name=document.file_name, content=content)
        db.commit()
        return result
    except StorageError as exc:
        db.rollback()
        if exc.code == "file_missing":
            raise ResumeLifecycleError("resume_file_missing", "简历原件已丢失，可删除记录或联系维护者", 410) from None
        raise ResumeLifecycleError("resume_storage_unavailable", "暂时无法读取简历原件，请稍后重试", 503) from None
    except SQLAlchemyError:
        db.rollback()
        raise ResumeLifecycleError("storage_unavailable", "服务暂时不可用，请稍后重试", 503) from None
    except ResumeLifecycleError:
        db.rollback()
        raise


def _mark_delete_intent(db: Session, *, resume_id: UUID, user_id: UUID) -> bool:
    """Commit invisibility and erase derived contents before touching files."""
    _owner(user_id)
    locked = db.execute(
        update(ResumeDocument)
        .where(ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id)
        .values(lifecycle_status=ResumeDocument.lifecycle_status)
        .execution_options(synchronize_session=False)
    )
    if locked.rowcount != 1:
        operation = db.scalar(select(ResumeFileOperation).where(
            ResumeFileOperation.resume_id == resume_id, ResumeFileOperation.user_id == user_id,
        ))
        db.rollback()
        if operation is not None and operation.status == "deleted":
            return False
        if operation is not None and operation.status in ("deleting", "delete_failed"):
            return True
        raise _not_found()
    document = db.get(ResumeDocument, resume_id, populate_existing=True)
    operation = db.get(ResumeFileOperation, resume_id, populate_existing=True)
    if operation is not None and operation.user_id != user_id:
        db.rollback()
        raise ResumeLifecycleError("storage_unavailable", "清理记录无效，请联系维护者", 503)
    if operation is None:
        operation = ResumeFileOperation(
            resume_id=resume_id, user_id=user_id, object_key=document.object_key,
            legacy_path=document.file_path if not document.object_key else None,
            reason="delete", status="deleting",
        )
        db.add(operation)
    else:
        operation.reason = "delete"
        operation.status = "deleting"
        if operation.error_code != "object_key_collision":
            operation.error_code = None
        operation.updated_at = _now()
    db.execute(update(ResumeDocument).where(ResumeDocument.id == resume_id).values(
        lifecycle_status="deleting", raw_text=None,
        processing_status="failed" if document.processing_status in ("uploading", "uploaded") else document.processing_status,
    ))
    # Core DELETE uses the real database FK cascade for profile facts.
    remove_resume_profile_evidence(db, user_id=user_id, resume_id=resume_id)
    db.execute(delete(Profile).where(Profile.resume_id == resume_id))
    db.commit()
    return True


def _finish_cleanup(db: Session, *, resume_id: UUID, user_id: UUID, storage: ResumeStorage) -> bool:
    """Keep a write lock through idempotent file deletion and DB finalization."""
    try:
        # Match upload/intent lock order (resume, then operation), including on
        # row-locking databases. SQLite's global writer lock alone hid that
        # ordering concern during the local concurrency tests.
        document_locked = db.execute(update(ResumeDocument).where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
        ).values(lifecycle_status=ResumeDocument.lifecycle_status)
            .execution_options(synchronize_session=False))
        if document_locked.rowcount:
            document = db.get(ResumeDocument, resume_id, populate_existing=True)
            if document.lifecycle_status == "active":
                db.rollback()
                raise ResumeLifecycleError("resume_delete_pending", "资源尚未进入删除状态，请重试", 503, resume_id=resume_id)
        claimed = db.execute(
            update(ResumeFileOperation)
            .where(
                ResumeFileOperation.resume_id == resume_id, ResumeFileOperation.user_id == user_id,
                ResumeFileOperation.status.in_(("deleting", "delete_failed")),
            )
            .values(status="deleting", attempt_count=ResumeFileOperation.attempt_count + 1, updated_at=_now())
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            operation = db.get(ResumeFileOperation, resume_id, populate_existing=True)
            completed = operation is None or (operation.user_id == user_id and operation.status == "deleted")
            db.rollback()
            return completed
        operation = db.get(ResumeFileOperation, resume_id, populate_existing=True)
        try:
            if operation.error_code == "object_key_collision":
                raise StorageError("object_exists", "Storage reference conflict requires operator review")
            storage.delete(_key(storage, operation.object_key, operation.legacy_path), **_claim(operation.object_key, resume_id))
        except (StorageError, OSError):
            operation.status = "delete_failed"
            if operation.error_code != "object_key_collision":
                operation.error_code = "file_delete_failed"
            operation.updated_at = _now()
            db.execute(update(ResumeDocument).where(
                ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ).values(lifecycle_status="delete_failed", raw_text=None))
            db.commit()
            raise ResumeLifecycleError("resume_delete_failed", "文件清理失败，资源已隐藏；请重试删除", 503, resume_id=resume_id)
        db.execute(delete(ResumeDocument).where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status.in_(("deleting", "delete_failed")),
        ))
        if operation.reason == "upload":
            db.delete(operation)
        else:
            operation.status = "deleted"
            operation.object_key = None
            operation.legacy_path = None
            operation.error_code = None
            operation.updated_at = _now()
        db.commit()
        return True
    except SQLAlchemyError:
        # Deletion intent already committed. Even if the final transaction
        # fails, its journal stays pending and retry treats missing files as OK.
        db.rollback()
        raise ResumeLifecycleError("resume_delete_pending", "清理尚未完成，请重试或联系维护者", 503, resume_id=resume_id) from None


def delete_owned_resume(db: Session, *, resume_id: UUID, user_id: UUID, storage: ResumeStorage) -> None:
    try:
        pending = _mark_delete_intent(db, resume_id=resume_id, user_id=user_id)
        if pending and not _finish_cleanup(db, resume_id=resume_id, user_id=user_id, storage=storage):
            raise ResumeLifecycleError("resume_delete_pending", "清理尚未完成，请重试", 503, resume_id=resume_id)
    except SQLAlchemyError:
        db.rollback()
        raise ResumeLifecycleError("storage_unavailable", "服务暂时不可用，删除尚未完成", 503, resume_id=resume_id) from None


def abort_resume_upload(
    db: Session, *, resume_id: UUID, user_id: UUID, storage: ResumeStorage,
    stale_before: datetime | None = None,
) -> bool:
    """Convert only an incomplete upload into durable compensating cleanup."""
    _owner(user_id)
    filters = [
        ResumeFileOperation.resume_id == resume_id, ResumeFileOperation.user_id == user_id,
        ResumeFileOperation.reason == "upload", ResumeFileOperation.status == "uploading",
    ]
    if stale_before is not None:
        filters.append(ResumeFileOperation.created_at <= stale_before)
    # Always lock the resume before its operation, matching upload/delete order.
    fenced = db.execute(update(ResumeDocument).where(
        ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
        ResumeDocument.lifecycle_status == "active",
        ResumeDocument.processing_status.in_(("uploading", "uploaded")),
        ResumeDocument.id.in_(select(ResumeFileOperation.resume_id).where(*filters)),
    ).values(processing_status="failed", lifecycle_status="deleting", raw_text=None)
        .execution_options(synchronize_session=False))
    if fenced.rowcount != 1:
        db.rollback()
        return False
    db.execute(update(ResumeFileOperation).where(*filters).values(status="deleting", updated_at=_now()))
    remove_resume_profile_evidence(db, user_id=user_id, resume_id=resume_id)
    db.execute(delete(Profile).where(Profile.resume_id == resume_id))
    db.commit()
    return _finish_cleanup(db, resume_id=resume_id, user_id=user_id, storage=storage)


def get_owned_lifecycle(db: Session, *, resume_id: UUID, user_id: UUID) -> dict:
    _owner(user_id)
    document = db.scalar(select(ResumeDocument).where(
        ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
    ))
    operation = db.scalar(select(ResumeFileOperation).where(
        ResumeFileOperation.resume_id == resume_id, ResumeFileOperation.user_id == user_id,
    ))
    if document is None and operation is None:
        raise _not_found()
    if operation is not None and operation.status == "deleted":
        # Deletion may finish between the two reads; its tombstone is final.
        document = None
    lifecycle_status = document.lifecycle_status if document else "deleting"
    if operation is not None and operation.status in ("deleting", "delete_failed", "deleted"):
        # A committed intent read after the document is authoritative: a
        # concurrently deleted resource must never be reported as active.
        lifecycle_status = operation.status
    return {
        "id": resume_id,
        "processing_status": document.processing_status if document else None,
        "lifecycle_status": lifecycle_status,
        "deletion_status": operation.status if operation and operation.status != "uploading" else None,
        "error_code": operation.error_code if operation else None,
        "attempt_count": operation.attempt_count if operation else 0,
    }


def recover_pending_files(
    db: Session, storage: ResumeStorage, *, stale_before: datetime, limit: int = 100,
) -> RecoveryResult:
    if not 1 <= limit <= 1000:
        raise ValueError("Recovery batch limit must be between 1 and 1000")
    candidates = db.execute(select(
        ResumeFileOperation.resume_id, ResumeFileOperation.user_id, ResumeFileOperation.status,
    ).where(
        ResumeFileOperation.status.in_(("deleting", "delete_failed"))
        | ((ResumeFileOperation.status == "uploading") & (ResumeFileOperation.created_at <= stale_before))
    ).order_by(ResumeFileOperation.created_at, ResumeFileOperation.resume_id).limit(limit)).all()
    db.rollback()
    completed = failed = skipped = 0
    for resume_id, user_id, status in candidates:
        try:
            if status == "uploading":
                finished = abort_resume_upload(db, resume_id=resume_id, user_id=user_id, storage=storage, stale_before=stale_before)
            else:
                finished = _finish_cleanup(db, resume_id=resume_id, user_id=user_id, storage=storage)
            if finished:
                completed += 1
            else:
                skipped += 1
        except (ResumeLifecycleError, SQLAlchemyError):
            db.rollback()
            failed += 1
    return RecoveryResult(attempted=len(candidates), completed=completed, failed=failed, skipped=skipped)
