"""Private job use cases with CAS, exact evidence and request idempotency.

These services never fetch URLs, invoke models, access profiles or log JD text.
"""

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.db.models import JobCreateReceipt, JobJDSnapshot, JobRequirement, SavedJob
from offerpilot_api.job_catalog.schemas import (
    MAX_REVISION, JobCreateRequest, JobDetailResponse, JobFavoriteRequest,
    JobListItem, JobListResponse, JobRequirementInput, JobRequirementResponse,
    JobRequirementReviewRequest, JobRequirementWriteRequest,
    JobRequirementsSaveRequest, JobSaveRequest,
)
from offerpilot_api.job_intelligence.parser import PARSER_VERSION, parse_jd

BASIC_FIELDS = ("title", "company", "location", "salary", "work_type", "source_url")
MAX_JOBS = 1000
MAX_JD_VERSIONS = 100


class JobError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _now():
    return datetime.now(timezone.utc)


def _missing():
    return JobError("job_not_found", "职位不存在或当前账户无法访问", 404)


def _conflict():
    return JobError("job_revision_conflict", "职位已更新，请保留本次修改并重新加载最新版本", 409)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _content_digest(payload):
    return _digest(payload.model_dump(mode="json", include=set(BASIC_FIELDS) | {"jd_text"}))


def _job(db: Session, user_id: UUID, job_id: UUID):
    row = db.scalar(select(SavedJob).where(SavedJob.id == job_id, SavedJob.user_id == user_id).execution_options(populate_existing=True))
    if row is None:
        raise _missing()
    return row


def _claim(db: Session, user_id: UUID, job_id: UUID, revision: int):
    changed = db.execute(update(SavedJob).where(SavedJob.id == job_id, SavedJob.user_id == user_id,
                                              SavedJob.revision == revision, SavedJob.revision <= MAX_REVISION)
                         .values(revision=SavedJob.revision + 1, updated_at=_now()).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        _job(db, user_id, job_id)
        raise _conflict()
    return _job(db, user_id, job_id)


def _snapshots(db: Session, job_id: UUID):
    return {row.version: row for row in db.scalars(select(JobJDSnapshot).where(JobJDSnapshot.job_id == job_id).order_by(JobJDSnapshot.version))}


def _entries(db: Session, job_id: UUID):
    return list(db.scalars(select(JobRequirement).where(JobRequirement.job_id == job_id).order_by(JobRequirement.position, JobRequirement.id)))


def _summary(entries, snapshots, version):
    stale = sum(snapshots[row.snapshot_id].version != version for row in entries)
    confirmed = sum(row.review_status == "confirmed" and snapshots[row.snapshot_id].version == version for row in entries)
    return {"total": len(entries), "pending": len(entries) - confirmed, "confirmed": confirmed, "stale": stale}


def _response(db: Session, job: SavedJob) -> JobDetailResponse:
    snapshots = _snapshots(db, job.id)
    by_id = {row.id: row for row in snapshots.values()}
    entries = _entries(db, job.id)
    # An invalid snapshot chain is never presented as credible source evidence.
    if any(row.snapshot_id not in by_id for row in entries):
        raise JobError("job_source_invalid", "职位来源关联无效，请联系管理员", 409)
    result = []
    for row in entries:
        snapshot = by_id[row.snapshot_id]
        start, end = row.source_start, row.source_end
        result.append(JobRequirementResponse(
            id=row.id, type=row.type, content=row.content, priority=row.priority,
            review_status="pending" if snapshot.version != job.jd_version else row.review_status,
            evidence_text=row.evidence_text, source_start=start, source_end=end,
            line_start=snapshot.jd_text.count("\n", 0, start) + 1 if start is not None else None,
            line_end=snapshot.jd_text.count("\n", 0, end - 1) + 1 if end is not None else None,
            source_jd_version=snapshot.version, source_kind=row.source_kind,
            stale=snapshot.version != job.jd_version, revision=row.revision,
            parser_version=row.parser_version, created_at=row.created_at,
            updated_at=row.updated_at, reviewed_at=row.reviewed_at,
        ))
    return JobDetailResponse(id=job.id, **{key: getattr(job, key) for key in BASIC_FIELDS},
        source_kind=job.source_kind, revision=job.revision, jd_version=job.jd_version,
        is_favorite=job.is_favorite, created_at=job.created_at, updated_at=job.updated_at,
        review_summary=_summary(entries, by_id, job.jd_version), jd_text=snapshots[job.jd_version].jd_text,
        requirements=result, parser_version=PARSER_VERSION,
        jd_snapshots=[{"version": row.version, "jd_text": row.jd_text, "created_at": row.created_at} for row in snapshots.values()])


def get_job(db: Session, *, user_id: UUID, job_id: UUID) -> JobDetailResponse:
    return _response(db, _job(db, user_id, job_id))


def list_jobs(db: Session, *, user_id: UUID, q=None, work_type=None, is_favorite=None,
              review_status=None, sort="updated_desc", page=1, page_size=20) -> JobListResponse:
    filters = [SavedJob.user_id == user_id]
    if q:
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(or_(*(getattr(SavedJob, key).ilike(f"%{escaped}%", escape="\\") for key in ("title", "company", "location"))))
    if work_type is not None:
        filters.append(SavedJob.work_type == work_type)
    if is_favorite is not None:
        filters.append(SavedJob.is_favorite == is_favorite)
    if review_status is not None:
        pending = exists(select(JobRequirement.id).join(JobJDSnapshot, JobJDSnapshot.id == JobRequirement.snapshot_id)
            .where(JobRequirement.job_id == SavedJob.id,
                   or_(JobRequirement.review_status != "confirmed", JobJDSnapshot.version != SavedJob.jd_version)))
        any_requirement = exists(select(JobRequirement.id).where(JobRequirement.job_id == SavedJob.id))
        filters.append(pending if review_status == "pending" else any_requirement & ~pending)
    ordering = {"updated_desc": SavedJob.updated_at.desc(), "created_desc": SavedJob.created_at.desc(), "title_asc": SavedJob.title.asc()}[sort]
    total = db.scalar(select(func.count()).select_from(SavedJob).where(*filters))
    rows = db.scalars(select(SavedJob).where(*filters).order_by(ordering, SavedJob.id).offset((page - 1) * page_size).limit(page_size))
    def summary_item(row):
        counts = db.execute(select(func.count(JobRequirement.id),
            func.count(JobRequirement.id).filter(JobRequirement.review_status == "confirmed", JobJDSnapshot.version == row.jd_version),
            func.count(JobRequirement.id).filter(JobJDSnapshot.version != row.jd_version))
            .join(JobJDSnapshot, JobJDSnapshot.id == JobRequirement.snapshot_id)
            .where(JobRequirement.job_id == row.id, JobJDSnapshot.job_id == row.id)).one()
        total, confirmed, stale = counts
        return JobListItem(id=row.id, **{key: getattr(row, key) for key in BASIC_FIELDS},
            source_kind=row.source_kind, revision=row.revision, jd_version=row.jd_version,
            is_favorite=row.is_favorite, created_at=row.created_at, updated_at=row.updated_at,
            review_summary={"total": total, "pending": total - confirmed, "confirmed": confirmed, "stale": stale})
    return JobListResponse(items=[summary_item(row) for row in rows],
                           total=total, page=page, page_size=page_size)


def _evidence(item: JobRequirementInput, snapshot: JobJDSnapshot):
    if item.evidence_text is None:
        return None, None
    quote = item.evidence_text
    start = item.source_start
    if start is None:
        start = snapshot.jd_text.find(quote)
        if start >= 0 and snapshot.jd_text.find(quote, start + 1) >= 0:
            raise JobError("job_evidence_ambiguous", "原文证据出现多次，请选择明确的来源位置", 422)
    if start < 0 or snapshot.jd_text[start:start + len(quote)] != quote:
        raise JobError("job_evidence_invalid", "证据与所选 JD 版本不一致，请重新对照原文", 422)
    return start, start + len(quote)


def _write_entries(db: Session, job: SavedJob, inputs: list[JobRequirementInput], *, jd_changed=False):
    old = {row.id: row for row in _entries(db, job.id)}
    snapshots = _snapshots(db, job.id)
    ids = [item.id for item in inputs if item.id is not None]
    if len(ids) != len(set(ids)):
        raise JobError("job_requirement_duplicate", "同一条件不能重复保存", 422)
    if any(item_id not in old for item_id in ids):
        raise _missing()
    rule_items = {version: parse_jd(snapshot.jd_text).requirements for version, snapshot in snapshots.items()}
    retained = set()
    for position, item in enumerate(inputs):
        version = item.source_jd_version or job.jd_version
        if version not in snapshots:
            raise JobError("job_jd_version_invalid", "JD 来源版本无效", 422)
        snapshot = snapshots[version]
        start, end = _evidence(item, snapshot)
        row = old.get(item.id)
        is_rule = any(candidate.type == item.type and candidate.content == item.content and candidate.priority == item.priority
                      and candidate.evidence_text == item.evidence_text and candidate.source_start == start for candidate in rule_items[version])
        refers_to_rule = any(candidate.evidence_text == item.evidence_text and candidate.source_start == start
                            for candidate in rule_items[version] if item.evidence_text is not None)
        source_kind = "rule" if is_rule else "user_modified" if refers_to_rule else "manual"
        if row is not None:
            changed = any(getattr(row, key) != getattr(item, key) for key in ("type", "content", "priority", "evidence_text"))
            source_kind = "user_modified" if changed or row.source_kind == "user_modified" else row.source_kind
            row.revision += 1
        else:
            row = JobRequirement(job_id=job.id, revision=1, created_at=_now())
            db.add(row)
        status = "pending" if jd_changed or version != job.jd_version else item.review_status
        row.snapshot_id, row.type, row.content = snapshot.id, item.type, item.content
        row.priority, row.review_status, row.position = item.priority, status, position
        row.evidence_text, row.source_start, row.source_end = item.evidence_text, start, end
        row.source_kind, row.schema_version = source_kind, "1.0"
        row.parser_version = PARSER_VERSION if source_kind in {"rule", "user_modified"} and (refers_to_rule or (row.parser_version is not None)) else None
        row.updated_at = _now()
        row.reviewed_at = _now() if status == "confirmed" else None
        db.flush()
        retained.add(row.id)
    for entry_id in set(old) - retained:
        db.delete(old[entry_id])


def _receipt(db, user_id, key):
    return db.scalar(select(JobCreateReceipt).where(JobCreateReceipt.user_id == user_id, JobCreateReceipt.idempotency_key == key))


def _replay(db, receipt, digest, user_id):
    if receipt.request_digest != digest:
        raise JobError("job_idempotency_conflict", "本次保存请求标识已用于其他内容，请使用新的请求标识", 409)
    if receipt.job_id is None:
        raise JobError("job_already_deleted", "这次保存对应的职位已删除，请重新创建", 409)
    return get_job(db, user_id=user_id, job_id=receipt.job_id)


def _duplicate_matches(db, job, payload):
    if payload.requirements is None:
        return
    current = _response(db, job)
    def comparable(item):
        value = item.model_dump(mode="json", include={"type", "content", "priority", "review_status", "evidence_text", "source_start", "source_jd_version"})
        value["source_jd_version"] = value["source_jd_version"] or job.jd_version
        return value
    if [comparable(item) for item in payload.requirements] != [comparable(item) for item in current.requirements]:
        raise JobError("job_duplicate", "已保存相同JD的职位，且审核内容不同，请打开已有职位编辑", 409)


def create_job(db: Session, *, user_id: UUID, payload: JobCreateRequest) -> JobDetailResponse:
    digest = _digest(payload.model_dump(mode="json", exclude={"idempotency_key"}))
    receipt = _receipt(db, user_id, payload.idempotency_key)
    if receipt:
        return _replay(db, receipt, digest, user_id)
    try:
        content_digest = _content_digest(payload)
        job = db.scalar(select(SavedJob).where(SavedJob.user_id == user_id, SavedJob.content_digest == content_digest))
        if job is None:
            if db.scalar(select(func.count()).select_from(SavedJob).where(SavedJob.user_id == user_id)) >= MAX_JOBS:
                raise JobError("job_limit_reached", "已达到1000个职位上限，请先整理现有职位", 409)
            job = SavedJob(user_id=user_id, **payload.model_dump(include=set(BASIC_FIELDS)),
                           source_kind="pasted" if payload.jd_text.strip() else "manual", content_digest=content_digest,
                           revision=1, jd_version=1, created_at=_now(), updated_at=_now())
            db.add(job)
            db.flush()
            db.add(JobJDSnapshot(job_id=job.id, version=1, jd_text=payload.jd_text,
                                 text_sha256=hashlib.sha256(payload.jd_text.encode()).hexdigest()))
            db.flush()
            _write_entries(db, job, payload.requirements if payload.requirements is not None else parse_jd(payload.jd_text).requirements)
        else:
            _duplicate_matches(db, job, payload)
        db.add(JobCreateReceipt(user_id=user_id, job_id=job.id, idempotency_key=payload.idempotency_key, request_digest=digest))
        db.commit()
    except IntegrityError:
        db.rollback()
        receipt = _receipt(db, user_id, payload.idempotency_key)
        if receipt:
            return _replay(db, receipt, digest, user_id)
        # Concurrent identical creates under different keys reuse one resource.
        existing = db.scalar(select(SavedJob).where(SavedJob.user_id == user_id, SavedJob.content_digest == _content_digest(payload)))
        if existing is None:
            raise _conflict() from None
        _duplicate_matches(db, existing, payload)
        db.add(JobCreateReceipt(user_id=user_id, job_id=existing.id, idempotency_key=payload.idempotency_key, request_digest=digest))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            receipt = _receipt(db, user_id, payload.idempotency_key)
            if receipt:
                return _replay(db, receipt, digest, user_id)
            raise _conflict() from None
        return _response(db, existing)
    except Exception:
        db.rollback()
        raise
    return _response(db, job)


def save_job(db: Session, *, user_id: UUID, job_id: UUID, payload: JobSaveRequest) -> JobDetailResponse:
    try:
        job = _claim(db, user_id, job_id, payload.revision)
        current = _snapshots(db, job.id)[job.jd_version]
        jd_changed = current.jd_text != payload.jd_text
        if jd_changed:
            if job.jd_version >= MAX_JD_VERSIONS:
                raise JobError("job_version_limit", "JD已达到100个版本上限，请另建职位", 409)
            job.jd_version += 1
            db.add(JobJDSnapshot(job_id=job.id, version=job.jd_version, jd_text=payload.jd_text,
                                 text_sha256=hashlib.sha256(payload.jd_text.encode()).hexdigest()))
            db.flush()
        for key in BASIC_FIELDS:
            setattr(job, key, getattr(payload, key))
        job.content_digest = _content_digest(payload)
        _write_entries(db, job, payload.requirements, jd_changed=jd_changed)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise JobError("job_duplicate", "已有相同内容的职位，请检查列表", 409) from None
    except Exception:
        db.rollback()
        raise
    return _response(db, job)


def favorite_job(db: Session, *, user_id: UUID, job_id: UUID, payload: JobFavoriteRequest):
    try:
        job = _claim(db, user_id, job_id, payload.revision)
        job.is_favorite = payload.is_favorite
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _response(db, job)


def delete_job(db: Session, *, user_id: UUID, job_id: UUID, revision: int):
    try:
        _claim(db, user_id, job_id, revision)
        from offerpilot_api.db.models import Application
        if db.scalar(select(Application.id).where(Application.job_id == job_id, Application.user_id == user_id).limit(1)):
            raise JobError("job_has_applications", "该职位有关联申请；请先在投递进度中确认并删除相关申请，避免丢失历史", 409)
        db.execute(delete(SavedJob).where(SavedJob.id == job_id, SavedJob.user_id == user_id))
        db.commit()
    except Exception:
        db.rollback()
        raise


def save_requirements(db: Session, *, user_id: UUID, job_id: UUID, payload: JobRequirementsSaveRequest):
    try:
        job = _claim(db, user_id, job_id, payload.revision)
        _write_entries(db, job, payload.requirements)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _response(db, job)


def write_requirement(db: Session, *, user_id: UUID, job_id: UUID, payload: JobRequirementWriteRequest, requirement_id: UUID | None = None):
    current = get_job(db, user_id=user_id, job_id=job_id)
    inputs = [JobRequirementInput.model_validate(row.model_dump(include=set(JobRequirementInput.model_fields))) for row in current.requirements]
    if requirement_id is None:
        if payload.requirement.id is not None:
            raise JobError("job_requirement_id_invalid", "新条件不应指定已有标识", 422)
        inputs.append(payload.requirement)
    else:
        if not any(item.id == requirement_id for item in inputs):
            raise _missing()
        if payload.requirement.id not in {None, requirement_id}:
            raise _missing()
        inputs = [payload.requirement.model_copy(update={"id": requirement_id}) if item.id == requirement_id else item for item in inputs]
    return save_requirements(db, user_id=user_id, job_id=job_id, payload=JobRequirementsSaveRequest(revision=payload.revision, requirements=inputs))


def remove_requirement(db: Session, *, user_id: UUID, job_id: UUID, requirement_id: UUID, revision: int):
    current = get_job(db, user_id=user_id, job_id=job_id)
    if not any(item.id == requirement_id for item in current.requirements):
        raise _missing()
    inputs = [JobRequirementInput.model_validate(row.model_dump(include=set(JobRequirementInput.model_fields))) for row in current.requirements if row.id != requirement_id]
    return save_requirements(db, user_id=user_id, job_id=job_id, payload=JobRequirementsSaveRequest(revision=revision, requirements=inputs))


def review_requirement(db: Session, *, user_id: UUID, job_id: UUID, requirement_id: UUID, payload: JobRequirementReviewRequest):
    current = get_job(db, user_id=user_id, job_id=job_id)
    target = next((item for item in current.requirements if item.id == requirement_id), None)
    if target is None:
        raise _missing()
    if target.stale and payload.action == "confirm":
        raise JobError("job_requirement_stale", "JD已修改，请先将条件与当前JD重新对照，再确认", 409)
    target.review_status = "confirmed" if payload.action == "confirm" else "pending"
    return write_requirement(db, user_id=user_id, job_id=job_id, requirement_id=requirement_id,
        payload=JobRequirementWriteRequest(revision=payload.revision,
            requirement=JobRequirementInput.model_validate(target.model_dump(include=set(JobRequirementInput.model_fields)))))
