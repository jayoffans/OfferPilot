"""Bounded, durable owner-scoped ZIP exports using private local storage.

The durable job precedes IO. A user lock fences deletion and business writes
through snapshot creation; resume locks also serialize existing file deletion.
Each JSON section is streamed and every byte/record is charged to a budget.
No new provider, broker, import path or user-controlled filesystem name exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import stat
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from offerpilot_api.account_lifecycle.export_schemas import EXPORT_SCHEMA_VERSION, ExportStatus
from offerpilot_api.account_lifecycle.guards import require_active_account
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import (
    AccountExport, Application, ApplicationEvent, CareerProfile, CareerProfileEntry,
    JobJDSnapshot, JobMatch, JobRequirement, Profile, ProfileCandidate, ProfileFact,
    ResumeDocument, ResumeExtractionRun, ResumeFileOperation, SavedJob, User,
)
from offerpilot_api.identity.services import IdentityError
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, ResumeStorage, StorageError

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class ExportError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _json_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _record(row, fields):
    # Export allowlists are intentional: adding a DB column must never silently
    # add credentials or internal storage references to the archive.
    return {field: _json_value(getattr(row, field)) for field in fields.split()}


def export_status(row: AccountExport) -> ExportStatus:
    available = row.status == "ready" and row.expires_at is not None and _utc(row.expires_at) > _now()
    status = "expired" if row.status == "ready" and not available else row.status
    return ExportStatus(id=row.id, status=status, created_at=row.created_at, updated_at=row.updated_at,
                        completed_at=row.completed_at, expires_at=row.expires_at, byte_size=row.byte_size,
                        sha256=row.sha256, error_code=row.error_code, attempt_count=row.attempt_count,
                        download_available=available)


def get_export(db: Session, *, user_id: UUID, export_id: UUID) -> AccountExport:
    row = db.scalar(select(AccountExport).where(AccountExport.id == export_id, AccountExport.user_id == user_id))
    if row is None:
        raise ExportError("export_not_found", "导出任务不存在或当前账户无法访问", 404)
    return row


def list_exports(db: Session, *, user_id: UUID) -> list[ExportStatus]:
    return [export_status(row) for row in db.scalars(select(AccountExport).where(AccountExport.user_id == user_id)
            .order_by(AccountExport.created_at.desc(), AccountExport.id.desc()).limit(10))]


def create_export(db: Session, *, user_id: UUID, idempotency_key: str, settings: Settings) -> AccountExport:
    require_active_account(db, user_id, settings=settings)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(735120042)"))
    existing = db.scalar(select(AccountExport).where(AccountExport.user_id == user_id,
                                                     AccountExport.idempotency_key == idempotency_key))
    if existing is not None:
        db.commit()
        return existing
    cleanup_pending = db.scalar(select(AccountExport.id).where(AccountExport.user_id == user_id,
        AccountExport.error_code == "export_cleanup_failed").limit(1))
    if cleanup_pending is not None:
        db.rollback()
        raise ExportError("export_cleanup_pending", "已有导出文件清理未完成，请联系维护者恢复后再申请", 429)
    active = ("queued", "running")
    owned = db.scalar(select(func.count()).select_from(AccountExport).where(AccountExport.user_id == user_id,
                AccountExport.status.in_(active)))
    total = db.scalar(select(func.count()).select_from(AccountExport).where(AccountExport.status.in_(active)))
    retained = db.scalar(select(func.count()).select_from(AccountExport).where(AccountExport.user_id == user_id,
                AccountExport.status == "ready", AccountExport.expires_at > _now()))
    # Terminal receipts are bounded too. TTL files are independent of the small
    # idempotency history, which survives until account deletion.
    history = db.scalar(select(func.count()).select_from(AccountExport).where(AccountExport.user_id == user_id))
    if owned or total >= settings.export_max_active_total or retained >= settings.export_max_retained_per_user or history >= 100:
        db.rollback()
        raise ExportError("export_quota_exceeded", "已有导出正在处理，或导出份数已达上限，请下载现有文件或等待过期", 429)
    row = AccountExport(user_id=user_id, idempotency_key=idempotency_key, status="queued", attempt_count=0, max_attempts=2)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class ExportFiles:
    """Only durable job UUIDs can identify regular files below a private root."""

    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def _check(self, *, create=False):
        for path in (*reversed(self.root.parents), self.root):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT or not stat.S_ISDIR(info.st_mode):
                raise ExportError("export_storage_unavailable", "导出存储暂时不可用")
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._check()

    def path(self, export_id: UUID, suffix: str):
        if not isinstance(export_id, UUID) or suffix not in (".zip", ".part"):
            raise ExportError("export_storage_unavailable", "导出存储暂时不可用")
        self._check()
        path = self.root / f"{export_id.hex}{suffix}"
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT or not stat.S_ISREG(info.st_mode):
            raise ExportError("export_storage_unavailable", "导出存储暂时不可用")
        return path

    def open(self, export_id: UUID, suffix: str, *, create=False):
        self._check(create=create)
        path = self.path(export_id, suffix)
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL) if create else os.O_RDONLY
        descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
        try:
            info = os.fstat(descriptor)
            current = path.lstat()
            if not stat.S_ISREG(info.st_mode) or not os.path.samestat(info, current):
                raise ExportError("export_storage_unavailable", "导出存储暂时不可用")
            return os.fdopen(descriptor, "wb" if create else "rb")
        except BaseException:
            os.close(descriptor)
            raise

    def delete(self, export_id: UUID):
        for suffix in (".part", ".zip"):
            self.path(export_id, suffix).unlink(missing_ok=True)
        self.sync_directory()

    def sync_directory(self):
        if os.name != "nt" and self.root.exists():
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


class _Budget:
    def __init__(self, settings):
        self.settings, self.bytes, self.records = settings, 0, 0
        self.deadline = time.monotonic() + settings.export_timeout_seconds

    def check(self, *, size=0, records=0):
        self.bytes += size
        self.records += records
        if self.bytes > self.settings.export_max_bytes or self.records > self.settings.export_max_records:
            raise ExportError("export_resource_limit", "数据量超过本次导出上限，请联系维护者分批处理", 409)
        if time.monotonic() > self.deadline:
            raise ExportError("export_time_limit", "本次导出处理超时，请重新申请", 409)


def _write_json(archive, name, values, budget, entries):
    digest, count = hashlib.sha256(), 0
    with archive.open(name, "w") as output:
        def write(value):
            nonlocal count
            data = value.encode("utf-8")
            budget.check(size=len(data))
            output.write(data)
            digest.update(data)
            count += len(data)
        write("[")
        first = True
        for value in values:
            budget.check(records=1)
            if not first:
                write(",")
            first = False
            for chunk in _ENCODER.iterencode(_json_value(value)):
                write(chunk)
        write("]")
    entries.append({"path": name, "byte_size": count, "sha256": digest.hexdigest()})


def _rows(db, statement, fields, budget, transform=None):
    for row in db.scalars(statement.execution_options(yield_per=50)):
        budget.check()
        value = _record(row, fields)
        if transform:
            value = transform(row, value)
        yield value


def _snapshot(db, row, user, archive, storage, settings):
    budget, entries, omissions = _Budget(settings), [], []
    owner = row.user_id
    profile_ids = select(CareerProfile.id).where(CareerProfile.user_id == owner)
    resume_ids = select(ResumeDocument.id).where(ResumeDocument.user_id == owner)
    job_ids = select(SavedJob.id).where(SavedJob.user_id == owner)
    application_ids = select(Application.id).where(Application.user_id == owner)
    snapshot_at = _now()
    _write_json(archive, "account.json", [_record(user, "id email_normalized created_at")], budget, entries)
    _write_json(archive, "profile.json", _rows(db, select(CareerProfile).where(CareerProfile.user_id == owner),
                "id user_id revision schema_version preferences created_at updated_at", budget), budget, entries)
    metadata_fields = {"test_result", "provider", "model", "run_id", "source_version", "prompt_version", "schema_version", "parser_version"}
    def safe_metadata(_row, value):
        value["extraction_metadata"] = {key: item for key, item in value["extraction_metadata"].items() if key in metadata_fields}
        return value
    _write_json(archive, "profile_entries.json", _rows(db, select(CareerProfileEntry).where(CareerProfileEntry.profile_id.in_(profile_ids)),
                "id profile_id category value position source_kind resume_id field_sources evidence extraction_metadata created_at updated_at",
                budget, safe_metadata), budget, entries)
    _write_json(archive, "profile_candidates.json", _rows(db, select(ProfileCandidate).where(ProfileCandidate.profile_id.in_(profile_ids), ProfileCandidate.resume_id.in_(resume_ids)),
                "id profile_id resume_id category value evidence evidence_status status schema_version extraction_version extraction_metadata extraction_run_id entry_id created_at reviewed_at",
                budget, safe_metadata), budget, entries)
    legacy_ids = select(Profile.id).where(Profile.resume_id.in_(resume_ids))
    _write_json(archive, "legacy_resume_profiles.json", _rows(db, select(Profile).where(Profile.resume_id.in_(resume_ids)),
                "id resume_id name phone email education skills projects internships created_at", budget), budget, entries)
    _write_json(archive, "legacy_resume_facts.json", _rows(db, select(ProfileFact).where(ProfileFact.profile_id.in_(legacy_ids)),
                "id profile_id field_name value confidence source_text created_at", budget), budget, entries)
    # Existing file deletion locks resume before operation; account lock is the
    # common outer lock. No caller filename/path can become a ZIP entry.
    resume_states = {}
    resume_query = select(ResumeDocument).where(ResumeDocument.user_id == owner).order_by(ResumeDocument.id).with_for_update()
    for document in db.scalars(resume_query.execution_options(yield_per=20)):
            if len(resume_states) >= settings.export_max_records:
                raise ExportError("export_resource_limit", "数据量超过本次导出上限，请联系维护者分批处理", 409)
            value = {}
            status = "included"
            if document.lifecycle_status != "active":
                status = "deleting"
            elif document.processing_status != "parsed":
                status = "processing_incomplete"
            else:
                budget.check()
                try:
                    if document.object_key:
                        content = storage.read(document.object_key, claim_token=document.id.hex)
                    else:
                        content = storage.read(storage.resolve_legacy_key(document.file_path))
                except StorageError as exc:
                    status = "file_missing" if exc.code == "file_missing" else "file_unavailable"
                if status == "included":
                    actual = hashlib.sha256(content).hexdigest()
                    if (document.byte_size is not None and len(content) != document.byte_size) or (document.sha256 and not hmac.compare_digest(actual, document.sha256)):
                        status = "integrity_failed"
                    else:
                        name = f"resumes/{document.id}.pdf"
                        budget.check(size=len(content))
                        archive.writestr(name, content)
                        entries.append({"path": name, "byte_size": len(content), "sha256": actual})
                        value["archive_path"] = name
                        value["exported_sha256"] = actual
            value["original_file_status"] = status
            if status != "included":
                omissions.append({"resume_id": str(document.id), "status": status})
            resume_states[document.id] = value
    def resume_status(document, value):
        value.update(resume_states[document.id])
        if document.lifecycle_status != "active":
            value["raw_text"], value["text_layout"] = None, None
        return value
    _write_json(archive, "resumes.json", _rows(db, select(ResumeDocument).where(ResumeDocument.user_id == owner),
                "id file_name file_type byte_size sha256 processing_status lifecycle_status raw_text text_layout created_at",
                budget, resume_status), budget, entries)
    _write_json(archive, "resume_lifecycle.json", _rows(db, select(ResumeFileOperation).where(ResumeFileOperation.user_id == owner),
                "resume_id reason status attempt_count error_code created_at updated_at", budget), budget, entries)
    _write_json(archive, "extraction_runs.json", _rows(db, select(ResumeExtractionRun).where(ResumeExtractionRun.user_id == owner),
                "id resume_id source_version status mode test_result revision attempt_count max_attempts call_count max_calls candidate_count schema_version extraction_version prompt_version provider_version model error_code created_at updated_at finished_at billing_status prompt_tokens completion_tokens total_tokens",
                budget), budget, entries)
    _write_json(archive, "jobs.json", _rows(db, select(SavedJob).where(SavedJob.user_id == owner),
                "id user_id title company location salary work_type source_url source_kind revision jd_version is_favorite created_at updated_at", budget), budget, entries)
    _write_json(archive, "job_jd_snapshots.json", _rows(db, select(JobJDSnapshot).where(JobJDSnapshot.job_id.in_(job_ids)),
                "id job_id version jd_text text_sha256 created_at", budget), budget, entries)
    _write_json(archive, "job_requirements.json", _rows(db, select(JobRequirement).where(JobRequirement.job_id.in_(job_ids)),
                "id job_id snapshot_id type content priority review_status evidence_text source_start source_end source_kind position revision schema_version parser_version created_at updated_at reviewed_at", budget), budget, entries)
    current_profile_revision = db.scalar(select(CareerProfile.revision).where(CareerProfile.user_id == owner)) or 0
    from offerpilot_api.job_intelligence.matching import RULE_VERSION
    def match_status(result, value):
        job = db.scalar(select(SavedJob).where(SavedJob.id == result.job_id, SavedJob.user_id == owner))
        reasons = []
        if result.profile_revision != current_profile_revision:
            reasons.append("profile_changed")
        if job.revision != result.job_revision or job.jd_version != result.jd_version:
            reasons.append("job_changed")
        if result.rule_version != RULE_VERSION:
            reasons.append("rule_changed")
        value["stale"], value["stale_reasons"] = bool(reasons), reasons
        return value
    _write_json(archive, "matches.json", _rows(db, select(JobMatch).where(JobMatch.user_id == owner, JobMatch.job_id.in_(job_ids)),
                "id user_id job_id profile_revision job_revision jd_version rule_version schema_version evaluation created_at", budget, match_status), budget, entries)
    _write_json(archive, "applications.json", _rows(db, select(Application).where(Application.user_id == owner, Application.job_id.in_(job_ids)),
                "id user_id job_id job_title company location match_id attempt_number status revision submitted_at notes action_title action_notes action_due_date action_due_time action_status created_at updated_at", budget,
                lambda _row, value: {**value, "action_timezone": "Asia/Shanghai"}), budget, entries)
    _write_json(archive, "application_events.json", _rows(db, select(ApplicationEvent).where(ApplicationEvent.user_id == owner, ApplicationEvent.application_id.in_(application_ids)),
                "id user_id application_id revision type changes occurred_at recorded_at", budget), budget, entries)
    manifest = {"schema_version": EXPORT_SCHEMA_VERSION, "export_id": str(row.id), "user_id": str(owner),
                "snapshot_at": _json_value(snapshot_at), "timezone": "UTC", "action_timezone": "Asia/Shanghai",
                "scope": "当前用户已保存的账户、简历、正式画像与候选、职位、匹配、申请和时间线；不包含服务器凭据、先前导出包或备份。",
                "complete": not omissions, "omissions": omissions, "files": entries,
                "notes": ["未确认候选不是正式事实；历史解析资料与正式画像分开保存。", "本格式不支持自动重新导入。", "manifest 的 files 哈希不包含 manifest 自身。"]}
    content = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    budget.check(size=len(content))
    archive.writestr("manifest.json", content)
    return budget


def _claim(db, export_id, settings):
    # Ownership is reread from the durable record; the worker accepts no user
    # data or filesystem targets from a request queue.
    row = db.get(AccountExport, export_id)
    if row is None:
        db.rollback()
        return None
    owner = row.user_id
    require_active_account(db, owner, settings=settings)
    token, now = uuid4(), _now()
    changed = db.execute(update(AccountExport).where(AccountExport.id == export_id,
                AccountExport.status == "queued", AccountExport.attempt_count < AccountExport.max_attempts)
                .values(status="running", execution_token=token,
                        lease_deadline=now + timedelta(seconds=settings.export_timeout_seconds + 30),
                        attempt_count=AccountExport.attempt_count + 1, updated_at=now, error_code=None)
                .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return owner, token


def execute_export(db: Session, *, export_id: UUID, storage: ResumeStorage, settings: Settings) -> bool:
    claimed = _claim(db, export_id, settings)
    if claimed is None:
        return False
    owner, token = claimed
    files = ExportFiles(settings.export_dir)
    try:
        user = require_active_account(db, owner, settings=settings)
        row = db.scalar(select(AccountExport).where(AccountExport.id == export_id).with_for_update()
                        .execution_options(populate_existing=True))
        if row is None or row.status != "running" or row.execution_token != token:
            db.rollback()
            return False
        # A previous interrupted attempt's files belong to this durable UUID.
        files.delete(export_id)
        with files.open(export_id, ".part", create=True) as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
                _snapshot(db, row, user, archive, storage, settings)
            output.flush()
            os.fsync(output.fileno())
        with files.open(export_id, ".part") as source:
            digest, size = hashlib.sha256(), 0
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                if size > settings.export_max_bytes:
                    raise ExportError("export_resource_limit", "数据量超过本次导出上限，请联系维护者分批处理", 409)
        if _utc(row.lease_deadline) <= _now():
            raise ExportError("export_time_limit", "本次导出处理超时，请重新申请", 409)
        require_active_account(db, owner, settings=settings)
        os.replace(files.path(export_id, ".part"), files.path(export_id, ".zip"))
        files.sync_directory()
        row.status, row.execution_token, row.lease_deadline = "ready", None, None
        row.byte_size, row.sha256 = size, digest.hexdigest()
        row.completed_at = row.updated_at = _now()
        row.expires_at = row.completed_at + timedelta(seconds=settings.export_ttl_seconds)
        db.commit()
        return True
    except (ExportError, StorageError, OSError, SQLAlchemyError, ValueError, AttributeError, IdentityError) as exc:
        db.rollback()
        # A lease may have been cancelled/recovered after rollback released the
        # snapshot locks. Reacquire the same User -> job order and prove this
        # execution still owns cleanup before touching any published/part file.
        db.execute(update(User).where(User.id == owner).values(is_active=User.is_active))
        current = db.scalar(select(AccountExport).where(AccountExport.id == export_id).with_for_update()
                            .execution_options(populate_existing=True))
        if current is None or current.status != "running" or current.execution_token != token:
            db.rollback()
            return False
        cleanup_failed = False
        try:
            files.delete(export_id)
        except (ExportError, OSError):
            cleanup_failed = True
        # Safe fixed code only. Database/provider/file exceptions can contain
        # names or user text and must never be persisted as an error message.
        code = exc.code if isinstance(exc, ExportError) else "export_failed"
        changed = db.execute(update(AccountExport).where(AccountExport.id == export_id,
                    AccountExport.status == "running", AccountExport.execution_token == token)
                    .values(status="failed", execution_token=None, lease_deadline=None,
                            updated_at=_now(), error_code="export_cleanup_failed" if cleanup_failed else code)
                    .execution_options(synchronize_session=False))
        db.commit()
        return False


def delete_user_exports(db: Session, *, user_id: UUID, settings: Settings) -> None:
    """Caller holds the User lock and has already revoked account access.

    Publish and deletion take User then AccountExport locks. Thus a running
    snapshot finishes before deletion intent, or sees the fence and discards
    its result. Failure leaves the caller's durable deletion journal pending.
    This function never deletes DB records or commits the caller's transaction.
    """
    files = ExportFiles(settings.export_dir)
    for row in db.scalars(select(AccountExport).where(AccountExport.user_id == user_id)
                          .order_by(AccountExport.id).with_for_update()):
        try:
            files.delete(row.id)
        except (OSError, ExportError):
            raise ExportError("export_cleanup_failed", "导出文件清理未完成，请重试或联系维护者") from None


def cleanup_expired_exports(db: Session, *, settings: Settings, limit: int = 20) -> dict:
    if not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be between 1 and 1000")
    now, files = _now(), ExportFiles(settings.export_dir)
    rows = db.execute(select(AccountExport.id, AccountExport.user_id).where(
        AccountExport.cleanup_attempt_count < 5,
        ((AccountExport.status == "ready") & (AccountExport.expires_at <= now))
        | ((AccountExport.status.in_(("failed", "expired", "cancelled"))) & (AccountExport.error_code == "export_cleanup_failed"))
    ).order_by(AccountExport.created_at, AccountExport.id).limit(limit)).all()
    db.rollback()
    completed = failed = 0
    for export_id, owner in rows:
        row = db.scalar(select(AccountExport).where(AccountExport.id == export_id).with_for_update())
        if row is None:
            db.rollback()
            continue
        if row.cleanup_attempt_count >= 5:
            db.rollback()
            continue
        row.cleanup_attempt_count += 1
        terminal_status = row.status if row.status in ("failed", "cancelled") else "expired"
        try:
            files.delete(export_id)
            row.status = terminal_status
            row.error_code = "export_interrupted" if terminal_status == "failed" else None
            row.updated_at = _now()
            db.commit()
            completed += 1
        except (ExportError, OSError):
            row.status, row.error_code = terminal_status, "export_cleanup_failed"
            row.updated_at = _now()
            db.commit()
            failed += 1
    return {"attempted": len(rows), "completed": completed, "failed": failed}


def recover_exports(db: Session, *, settings: Settings, limit: int = 20) -> int:
    if not 1 <= limit <= 1000:
        raise ValueError("Recovery limit must be between 1 and 1000")
    rows = db.scalars(select(AccountExport).where(AccountExport.status == "running", AccountExport.lease_deadline <= _now())
                      .order_by(AccountExport.created_at, AccountExport.id).limit(limit).with_for_update()).all()
    for row in rows:
        row.status = "queued" if row.attempt_count < row.max_attempts else "failed"
        row.error_code = "export_cleanup_failed" if row.status == "failed" else None
        row.execution_token, row.lease_deadline, row.updated_at = None, None, _now()
    db.commit()
    return len(rows)


def process_export_batch(db: Session, storage: ResumeStorage, settings: Settings, *, limit: int = 3) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("Export batch must be between 1 and 100")
    recover_exports(db, settings=settings, limit=limit)
    cleaned = cleanup_expired_exports(db, settings=settings, limit=limit)
    ids = db.scalars(select(AccountExport.id).where(AccountExport.status == "queued")
                     .order_by(AccountExport.created_at, AccountExport.id).limit(limit)).all()
    db.rollback()
    completed = 0
    for export_id in ids:
        try:
            completed += int(execute_export(db, export_id=export_id, storage=storage, settings=settings))
        except Exception:
            # No exception text in stdout/logs. Durable lease/status preserves
            # work for bounded retry or operator inspection after DB outages.
            db.rollback()
    return {"attempted": len(ids), "completed": completed, "cleanup": cleaned}


def download_export(db: Session, *, user_id: UUID, export_id: UUID, settings: Settings):
    require_active_account(db, user_id, settings=settings)
    row = get_export(db, user_id=user_id, export_id=export_id)
    if row.status == "expired" or (row.status == "ready" and row.expires_at is None) or (row.expires_at and _utc(row.expires_at) <= _now()):
        raise ExportError("export_expired", "导出文件已过期，请重新申请", 410)
    if row.status != "ready":
        raise ExportError("export_not_ready", "导出尚未完成，请查看任务状态", 409)
    source = None
    try:
        source = ExportFiles(settings.export_dir).open(export_id, ".zip")
        info = os.fstat(source.fileno())
        if info.st_size != row.byte_size or info.st_size > settings.export_max_bytes:
            raise ExportError("export_integrity_failed", "导出文件校验失败，请重新申请", 409)
        digest = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
        if not row.sha256 or not hmac.compare_digest(digest.hexdigest(), row.sha256):
            raise ExportError("export_integrity_failed", "导出文件校验失败，请重新申请", 409)
        source.seek(0)
        # Release DB locks before streaming; this already-authorized open file
        # may complete while deletion starts. New downloads lose authorization.
        db.commit()
        return source, info.st_size
    except FileNotFoundError:
        if source:
            source.close()
        raise ExportError("export_file_missing", "导出文件已丢失，请重新申请", 410) from None
    except ExportError:
        if source:
            source.close()
        raise
    except OSError:
        if source:
            source.close()
        raise ExportError("export_storage_unavailable", "导出存储暂时不可用，请稍后重试") from None


async def export_dispatcher(app, session_factory):
    settings = getattr(app.state, "lifecycle_settings", None) or get_settings()
    def tick():
        with session_factory() as db:
            process_export_batch(db, LocalResumeStorage(settings.upload_dir), settings, limit=1)
    while True:
        if getattr(app.state, "lifecycle_dispatcher_enabled", True):
            try:
                await asyncio.to_thread(tick)
            except Exception:
                pass  # Work remains durable; never log a potentially sensitive exception.
        await asyncio.sleep(1)
