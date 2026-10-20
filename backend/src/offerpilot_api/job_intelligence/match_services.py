"""Bounded deterministic matching over server-owned, reviewed data only.

Lock order is profile then job. A no-op CAS fences concurrent input edits
without claiming a new input revision. Same-basis calculations share one row;
request receipts never change immutable creation time or latest ordering.
"""

import hashlib
import json
from uuid import UUID

from pydantic import ValidationError

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.db.models import CareerProfile, JobMatch, JobMatchReceipt, SavedJob
from offerpilot_api.job_catalog.services import get_job
from offerpilot_api.job_intelligence import matching
from offerpilot_api.job_intelligence.match_api_schemas import JobMatchCreateRequest, JobMatchHistoryResponse, JobMatchResponse
from offerpilot_api.job_intelligence.match_schemas import MatchEvaluation
from offerpilot_api.profile.services import get_profile

MAX_MATCHES_PER_JOB = 200
MAX_RECEIPTS_PER_JOB = 1000


class MatchError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _owner(user_id):
    if not isinstance(user_id, UUID):
        raise MatchError("authentication_required", "请先登录", 401)


def _job(db, user_id, job_id):
    _owner(user_id)
    row = db.scalar(select(SavedJob).where(SavedJob.id == job_id, SavedJob.user_id == user_id).execution_options(populate_existing=True))
    if row is None:
        raise MatchError("job_not_found", "职位不存在或当前账户无法访问", 404)
    return row


def _response(db, user_id, job, result):
    revision = db.scalar(select(CareerProfile.revision).where(CareerProfile.user_id == user_id)) or 0
    stale = []
    if revision != result.profile_revision:
        stale.append("profile_changed")
    if job.revision != result.job_revision or job.jd_version != result.jd_version:
        stale.append("job_changed")
    if result.rule_version != matching.RULE_VERSION:
        stale.append("rule_changed")
    # Stored JSON is revalidated before crossing the explicit API schema.
    try:
        evaluation = MatchEvaluation.model_validate(result.evaluation)
    except ValidationError:
        raise MatchError("match_result_invalid", "历史匹配结构无法读取，请联系管理员核对", 409) from None
    return JobMatchResponse(id=result.id, job_id=result.job_id,
        profile_revision=result.profile_revision, job_revision=result.job_revision,
        jd_version=result.jd_version, rule_version=result.rule_version, schema_version=result.schema_version,
        created_at=result.created_at, stale=bool(stale), stale_reasons=stale, evaluation=evaluation)


def latest_match(db: Session, *, user_id: UUID, job_id: UUID) -> JobMatchResponse | None:
    job = _job(db, user_id, job_id)
    result = db.scalar(select(JobMatch).where(JobMatch.user_id == user_id, JobMatch.job_id == job.id)
        .order_by(JobMatch.profile_revision.desc(), JobMatch.job_revision.desc(),
                  (JobMatch.rule_version == matching.RULE_VERSION).desc(), JobMatch.created_at.desc(), JobMatch.id.desc()).limit(1))
    return _response(db, user_id, job, result) if result else None


def get_match(db: Session, *, user_id: UUID, job_id: UUID, match_id: UUID) -> JobMatchResponse:
    job = _job(db, user_id, job_id)
    result = db.scalar(select(JobMatch).where(JobMatch.id == match_id, JobMatch.user_id == user_id, JobMatch.job_id == job.id))
    if result is None:
        raise MatchError("match_not_found", "匹配结果不存在或当前账户无法访问", 404)
    return _response(db, user_id, job, result)


def match_history(db: Session, *, user_id: UUID, job_id: UUID, limit: int = 20) -> JobMatchHistoryResponse:
    job = _job(db, user_id, job_id)
    total = db.scalar(select(func.count()).select_from(JobMatch).where(JobMatch.user_id == user_id, JobMatch.job_id == job.id))
    rows = db.scalars(select(JobMatch).where(JobMatch.user_id == user_id, JobMatch.job_id == job.id)
        .order_by(JobMatch.profile_revision.desc(), JobMatch.job_revision.desc(), JobMatch.created_at.desc(), JobMatch.id.desc()).limit(limit))
    return JobMatchHistoryResponse(items=[_response(db, user_id, job, row) for row in rows], total=total)


def _receipt(db, user_id, key):
    return db.scalar(select(JobMatchReceipt).where(JobMatchReceipt.user_id == user_id, JobMatchReceipt.idempotency_key == key))


def _replay(db, user_id, job, receipt, digest):
    if receipt.request_digest != digest or receipt.job_id != job.id:
        raise MatchError("match_idempotency_conflict", "该请求标识已用于不同匹配，请刷新后重试", 409)
    result = db.scalar(select(JobMatch).where(JobMatch.id == receipt.match_id, JobMatch.user_id == user_id, JobMatch.job_id == job.id))
    if result is None:
        raise MatchError("match_not_found", "匹配结果不存在或当前账户无法访问", 404)
    return _response(db, user_id, job, result)


def create_match(db: Session, *, user_id: UUID, job_id: UUID, payload: JobMatchCreateRequest) -> JobMatchResponse:
    _owner(user_id)
    digest = hashlib.sha256(json.dumps({"job_id": str(job_id), "profile_revision": payload.profile_revision,
        "job_revision": payload.job_revision, "rule_version": matching.RULE_VERSION}, sort_keys=True).encode()).hexdigest()
    try:
        job = _job(db, user_id, job_id)
        receipt = _receipt(db, user_id, payload.idempotency_key)
        if receipt:
            return _replay(db, user_id, job, receipt, digest)
        profile_lock = db.execute(update(CareerProfile).where(CareerProfile.user_id == user_id,
            CareerProfile.revision == payload.profile_revision).values(revision=CareerProfile.revision)
            .execution_options(synchronize_session=False))
        if profile_lock.rowcount != 1:
            actual = db.scalar(select(CareerProfile.revision).where(CareerProfile.user_id == user_id))
            if actual is None:
                raise MatchError("profile_not_ready", "请先填写并保存正式个人画像，再执行匹配", 409)
            raise MatchError("profile_revision_conflict", "个人画像已更新，请重新加载后再匹配", 409)
        job_lock = db.execute(update(SavedJob).where(SavedJob.id == job_id, SavedJob.user_id == user_id,
            SavedJob.revision == payload.job_revision).values(revision=SavedJob.revision).execution_options(synchronize_session=False))
        if job_lock.rowcount != 1:
            _job(db, user_id, job_id)
            raise MatchError("job_revision_conflict", "职位条件已更新，请重新加载后再匹配", 409)
        # A waiting concurrent request may now have committed a receipt.
        receipt = _receipt(db, user_id, payload.idempotency_key)
        job = _job(db, user_id, job_id)
        if receipt:
            response = _replay(db, user_id, job, receipt, digest)
            db.commit()
            return response
        profile = get_profile(db, user_id=user_id)
        prefs = profile.preferences.model_dump(mode="json")
        if not any((profile.education, profile.skills, profile.projects, profile.experiences)) and not any(prefs.values()):
            raise MatchError("profile_not_ready", "请先填写并保存正式个人画像，再执行匹配", 409)
        detail = get_job(db, user_id=user_id, job_id=job_id)
        if not any(row.review_status == "confirmed" and not row.stale for row in detail.requirements):
            raise MatchError("reviewed_requirements_required", "请先审核至少一条当前 JD 条件，再执行匹配", 409)
        result = db.scalar(select(JobMatch).where(JobMatch.user_id == user_id, JobMatch.job_id == job_id,
            JobMatch.profile_revision == payload.profile_revision, JobMatch.job_revision == payload.job_revision,
            JobMatch.rule_version == matching.RULE_VERSION))
        if result is None:
            count = db.scalar(select(func.count()).select_from(JobMatch).where(JobMatch.user_id == user_id, JobMatch.job_id == job_id))
            if count >= MAX_MATCHES_PER_JOB:
                raise MatchError("match_limit_reached", "该职位的历史匹配已达上限，请保留所需结果后联系管理员", 409)
            evaluated = matching.evaluate_match(profile, detail)
            try:
                evaluation = MatchEvaluation.model_validate(evaluated.model_dump(mode="json", warnings=False))
            except (ValidationError, AttributeError, TypeError):
                raise MatchError("match_rule_output_invalid", "匹配规则输出未通过校验，请稍后重试", 503) from None
            if evaluation.rule_version != matching.RULE_VERSION:
                raise MatchError("match_rule_version_conflict", "匹配规则版本已变化，请重新计算", 409)
            result = JobMatch(user_id=user_id, job_id=job_id, profile_revision=profile.revision,
                job_revision=detail.revision, jd_version=detail.jd_version, rule_version=evaluation.rule_version,
                schema_version=evaluation.schema_version, evaluation=evaluation.model_dump(mode="json"))
            db.add(result)
            db.flush()
        receipt_count = db.scalar(select(func.count()).select_from(JobMatchReceipt).where(JobMatchReceipt.user_id == user_id, JobMatchReceipt.job_id == job_id))
        if receipt_count >= MAX_RECEIPTS_PER_JOB:
            raise MatchError("match_request_limit_reached", "该职位的匹配请求记录已达上限，请使用已有结果", 409)
        db.add(JobMatchReceipt(user_id=user_id, job_id=job_id, match_id=result.id,
            idempotency_key=payload.idempotency_key, request_digest=digest))
        db.commit()
        return get_match(db, user_id=user_id, job_id=job_id, match_id=result.id)
    except IntegrityError:
        db.rollback()
        # A UNIQUE winner is returned only for an exact owner-scoped request.
        job = _job(db, user_id, job_id)
        receipt = _receipt(db, user_id, payload.idempotency_key)
        if receipt:
            return _replay(db, user_id, job, receipt, digest)
        raise MatchError("match_state_conflict", "匹配依据正在更新，请刷新后重试", 409) from None
    except Exception:
        db.rollback()
        raise


def remove_resume_match_evidence(db: Session, *, user_id: UUID, resume_id: UUID) -> None:
    """Erase retained original excerpts, keeping confirmed values and decisions.

    Called in the existing resume deletion transaction after the profile lock.
    """
    rows = db.scalars(select(JobMatch).where(JobMatch.user_id == user_id)).all()
    for row in rows:
        value = json.loads(json.dumps(row.evaluation))
        changed = False
        for item in value.get("items", []):
            for evidence in item.get("profile_evidence", []):
                if evidence.get("resume_id") == str(resume_id):
                    evidence.update(resume_id=None, source_text=None)
                    # Provenance belongs to the individual field. A user's
                    # correction can still share the entry's source resume ID
                    # without becoming a resume-authored fact.
                    if evidence.get("source_kind") in {"resume", "resume_confirmed"}:
                        evidence["source_kind"] = "resume_deleted"
                    changed = True
        if changed:
            row.evaluation = value
    db.flush()
