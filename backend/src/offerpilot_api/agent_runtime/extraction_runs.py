"""Durable DB-claimed Mock runs with bounded leases and atomic candidate writes.

Only the fixed synthetic Mock adapter can be selected by HTTP configuration.
The dispatcher is a convenience: state and correctness live in the database,
and a finite local CLI can process/recover the same work after an interruption.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from offerpilot_api.agent_runtime.extraction_schemas import (
    ExtractionCapabilityResponse, ExtractionRunCandidatesResponse,
    ExtractionRunCreateRequest, ExtractionRunResponse,
)
from offerpilot_api.agent_runtime.llm_provider import LLMProvider, LLMProviderError
from offerpilot_api.core.config import Settings
from offerpilot_api.db.models import ProfileCandidate, ResumeDocument, ResumeExtractionRun
from offerpilot_api.profile.services import get_profile, ingest_profile_candidates

EXTRACTION_VERSION = "resume-extraction-v2.0"
PROVIDER_VERSION = "mock-synthetic-fixture-v2"
PROMPT_VERSION = "resume_extract_v2"
ACTIVE = ("queued", "running")
SAFE_FAILURE_CODES = {
    "schema_validation_failed", "invalid_json", "model_response_exceeds_limit",
    "resume_text_empty", "resume_text_exceeds_limit", "source_text_not_in_resume",
    "mock_fixture_not_found", "evidence_validation_failed", "llm_call_failed",
    "source_version_changed", "call_limit_reached", "lease_expired", "run_cancelled",
    "extraction_validation_failed", "candidate_limit_reached", "invalid_candidate_batch",
    "unmappable_extraction_fields", "invalid_candidate_metadata", "invalid_candidate_mapping",
    "candidate_source_version_conflict", "invalid_candidate_run",
}


class ExtractionRunError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _owner(user_id: UUID) -> None:
    if not isinstance(user_id, UUID):
        raise ExtractionRunError("authentication_required", "请先登录", 401)


def _missing() -> ExtractionRunError:
    return ExtractionRunError("extraction_resource_not_found", "抽取资源不存在", 404)


def source_version(document: ResumeDocument) -> str:
    """Recompute from current server text/file/layout, never a cached client hash."""
    content = {
        "text_sha256": hashlib.sha256((document.raw_text or "").encode("utf-8")).hexdigest(),
        "file_sha256": document.sha256,
        "layout": document.text_layout,
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _document(db: Session, user_id: UUID, resume_id: UUID, *, lock: bool = False) -> ResumeDocument:
    if lock:
        result = db.execute(update(ResumeDocument).where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.processing_status == "parsed", ResumeDocument.lifecycle_status == "active",
        ).values(lifecycle_status=ResumeDocument.lifecycle_status).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise _missing()
        document = db.get(ResumeDocument, resume_id, populate_existing=True)
    else:
        document = db.scalar(select(ResumeDocument).where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.processing_status == "parsed", ResumeDocument.lifecycle_status == "active",
        ))
    if document is None or not document.raw_text:
        raise _missing()
    return document


def _run(db: Session, user_id: UUID, run_id: UUID) -> ResumeExtractionRun:
    _owner(user_id)
    run = db.scalar(select(ResumeExtractionRun).where(ResumeExtractionRun.id == run_id, ResumeExtractionRun.user_id == user_id))
    if run is None:
        raise _missing()
    return run


def _response(run: ResumeExtractionRun) -> ExtractionRunResponse:
    return ExtractionRunResponse.model_validate(run)


def recover_expired_runs(db: Session, *, user_id: UUID | None = None, now: datetime | None = None, limit: int = 100) -> int:
    """Expire leases without replaying provider calls or trusting a process lock."""
    if not 1 <= limit <= 100:
        raise ValueError("Recovery batch limit must be 1..100")
    now = now or _now()
    query = select(ResumeExtractionRun.id).where(
        ResumeExtractionRun.status == "running", ResumeExtractionRun.lease_deadline <= now,
    ).order_by(ResumeExtractionRun.lease_deadline, ResumeExtractionRun.id).limit(limit)
    if user_id is not None:
        _owner(user_id)
        query = query.where(ResumeExtractionRun.user_id == user_id)
    ids = list(db.scalars(query))
    if not ids:
        db.rollback()
        return 0
    changed = db.execute(update(ResumeExtractionRun).where(
        ResumeExtractionRun.id.in_(ids), ResumeExtractionRun.status == "running", ResumeExtractionRun.lease_deadline <= now,
    ).values(status="failed", revision=ResumeExtractionRun.revision + 1, error_code="lease_expired",
             execution_token=None, lease_deadline=None, finished_at=now, updated_at=now).execution_options(synchronize_session=False))
    db.commit()
    return changed.rowcount


def get_extraction_run(db: Session, *, user_id: UUID, run_id: UUID) -> ExtractionRunResponse:
    _run(db, user_id, run_id)  # Authorize before recovery.
    recover_expired_runs(db, user_id=user_id)
    return _response(_run(db, user_id, run_id))


def get_extraction_capability(db: Session, *, user_id: UUID, resume_id: UUID, settings: Settings) -> ExtractionCapabilityResponse:
    _owner(user_id)
    _document(db, user_id, resume_id)
    recover_expired_runs(db, user_id=user_id)
    document = _document(db, user_id, resume_id)
    recent = db.scalars(select(ResumeExtractionRun).where(
        ResumeExtractionRun.user_id == user_id, ResumeExtractionRun.resume_id == resume_id,
    ).order_by(ResumeExtractionRun.created_at.desc(), ResumeExtractionRun.id).limit(10)).all()
    return ExtractionCapabilityResponse(
        enabled=settings.mock_extraction_available,
        mode="mock" if settings.mock_extraction_available else "disabled",
        test_result=settings.mock_extraction_available,
        source_version=source_version(document), recent_runs=[_response(run) for run in recent],
    )


def _enabled(settings: Settings) -> None:
    if not settings.mock_extraction_available:
        raise ExtractionRunError("mock_extraction_disabled", "合成 Mock 测试模式未开启；真实 AI 抽取尚未启用", 503)


def _admission(db: Session, user_id: UUID, settings: Settings) -> None:
    # SQLite's writer lock (held through resume mutation) serializes admission
    # checks. PostgreSQL admission/concurrency remains a separate release gate.
    count = db.scalar(select(func.count()).select_from(ResumeExtractionRun).where(ResumeExtractionRun.status.in_(ACTIVE))) or 0
    owned = db.scalar(select(func.count()).select_from(ResumeExtractionRun).where(
        ResumeExtractionRun.user_id == user_id, ResumeExtractionRun.status.in_(ACTIVE),
    )) or 0
    if count >= settings.extraction_max_active_total or owned >= settings.extraction_max_active_per_user:
        raise ExtractionRunError("extraction_capacity_reached", "测试任务已达到运行或排队上限，请完成或取消已有任务", 429)


def create_extraction_run(db: Session, *, user_id: UUID, resume_id: UUID, payload: ExtractionRunCreateRequest, settings: Settings) -> ExtractionRunResponse:
    _owner(user_id)
    _enabled(settings)
    recover_expired_runs(db, user_id=user_id)
    try:
        document = _document(db, user_id, resume_id, lock=True)
        current_version = source_version(document)
        if current_version != payload.source_version:
            raise ExtractionRunError("source_version_changed", "简历版本已变化，请刷新后重试", 409)
        prior = db.scalar(select(ResumeExtractionRun).where(
            ResumeExtractionRun.user_id == user_id, ResumeExtractionRun.idempotency_key == payload.idempotency_key,
        ))
        if prior is not None:
            if prior.resume_id != resume_id or prior.source_version != current_version:
                raise ExtractionRunError("extraction_idempotency_conflict", "该请求键已用于不同简历版本", 409)
            result = _response(prior)
            db.commit()
            return result
        prior_source = db.scalar(select(ResumeExtractionRun).where(
            ResumeExtractionRun.resume_id == resume_id, ResumeExtractionRun.source_version == current_version,
            ResumeExtractionRun.extraction_version == EXTRACTION_VERSION,
        ))
        if prior_source is not None:
            raise ExtractionRunError("extraction_already_exists", "该版本已有抽取任务，请查看或重试已有任务", 409)
        _admission(db, user_id, settings)
        run = ResumeExtractionRun(
            user_id=user_id, resume_id=resume_id, idempotency_key=payload.idempotency_key,
            source_version=current_version, max_attempts=settings.extraction_max_attempts,
            max_calls=settings.extraction_max_calls, timeout_seconds=settings.extraction_timeout_seconds,
            schema_version="2.0", extraction_version=EXTRACTION_VERSION,
            prompt_version=PROMPT_VERSION, provider_version=PROVIDER_VERSION,
        )
        db.add(run)
        db.flush()
        result = _response(run)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def mutate_extraction_run(db: Session, *, user_id: UUID, run_id: UUID, revision: int, action: str, settings: Settings) -> ExtractionRunResponse:
    _owner(user_id)
    _run(db, user_id, run_id)
    recover_expired_runs(db, user_id=user_id)
    try:
        run = _run(db, user_id, run_id)
        # Follow resume → run → profile ordering even for cancellation.
        if action == "retry":
            _enabled(settings)
            document = _document(db, user_id, run.resume_id, lock=True)
            if source_version(document) != run.source_version:
                raise ExtractionRunError("source_version_changed", "简历版本已变化，不能重试旧任务", 409)
            if run.status != "failed" or run.attempt_count >= run.max_attempts or run.call_count >= run.max_calls:
                raise ExtractionRunError("extraction_retry_unavailable", "该任务无法重试或已达到次数上限", 409)
            _admission(db, user_id, settings)
            allowed = ("failed",)
            values = dict(status="queued", error_code=None, finished_at=None)
        elif action == "cancel":
            db.execute(update(ResumeDocument).where(
                ResumeDocument.id == run.resume_id, ResumeDocument.user_id == user_id,
            ).values(lifecycle_status=ResumeDocument.lifecycle_status).execution_options(synchronize_session=False))
            allowed = ACTIVE
            values = dict(status="cancelled", error_code="run_cancelled", finished_at=_now())
        else:
            raise ValueError("Unknown extraction action")
        changed = db.execute(update(ResumeExtractionRun).where(
            ResumeExtractionRun.id == run_id, ResumeExtractionRun.user_id == user_id,
            ResumeExtractionRun.revision == revision, ResumeExtractionRun.status.in_(allowed),
        ).values(**values, execution_token=None, lease_deadline=None,
                 revision=ResumeExtractionRun.revision + 1, updated_at=_now()).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise ExtractionRunError("extraction_state_conflict", "任务状态已变化，请刷新后重试", 409)
        db.commit()
        return _response(db.get(ResumeExtractionRun, run_id, populate_existing=True))
    except Exception:
        db.rollback()
        raise


def cancel_resume_extraction_runs(db: Session, *, user_id: UUID, resume_id: UUID) -> None:
    """Part of deletion intent; caller already holds resume lock and owns commit."""
    now = _now()
    db.execute(update(ResumeExtractionRun).where(
        ResumeExtractionRun.user_id == user_id, ResumeExtractionRun.resume_id == resume_id,
        ResumeExtractionRun.status.in_(ACTIVE),
    ).values(status="cancelled", revision=ResumeExtractionRun.revision + 1, error_code="resume_deleted",
             execution_token=None, lease_deadline=None, finished_at=now, updated_at=now).execution_options(synchronize_session=False))
    db.execute(update(ResumeExtractionRun).where(
        ResumeExtractionRun.user_id == user_id, ResumeExtractionRun.resume_id == resume_id,
    ).values(candidate_count=0).execution_options(synchronize_session=False))


def get_run_candidates(db: Session, *, user_id: UUID, run_id: UUID) -> ExtractionRunCandidatesResponse:
    run = get_extraction_run(db, user_id=user_id, run_id=run_id)
    ids = set(db.scalars(select(ProfileCandidate.id).where(ProfileCandidate.extraction_run_id == run_id)))
    profile = get_profile(db, user_id=user_id)
    return ExtractionRunCandidatesResponse(run=run, candidates=[candidate for candidate in profile.candidates if candidate.id in ids])


@dataclass(frozen=True)
class ClaimedExtractionRun:
    id: UUID
    user_id: UUID
    resume_id: UUID
    token: UUID
    source_version: str
    deadline: datetime
    raw_text: str
    text_layout: dict | None


def claim_extraction_run(db: Session, *, run_id: UUID, now: datetime | None = None) -> ClaimedExtractionRun | None:
    now = now or _now()
    run = db.get(ResumeExtractionRun, run_id)
    if run is None or run.status != "queued":
        db.rollback()
        return None
    try:
        document = _document(db, run.user_id, run.resume_id, lock=True)
        if source_version(document) != run.source_version:
            raise ExtractionRunError("source_version_changed", "简历版本已变化", 409)
        token = uuid4()
        deadline = now + timedelta(seconds=run.timeout_seconds)
        changed = db.execute(update(ResumeExtractionRun).where(
            ResumeExtractionRun.id == run_id, ResumeExtractionRun.status == "queued",
            ResumeExtractionRun.attempt_count < ResumeExtractionRun.max_attempts,
            ResumeExtractionRun.call_count < ResumeExtractionRun.max_calls,
        ).values(status="running", attempt_count=ResumeExtractionRun.attempt_count + 1,
                 revision=ResumeExtractionRun.revision + 1, execution_token=token, lease_deadline=deadline,
                 error_code=None, finished_at=None, updated_at=now).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            db.rollback()
            return None
        claimed = ClaimedExtractionRun(run.id, run.user_id, run.resume_id, token, run.source_version, deadline, document.raw_text, document.text_layout)
        db.commit()
        return claimed
    except ExtractionRunError as exc:
        db.rollback()
        db.execute(update(ResumeExtractionRun).where(
            ResumeExtractionRun.id == run_id, ResumeExtractionRun.status == "queued",
        ).values(status="cancelled" if exc.status_code == 404 else "failed", error_code="resume_deleted" if exc.status_code == 404 else exc.code,
                 revision=ResumeExtractionRun.revision + 1, finished_at=now, updated_at=now).execution_options(synchronize_session=False))
        db.commit()
        return None
    except Exception:
        db.rollback()
        raise


class _BudgetedProvider:
    def __init__(self, factory: Callable[[], Session], claim: ClaimedExtractionRun, provider: LLMProvider):
        self.factory, self.claim, self.provider = factory, claim, provider

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
        now = _now()
        with self.factory() as db:
            changed = db.execute(update(ResumeExtractionRun).where(
                ResumeExtractionRun.id == self.claim.id, ResumeExtractionRun.status == "running",
                ResumeExtractionRun.execution_token == self.claim.token,
                ResumeExtractionRun.lease_deadline > now, ResumeExtractionRun.call_count < ResumeExtractionRun.max_calls,
            ).values(call_count=ResumeExtractionRun.call_count + 1, updated_at=now).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                db.rollback()
                raise LLMProviderError("Run budget or lease is unavailable", provider_error_type="call_limit_reached")
            # Record attempted calls before invocation, including crashes.
            db.commit()
        return self.provider.generate_json(system_prompt=system_prompt, user_prompt=user_prompt)


def finish_extraction_run(db: Session, *, claim: ClaimedExtractionRun, result) -> bool:
    """Atomic success + candidates; every late result must pass this fence."""
    try:
        document = _document(db, claim.user_id, claim.resume_id, lock=True)
        changed = db.execute(update(ResumeExtractionRun).where(
            ResumeExtractionRun.id == claim.id, ResumeExtractionRun.user_id == claim.user_id,
            ResumeExtractionRun.status == "running", ResumeExtractionRun.execution_token == claim.token,
            ResumeExtractionRun.lease_deadline > _now(),
        ).values(updated_at=_now()).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            db.rollback()
            return False
        if source_version(document) != claim.source_version:
            raise ExtractionRunError("source_version_changed", "简历版本已变化", 409)
        count = len(result.batch.candidates) if result.batch is not None else 0
        if result.batch is not None:
            metadata = [{**item, "test_result": True, "provider": "mock"}
                        for item in result.candidate_metadata]
            ingest_profile_candidates(db, user_id=claim.user_id, resume_id=claim.resume_id,
                                      batch_key=str(claim.id), payload=result.batch, commit=False,
                                      candidate_metadata=metadata, run_id=claim.id)
        completed = db.execute(update(ResumeExtractionRun).where(
            ResumeExtractionRun.id == claim.id, ResumeExtractionRun.execution_token == claim.token,
            ResumeExtractionRun.status == "running", ResumeExtractionRun.lease_deadline > _now(),
        ).values(status="succeeded", candidate_count=count, revision=ResumeExtractionRun.revision + 1,
                 execution_token=None, lease_deadline=None, error_code=None, finished_at=_now(), updated_at=_now())
                   .execution_options(synchronize_session=False))
        if completed.rowcount != 1:
            # Mapping and candidate flush may outlast the lease; discard both
            # candidates and success in the same transaction in that case.
            db.rollback()
            return False
        db.commit()
        return True
    except ExtractionRunError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def fail_extraction_run(db: Session, *, claim: ClaimedExtractionRun, error_code: str) -> None:
    # CAS prevents acknowledgement-loss recovery from changing real success.
    safe = error_code if error_code in SAFE_FAILURE_CODES else "extraction_failed"
    now = _now()
    db.execute(update(ResumeExtractionRun).where(
        ResumeExtractionRun.id == claim.id, ResumeExtractionRun.status == "running",
        ResumeExtractionRun.execution_token == claim.token,
    ).values(status="failed", error_code=safe, execution_token=None, lease_deadline=None,
             revision=ResumeExtractionRun.revision + 1, finished_at=now, updated_at=now).execution_options(synchronize_session=False))
    db.commit()


def mock_provider_factory() -> LLMProvider:
    from offerpilot_api.agent_runtime.providers.mock import MockResumeProvider
    return MockResumeProvider()


def execute_extraction_run(session_factory: Callable[[], Session], *, run_id: UUID, provider_factory: Callable[[], LLMProvider] = mock_provider_factory) -> bool:
    from offerpilot_api.resume_ingestion.extractor import ExtractionDiagnostics
    from offerpilot_api.resume_ingestion.extractor_v2 import extract_student_profile_v2
    with session_factory() as db:
        claim = claim_extraction_run(db, run_id=run_id)
    if claim is None:
        return False
    diagnostics = ExtractionDiagnostics()
    try:
        provider = _BudgetedProvider(session_factory, claim, provider_factory())
        result = extract_student_profile_v2(claim.raw_text, provider, text_layout=claim.text_layout, diagnostics=diagnostics)
        with session_factory() as db:
            completed = finish_extraction_run(db, claim=claim, result=result)
        if not completed:
            with session_factory() as db:
                # A deadline may expire before a query/dispatcher recovery.
                recover_expired_runs(db)
        return completed
    except Exception as exc:
        code = getattr(exc, "code", None) or diagnostics.failure_reason or "extraction_failed"
        with session_factory() as db:
            persisted = db.get(ResumeExtractionRun, claim.id)
            if persisted is not None and persisted.status == "succeeded":
                # The commit may have succeeded before its acknowledgement
                # was lost. Persisted success is authoritative and replay-safe.
                return True
            fail_extraction_run(db, claim=claim, error_code=code)
        # Exceptions/model output are deliberately neither persisted nor logged.
        return False


def process_extraction_batch(session_factory: Callable[[], Session], *, limit: int = 10, provider_factory: Callable[[], LLMProvider] = mock_provider_factory) -> dict[str, int]:
    if not 1 <= limit <= 100:
        raise ValueError("Processing batch limit must be 1..100")
    with session_factory() as db:
        recovered = recover_expired_runs(db, limit=limit)
        ids = list(db.scalars(select(ResumeExtractionRun.id).where(ResumeExtractionRun.status == "queued").order_by(ResumeExtractionRun.created_at, ResumeExtractionRun.id).limit(limit)))
    completed = sum(execute_extraction_run(session_factory, run_id=run_id, provider_factory=provider_factory) for run_id in ids)
    return {"selected": len(ids), "succeeded": completed, "recovered": recovered}


async def extraction_dispatcher(app, session_factory: Callable[[], Session]) -> None:
    """One finite batch per tick; stop by task cancellation on graceful shutdown."""
    while True:
        if app.state.extraction_dispatcher_enabled:
            try:
                await asyncio.to_thread(process_extraction_batch, session_factory, limit=3,
                                        provider_factory=app.state.extraction_provider_factory)
            except SQLAlchemyError:
                # A disabled/unmigrated local DB is actionable through safe API
                # errors; avoid traceback/SQL data in background logs.
                pass
        await asyncio.sleep(0.5)
