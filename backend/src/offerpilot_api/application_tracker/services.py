"""Manual, owner-scoped application transactions and append-only timelines.

Writes lock the owner before the job/application. Request receipts are checked
before revision claims, so a lost response can be retried without another event.
Only a user-provided timestamp records an actual submission; status is not proof.
"""

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from offerpilot_api.application_tracker.schemas import (
    ApplicationCreateRequest, ApplicationDeleteRequest, ApplicationEventListResponse, ApplicationEventResponse,
    ApplicationListResponse, ApplicationMatchReference, ApplicationResponse,
    ApplicationUpdateRequest, NextActionInput,
)
from offerpilot_api.db.models import (
    Application, ApplicationEvent, ApplicationReceipt, CareerProfile, JobMatch, SavedJob, User,
)
from offerpilot_api.job_catalog.schemas import MAX_REVISION
from offerpilot_api.job_intelligence import matching

MAX_APPLICATIONS_PER_USER = 1000
MAX_ATTEMPTS_PER_JOB = 100
MAX_EVENTS_PER_APPLICATION = 5000
MAX_RECEIPTS_PER_USER = 20000
TERMINAL_STATUSES = {"rejected", "withdrawn", "closed"}
# Shanghai has no daylight-saving changes in the supported modern calendar.
# An explicit fixed offset avoids relying on Windows' optional IANA tzdata.
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


class ApplicationError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _missing():
    return ApplicationError("application_not_found", "申请记录不存在或当前账户无法访问", 404)


def _conflict():
    return ApplicationError("application_revision_conflict", "申请已更新，请保留本次输入并重新加载最新版本", 409)


def _owner(user_id):
    if not isinstance(user_id, UUID):
        raise ApplicationError("authentication_required", "请先登录", 401)


def _lock_owner(db, user_id):
    _owner(user_id)
    changed = db.execute(update(User).where(User.id == user_id, User.is_active.is_(True))
        .values(is_active=User.is_active).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise ApplicationError("authentication_required", "会话已失效，请重新登录", 401)


def _application(db, user_id, application_id):
    _owner(user_id)
    row = db.scalar(select(Application).where(Application.id == application_id, Application.user_id == user_id)
                    .execution_options(populate_existing=True))
    if row is None:
        raise _missing()
    return row


def _job(db, user_id, job_id):
    row = db.scalar(select(SavedJob).where(SavedJob.id == job_id, SavedJob.user_id == user_id)
                    .execution_options(populate_existing=True))
    if row is None:
        raise ApplicationError("job_not_found", "职位不存在或当前账户无法访问", 404)
    return row


def _action(row):
    if row.action_title is None:
        return None
    try:
        return NextActionInput(title=row.action_title, notes=row.action_notes,
            due_date=row.action_due_date, due_time=row.action_due_time,
            timezone="Asia/Shanghai", status=row.action_status)
    except ValidationError:
        raise ApplicationError("application_action_invalid", "下一步行动结构无法读取，请联系管理员核对", 409) from None


def is_action_overdue(action: NextActionInput | None, *, now: datetime | None = None):
    """Date-only deadlines expire after that Shanghai calendar day, never at 00:00."""
    if action is None or action.status != "pending" or action.due_date is None:
        return False
    current = _utc(now or _now()).astimezone(SHANGHAI)
    if action.due_time is None:
        return current.date() > action.due_date
    deadline = datetime.combine(action.due_date, time.fromisoformat(action.due_time), tzinfo=SHANGHAI)
    return current > deadline


def _match_reference(db, user_id, job, match_id, *, matches=None, profile_revision=None):
    if match_id is None:
        return None
    result = matches.get(match_id) if matches is not None else db.scalar(select(JobMatch).where(
        JobMatch.id == match_id, JobMatch.user_id == user_id, JobMatch.job_id == job.id))
    if result is None or result.job_id != job.id:
        return None
    if profile_revision is None:
        profile_revision = db.scalar(select(CareerProfile.revision).where(CareerProfile.user_id == user_id)) or 0
    reasons = []
    if profile_revision != result.profile_revision:
        reasons.append("profile_changed")
    if job.revision != result.job_revision or job.jd_version != result.jd_version:
        reasons.append("job_changed")
    if result.rule_version != matching.RULE_VERSION:
        reasons.append("rule_changed")
    return ApplicationMatchReference(id=result.id, created_at=_utc(result.created_at),
        profile_revision=result.profile_revision, job_revision=result.job_revision,
        jd_version=result.jd_version, rule_version=result.rule_version,
        stale=bool(reasons), stale_reasons=reasons)


def _response(db, user_id, row, *, jobs=None, matches=None, profile_revision=None, now=None):
    job = jobs.get(row.job_id) if jobs is not None else _job(db, user_id, row.job_id)
    if job is None:
        raise _missing()
    action = _action(row)
    return ApplicationResponse(id=row.id, job_id=row.job_id, job_title=row.job_title,
        company=row.company, location=row.location, attempt_number=row.attempt_number,
        status=row.status, revision=row.revision, submitted_at=_utc(row.submitted_at), notes=row.notes,
        next_action=action, created_at=_utc(row.created_at), updated_at=_utc(row.updated_at),
        is_overdue=row.status not in TERMINAL_STATUSES and is_action_overdue(action, now=now),
        match=_match_reference(db, user_id, job, row.match_id, matches=matches, profile_revision=profile_revision))


def _pending_action_predicate():
    return and_(Application.action_title.is_not(None), Application.action_status == "pending",
                Application.status.not_in(TERMINAL_STATUSES))


def _overdue_predicate(now):
    """Use the same strict minute deadline as is_action_overdue, including exact equality."""
    current = _utc(now).astimezone(SHANGHAI)
    minute = current.strftime("%H:%M")
    earlier_time = (Application.action_due_time <= minute if current.second or current.microsecond
                    else Application.action_due_time < minute)
    return and_(_pending_action_predicate(), Application.action_due_date.is_not(None),
                or_(Application.action_due_date < current.date(),
                    and_(Application.action_due_date == current.date(),
                         Application.action_due_time.is_not(None), earlier_time)))


def get_application(db: Session, *, user_id: UUID, application_id: UUID) -> ApplicationResponse:
    return _response(db, user_id, _application(db, user_id, application_id))


def list_applications(db: Session, *, user_id: UUID, q=None, status=None, job_id=None,
                      next_action=None, overdue=None, sort="updated_desc", page=1, page_size=20):
    _owner(user_id)
    now = _now()
    filters = [Application.user_id == user_id]
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(or_(*(getattr(Application, key).ilike(f"%{escaped}%", escape="\\")
                             for key in ("job_title", "company", "location"))))
    if status is not None:
        filters.append(Application.status == status)
    if job_id is not None:
        filters.append(Application.job_id == job_id)
    if next_action is not None:
        filters.append(Application.action_title.is_(None) if next_action == "none" else
            _pending_action_predicate() if next_action == "pending" else Application.action_status == next_action)
    if overdue is not None:
        filters.append(_overdue_predicate(now) if overdue else ~_overdue_predicate(now))
    orders = {
        "updated_desc": [Application.updated_at.desc()],
        "created_desc": [Application.created_at.desc()],
        "submitted_desc": [Application.submitted_at.desc().nullslast()],
        "next_action_asc": [case((_pending_action_predicate(), 0), else_=1),
                            Application.action_due_date.asc().nullslast(),
                            func.coalesce(Application.action_due_time, "24:00").asc()],
    }
    total = db.scalar(select(func.count()).select_from(Application).where(*filters))
    rows = list(db.scalars(select(Application).where(*filters).order_by(*orders[sort], Application.id)
        .offset((page - 1) * page_size).limit(page_size)))
    # A bounded page uses batch metadata reads rather than a job/match/profile
    # query per row. Historical evaluations and JD snapshots are never loaded.
    jobs = {row.id: row for row in db.scalars(select(SavedJob).options(load_only(
        SavedJob.id, SavedJob.revision, SavedJob.jd_version)).where(
        SavedJob.user_id == user_id, SavedJob.id.in_({row.job_id for row in rows})))} if rows else {}
    match_ids = {row.match_id for row in rows if row.match_id is not None}
    matches = {row.id: row for row in db.scalars(select(JobMatch).options(load_only(
        JobMatch.id, JobMatch.job_id, JobMatch.created_at, JobMatch.profile_revision,
        JobMatch.job_revision, JobMatch.jd_version, JobMatch.rule_version)).where(
        JobMatch.user_id == user_id, JobMatch.id.in_(match_ids)))} if match_ids else {}
    revision = (db.scalar(select(CareerProfile.revision).where(CareerProfile.user_id == user_id)) or 0) if matches else 0
    return ApplicationListResponse(items=[_response(db, user_id, row, jobs=jobs, matches=matches,
        profile_revision=revision, now=now) for row in rows],
                                   total=total, page=page, page_size=page_size)


def list_events(db: Session, *, user_id: UUID, application_id: UUID, page=1, page_size=20):
    _application(db, user_id, application_id)
    filters = [ApplicationEvent.application_id == application_id, ApplicationEvent.user_id == user_id]
    total = db.scalar(select(func.count()).select_from(ApplicationEvent).where(*filters))
    # Revision is the stable record order when different events share a timestamp.
    rows = db.scalars(select(ApplicationEvent).where(*filters)
        .order_by(ApplicationEvent.revision.desc(), ApplicationEvent.recorded_at.desc(), ApplicationEvent.id)
        .offset((page - 1) * page_size).limit(page_size))
    return ApplicationEventListResponse(items=[ApplicationEventResponse(id=row.id, type=row.type,
        revision=row.revision, changes=row.changes, occurred_at=_utc(row.occurred_at), recorded_at=_utc(row.recorded_at))
        for row in rows], total=total, page=page, page_size=page_size)


def _digest(operation, application_id, payload):
    value = {"operation": operation, "application_id": str(application_id) if application_id else None,
             "payload": payload.model_dump(mode="json", exclude={"idempotency_key"})}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _receipt(db, user_id, key):
    return db.scalar(select(ApplicationReceipt).where(ApplicationReceipt.user_id == user_id,
        ApplicationReceipt.idempotency_key == key).execution_options(populate_existing=True))


def _replay(db, user_id, receipt, digest, operation):
    if receipt.request_digest != digest or receipt.operation != operation:
        raise ApplicationError("application_idempotency_conflict", "该请求标识已用于其他操作，请使用新的请求标识", 409)
    if operation == "delete":
        return None
    if receipt.application_id is None:
        raise ApplicationError("application_deleted", "这次请求对应的申请已删除，请明确重新创建", 409)
    return get_application(db, user_id=user_id, application_id=receipt.application_id)


def _add_receipt(db, user_id, application_id, payload, operation, digest):
    # At the write quota users can still delete. Deletion removes update-only
    # receipts; create/delete tombstones retain no personal content and persist.
    if operation != "delete":
        count = db.scalar(select(func.count()).select_from(ApplicationReceipt).where(ApplicationReceipt.user_id == user_id))
        if count >= MAX_RECEIPTS_PER_USER:
            raise ApplicationError("application_request_limit", "申请操作记录已达上限，请先整理记录或联系管理员", 409)
    db.add(ApplicationReceipt(user_id=user_id, application_id=application_id,
        idempotency_key=payload.idempotency_key, operation=operation, request_digest=digest, created_at=_now()))


def _append_event(db, row, event_type, before, after, occurred_at, recorded_at):
    db.add(ApplicationEvent(user_id=row.user_id, application_id=row.id, revision=row.revision,
        type=event_type, changes={"before": before, "after": after}, occurred_at=occurred_at,
        recorded_at=recorded_at))


def _recover_race(db, user_id, key, digest, operation):
    db.rollback()
    receipt = _receipt(db, user_id, key)
    if receipt:
        return _replay(db, user_id, receipt, digest, operation)
    raise ApplicationError("application_state_conflict", "申请状态正在更新，或该职位已有进行中的申请，请刷新后核对", 409) from None


def create_application(db: Session, *, user_id: UUID, payload: ApplicationCreateRequest):
    _owner(user_id)
    digest = _digest("create", None, payload)
    try:
        _lock_owner(db, user_id)
        receipt = _receipt(db, user_id, payload.idempotency_key)
        if receipt:
            result = _replay(db, user_id, receipt, digest, "create")
            db.commit()
            return result
        job = _job(db, user_id, payload.job_id)
        changed = db.execute(update(SavedJob).where(SavedJob.id == job.id, SavedJob.user_id == user_id,
            SavedJob.revision == payload.job_revision).values(revision=SavedJob.revision)
            .execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise ApplicationError("job_revision_conflict", "职位已更新，请重新加载后再创建申请", 409)
        active = db.scalar(select(Application.id).where(Application.user_id == user_id,
            Application.job_id == job.id, Application.active_slot == 1))
        if active is not None:
            raise ApplicationError("application_already_exists", "该职位已有进行中的申请，请打开现有记录", 409)
        attempt = db.scalar(select(func.max(Application.attempt_number)).where(Application.user_id == user_id,
                              Application.job_id == job.id)) or 0
        if attempt and not payload.reapply:
            raise ApplicationError("application_reapply_confirmation", "该职位已有历史申请，请明确选择再次投递", 409)
        if attempt >= MAX_ATTEMPTS_PER_JOB:
            raise ApplicationError("application_attempt_limit", "该职位已达100次申请上限，请整理历史记录", 409)
        count = db.scalar(select(func.count()).select_from(Application).where(Application.user_id == user_id))
        if count >= MAX_APPLICATIONS_PER_USER:
            raise ApplicationError("application_limit", "申请记录已达1000条上限，请先整理现有记录", 409)
        if payload.match_id is not None:
            match = db.scalar(select(JobMatch.id).where(JobMatch.id == payload.match_id,
                JobMatch.user_id == user_id, JobMatch.job_id == job.id))
            if match is None:
                raise ApplicationError("match_not_found", "匹配结果不存在或不属于当前职位", 404)
        now = _now()
        row = Application(user_id=user_id, job_id=job.id, job_title=job.title, company=job.company,
            location=job.location, match_id=payload.match_id, attempt_number=attempt + 1,
            status="preparing", active_slot=1, revision=1, notes="", created_at=now, updated_at=now)
        db.add(row)
        db.flush()
        _append_event(db, row, "application_created", None,
            {"status": row.status, "job_id": str(job.id), "attempt_number": row.attempt_number,
             "match_id": str(row.match_id) if row.match_id else None}, now, now)
        _add_receipt(db, user_id, row.id, payload, "create", digest)
        db.commit()
        return get_application(db, user_id=user_id, application_id=row.id)
    except IntegrityError:
        return _recover_race(db, user_id, payload.idempotency_key, digest, "create")
    except Exception:
        db.rollback()
        raise


def _claim(db, user_id, application_id, revision):
    changed = db.execute(update(Application).where(Application.id == application_id,
        Application.user_id == user_id, Application.revision == revision, Application.revision <= MAX_REVISION)
        .values(revision=Application.revision + 1, updated_at=_now()).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        _application(db, user_id, application_id)
        raise _conflict()
    return _application(db, user_id, application_id)


def update_application(db: Session, *, user_id: UUID, application_id: UUID, payload: ApplicationUpdateRequest):
    _owner(user_id)
    digest = _digest("update", application_id, payload)
    try:
        _lock_owner(db, user_id)
        receipt = _receipt(db, user_id, payload.idempotency_key)
        if receipt:
            result = _replay(db, user_id, receipt, digest, "update")
            db.commit()
            return result
        before = _application(db, user_id, application_id)
        # Save scalar values before populate_existing refreshes the claimed row.
        old = {"status": before.status, "submitted_at": _utc(before.submitted_at), "notes": before.notes,
               "next_action": _action(before)}
        row = _claim(db, user_id, application_id, payload.revision)
        if payload.status not in TERMINAL_STATUSES:
            other_active = db.scalar(select(Application.id).where(Application.user_id == user_id,
                Application.job_id == row.job_id, Application.id != row.id, Application.active_slot == 1))
            if other_active is not None:
                raise ApplicationError("application_already_exists", "该职位已有另一条进行中的申请，请先结束那条记录再恢复本次申请", 409)
        now = _now()
        occurred_at = payload.occurred_at or now
        count = db.scalar(select(func.count()).select_from(ApplicationEvent).where(
            ApplicationEvent.user_id == user_id, ApplicationEvent.application_id == application_id))
        changes = []
        if old["status"] != payload.status:
            changes.append(("status_changed", old["status"], payload.status))
        if old["submitted_at"] != payload.submitted_at:
            changes.append(("submitted_at_changed", old["submitted_at"].isoformat() if old["submitted_at"] else None,
                            payload.submitted_at.isoformat() if payload.submitted_at else None))
        if old["notes"] != payload.notes:
            changes.append(("notes_changed", old["notes"], payload.notes))
        old_action = old["next_action"].model_dump(mode="json") if old["next_action"] else None
        action = payload.next_action
        # Ending an application also ends its unfinished action. The effective
        # state and its event share the same CAS/receipt transaction. Reopening
        # does not silently revive that action; users can restore it explicitly.
        if action is not None and action.status == "pending" and payload.status in TERMINAL_STATUSES:
            action = action.model_copy(update={"status": "cancelled"})
        new_action = action.model_dump(mode="json") if action else None
        if old_action != new_action:
            changes.append(("next_action_changed", old_action, new_action))
        if count + len(changes) > MAX_EVENTS_PER_APPLICATION:
            raise ApplicationError("application_history_limit", "该申请的历史记录已达上限，请联系管理员", 409)
        # No ordering restriction: corrections and direct stage jumps are valid.
        row.status = payload.status
        row.active_slot = None if payload.status in TERMINAL_STATUSES else 1
        row.submitted_at = payload.submitted_at
        row.notes = payload.notes
        row.action_title = action.title if action else None
        row.action_notes = action.notes if action else None
        row.action_due_date = action.due_date if action else None
        row.action_due_time = action.due_time if action else None
        row.action_status = action.status if action else None
        for event_type, old_value, new_value in changes:
            _append_event(db, row, event_type, old_value, new_value, occurred_at, now)
        _add_receipt(db, user_id, row.id, payload, "update", digest)
        db.commit()
        return get_application(db, user_id=user_id, application_id=row.id)
    except IntegrityError:
        return _recover_race(db, user_id, payload.idempotency_key, digest, "update")
    except Exception:
        db.rollback()
        raise


def delete_application(db: Session, *, user_id: UUID, application_id: UUID, payload: ApplicationDeleteRequest):
    _owner(user_id)
    digest = _digest("delete", application_id, payload)
    try:
        _lock_owner(db, user_id)
        receipt = _receipt(db, user_id, payload.idempotency_key)
        if receipt:
            _replay(db, user_id, receipt, digest, "delete")
            db.commit()
            return
        row = _claim(db, user_id, application_id, payload.revision)
        # Update retries against a deleted ID fail 404. They cannot recreate it,
        # so their content-free receipts need not outlive the application.
        db.execute(delete(ApplicationReceipt).where(ApplicationReceipt.user_id == user_id,
            ApplicationReceipt.application_id == row.id, ApplicationReceipt.operation == "update"))
        _add_receipt(db, user_id, row.id, payload, "delete", digest)
        db.flush()
        db.execute(delete(Application).where(Application.id == row.id, Application.user_id == user_id))
        db.commit()
    except IntegrityError:
        _recover_race(db, user_id, payload.idempotency_key, digest, "delete")
    except Exception:
        db.rollback()
        raise
