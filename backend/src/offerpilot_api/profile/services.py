"""Reviewed profile use cases, atomic revisions, and bounded internal ingestion.

No provider, arbitrary URL, filesystem access, or browser candidate-write API.
Evidence validation is conservative lexical support, never semantic certainty.
"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.db.models import CareerProfile, CareerProfileEntry, ProfileCandidate, ResumeDocument, ResumeExtractionRun
from offerpilot_api.profile.schemas import (
    CandidateBatchV1, CareerProfileResponse, EducationEntry, ExperienceEntry,
    MAX_PROFILE_REVISION, ProfileCandidateReviewRequest, ProfileSaveRequest, ProjectEntry, SkillEntry, VALUE_MODELS,
)
from offerpilot_api.resume_ingestion.evidence import CandidateExtractionMetadata, evaluate_metadata, layout_fingerprint, text_fingerprint

MAX_CANDIDATES = 500
GROUPS = {"education": "education", "skills": "skill", "projects": "project", "experiences": "experience"}
LIMITS = {"education": 20, "skill": 100, "project": 30, "experience": 40}
ENTRY_MODELS = {"education": EducationEntry, "skill": SkillEntry, "project": ProjectEntry, "experience": ExperienceEntry}


class ProfileError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _owner(user_id: UUID) -> None:
    if not isinstance(user_id, UUID):
        raise ProfileError("authentication_required", "请先登录", 401)


def _not_found() -> ProfileError:
    return ProfileError("profile_resource_not_found", "资料不存在或当前账户无法访问", 404)


def _conflict() -> ProfileError:
    return ProfileError("profile_revision_conflict", "资料已在其他页面更新，请保留当前修改并重新加载最新资料", 409)


def _claim_revision(db: Session, *, user_id: UUID, revision: int | None, rollback_on_error: bool = True) -> CareerProfile:
    """CAS locks the sole profile before any child mutation, including initial insert."""
    filters = [CareerProfile.user_id == user_id, CareerProfile.revision <= MAX_PROFILE_REVISION]
    if revision is not None:
        filters.append(CareerProfile.revision == revision)
    changed = db.execute(update(CareerProfile).where(*filters).values(
        revision=CareerProfile.revision + 1, updated_at=_now(),
    ).execution_options(synchronize_session=False))
    if changed.rowcount == 1:
        return db.scalar(select(CareerProfile).where(CareerProfile.user_id == user_id).execution_options(populate_existing=True))
    if revision not in (None, 0) or db.scalar(select(CareerProfile.id).where(CareerProfile.user_id == user_id)):
        raise _conflict()
    profile = CareerProfile(user_id=user_id, revision=1, schema_version="1.0", updated_at=_now())
    db.add(profile)
    try:
        db.flush()
    except IntegrityError:
        # Another writer initialized the UNIQUE(user_id) row first.
        if rollback_on_error:
            db.rollback()
        raise _conflict() from None
    return profile


def _profile(db: Session, user_id: UUID) -> CareerProfile | None:
    return db.scalar(select(CareerProfile).where(CareerProfile.user_id == user_id).execution_options(populate_existing=True))


def get_profile(db: Session, *, user_id: UUID) -> CareerProfileResponse:
    _owner(user_id)
    profile = _profile(db, user_id)
    groups = {group: [] for group in GROUPS}
    candidates = []
    if profile:
        # Reads never trust an FK alone for source ownership or visibility.
        rows = db.execute(select(CareerProfileEntry, ResumeDocument.file_name).outerjoin(
            ResumeDocument,
            (CareerProfileEntry.resume_id == ResumeDocument.id)
            & (ResumeDocument.user_id == user_id)
            & (ResumeDocument.lifecycle_status == "active")
            & (ResumeDocument.processing_status == "parsed"),
        ).where(CareerProfileEntry.profile_id == profile.id).order_by(CareerProfileEntry.position, CareerProfileEntry.id)).all()
        category_groups = {category: group for group, category in GROUPS.items()}
        for entry, file_name in rows:
            # Corrupt/dangling source references are not presented as evidence.
            source_visible = entry.resume_id is not None and file_name is not None
            source_kind = entry.source_kind
            if entry.resume_id is not None and not source_visible:
                source_kind = "resume_deleted"
            sources = {field: ("resume_deleted" if kind == "resume" and not source_visible else kind) for field, kind in entry.field_sources.items()}
            rendered = ENTRY_MODELS[entry.category].model_validate({
                **entry.value, "id": entry.id,
                "source": {"kind": source_kind, "resume_id": entry.resume_id if source_visible else None,
                           "resume_file_name": file_name, "field_sources": sources,
                           "evidence": entry.evidence if source_visible else [],
                           "test_result": bool(entry.extraction_metadata.get("test_result")),
                           "provider": entry.extraction_metadata.get("provider"),
                           "model": entry.extraction_metadata.get("model"), "run_id": entry.extraction_metadata.get("run_id")},
            })
            groups[category_groups[entry.category]].append(rendered)
        candidate_rows = db.execute(select(ProfileCandidate, ResumeDocument.file_name).join(
            ResumeDocument, ProfileCandidate.resume_id == ResumeDocument.id,
        ).where(
            ProfileCandidate.profile_id == profile.id, ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status == "active", ResumeDocument.processing_status == "parsed",
        ).order_by(ProfileCandidate.created_at, ProfileCandidate.item_index, ProfileCandidate.id).limit(MAX_CANDIDATES)).all()
        for candidate, file_name in candidate_rows:
            metadata = candidate.extraction_metadata or None
            evidence_status = candidate.evidence_status
            if metadata:
                source = db.get(ResumeDocument, candidate.resume_id)
                if (metadata.get("source_text_sha256") != text_fingerprint(source.raw_text or "")
                    or metadata.get("source_layout_sha256") != layout_fingerprint(source.raw_text or "", source.text_layout)
                    or metadata.get("source_file_sha256") != source.sha256):
                    metadata = {**metadata, "evidence_status": "needs_review", "review_reasons": list(dict.fromkeys([*metadata.get("review_reasons", []), "source_version_changed"]))[:20]}
                    evidence_status = "needs_review"
            candidates.append({
                "id": candidate.id, "category": candidate.category,
                "value": VALUE_MODELS[candidate.category].model_validate(candidate.value),
                "status": candidate.status, "evidence_status": evidence_status,
                "evidence": candidate.evidence, "resume_id": candidate.resume_id,
                "resume_file_name": file_name, "schema_version": candidate.schema_version,
                "extraction_version": candidate.extraction_version, "entry_id": candidate.entry_id,
                "created_at": candidate.created_at, "reviewed_at": candidate.reviewed_at,
                "extraction_run_id": candidate.extraction_run_id, "extraction_metadata": metadata,
            })
    missing = []
    for field in ("school", "degree", "major", "end_date"):
        if not any(getattr(entry, field) is not None for entry in groups["education"]):
            missing.append(f"education.{field}")
    if not any(entry.name for entry in groups["skills"]):
        missing.append("skills.name")
    if not any(entry.name for entry in groups["projects"]):
        missing.append("projects.name")
    if not any(entry.organization for entry in groups["experiences"]):
        missing.append("experiences.organization")
    return CareerProfileResponse(
        revision=profile.revision if profile else 0, updated_at=profile.updated_at if profile else None,
        **groups, candidates=candidates,
        completion={"populated_groups": [group for group, items in groups.items() if items],
                    "missing_fields": missing, "pending_count": sum(item["status"] == "pending" for item in candidates)},
    )


def _manual_fields(value: dict) -> dict[str, str]:
    return {field: "manual" for field, provided in value.items() if provided is not None}


def save_profile(db: Session, *, user_id: UUID, payload: ProfileSaveRequest) -> CareerProfileResponse:
    _owner(user_id)
    try:
        profile = _claim_revision(db, user_id=user_id, revision=payload.revision)
        existing = {entry.id: entry for entry in db.scalars(select(CareerProfileEntry).where(CareerProfileEntry.profile_id == profile.id)).all()}
        retained = set()
        for group, category in GROUPS.items():
            for position, item in enumerate(getattr(payload, group)):
                value = VALUE_MODELS[category].model_validate(item.model_dump(exclude={"id"})).model_dump()
                if item.id is not None:
                    entry = existing.get(item.id)
                    if entry is None or entry.category != category:
                        raise _not_found()
                    retained.add(entry.id)
                    changed_fields = {field for field in value if value[field] != entry.value.get(field)}
                    if changed_fields:
                        entry.field_sources = {
                            field: ("manual" if field in changed_fields else entry.field_sources.get(field, "manual"))
                            for field, provided in value.items() if provided is not None
                        }
                        entry.evidence = [
                            {**evidence, "supports_current_value": False if evidence["field"] in changed_fields else evidence["supports_current_value"]}
                            for evidence in entry.evidence
                        ]
                        if entry.source_kind in ("resume_confirmed", "user_modified"):
                            entry.source_kind = "user_modified"
                        entry.value = value
                        entry.updated_at = _now()
                    entry.position = position
                else:
                    db.add(CareerProfileEntry(
                        profile_id=profile.id, category=category, value=value, position=position,
                        source_kind="manual", resume_id=None, field_sources=_manual_fields(value), evidence=[], updated_at=_now(),
                    ))
        for entry_id, entry in existing.items():
            if entry_id not in retained:
                db.delete(entry)
        db.commit()
        return get_profile(db, user_id=user_id)
    except (ProfileError, ValidationError):
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


# Negation/uncertainty detection only removes eligibility for one-click confirm.
# Absence of these tokens is not a correctness guarantee; explicit review remains
# mandatory. Future semantic evaluation must not weaken this lexical fence.
_UNCERTAIN = re.compile(
    r"\b(?:not|never|no|without|lack|lacking|unable|unfamiliar|unknown|maybe|might|planned|planning|intend|intended)\b"
    r"|(?:不|未|无|没有|非熟练|计划|拟学习|待学习|可能|未知|待确认)",
    re.IGNORECASE,
)
_SENTENCE_BREAKS = ".!?。！？;；\r\n"
MAX_EVIDENCE_OCCURRENCES = 20
MAX_CONTEXT_SIDE_CHARS = 500


def _source_occurrences(source: str, raw_text: str) -> tuple[list[tuple[int, str]], bool]:
    """Inspect server text around every quoted occurrence, with strict bounds.

    Quotes have no trusted offset. Repeated, contradictory or truncated context
    cannot be selected away by the model. More than twenty occurrences or a
    sentence too long for this bounded check requires user-provided correction.
    Context is transient and never persisted or returned in errors/logs.
    """
    occurrences = []
    position = 0
    ambiguous = False
    for _ in range(MAX_EVIDENCE_OCCURRENCES):
        start = raw_text.find(source, position)
        if start < 0:
            return occurrences, ambiguous
        end = start + len(source)
        lower, upper = max(0, start - MAX_CONTEXT_SIDE_CHARS), min(len(raw_text), end + MAX_CONTEXT_SIDE_CHARS)
        left = raw_text[lower:start]
        right = raw_text[end:upper]
        left_break = max((left.rfind(mark) for mark in _SENTENCE_BREAKS), default=-1)
        right_breaks = [index for mark in _SENTENCE_BREAKS if (index := right.find(mark)) >= 0]
        right_break = min(right_breaks) if right_breaks else None
        if (left_break < 0 and lower > 0) or (right_break is None and upper < len(raw_text)):
            ambiguous = True
        context_start = lower + left_break + 1
        context_end = end + right_break + 1 if right_break is not None else upper
        occurrences.append((start, raw_text[context_start:context_end]))
        # Advance by one so overlapping occurrences cannot hide a contrary
        # context, while the fixed occurrence budget bounds the work.
        position = start + 1
    return occurrences, ambiguous or raw_text.find(source, position) >= 0


def _word_character(char: str) -> bool:
    # Combining marks and invisible format characters are not trustworthy
    # delimiters for a visual word (e.g. Java + accent or zero-width Script).
    return char.isalnum() or char == "_" or unicodedata.category(char).startswith("M") or unicodedata.category(char) == "Cf"


def _uncertain_context(context: str) -> bool:
    # Full-width text or inserted invisible formatting cannot hide a negative
    # word. Normalization is transient and never rewrites the retained quote.
    normalized = "".join(char for char in unicodedata.normalize("NFKC", context) if unicodedata.category(char) != "Cf")
    return bool(_UNCERTAIN.search(normalized))


def _evidence_status(value: dict, evidence: list[dict], raw_text: str) -> str:
    provided = {field: item for field, item in value.items() if item is not None}
    by_field = {}
    for source in evidence:
        field = source["field"]
        if field not in provided or field in by_field or source["source_text"] not in raw_text:
            return "unsupported"
        by_field[field] = source["source_text"]
    if set(by_field) != set(provided):
        return "unsupported"
    requires_review = False
    for field, item in provided.items():
        # Boolean/enumerated/date normalization lacks literal support proof in
        # this batch. The user can explicitly supply these using modify.
        if not isinstance(item, str) or "".join(item.split()).casefold() not in "".join(by_field[field].split()).casefold():
            return "unsupported"
        # Avoid promoting Java merely because the source says JavaScript.
        # This is an additional literal boundary, not a semantic inference.
        source = by_field[field]
        occurrences, ambiguous = _source_occurrences(source, raw_text)
        requires_review |= ambiguous or any(_uncertain_context(context) for _, context in occurrences)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+#-]*", item):
            relative_matches = list(re.finditer(r"(?<!\w)" + re.escape(item) + r"(?!\w)", source, re.IGNORECASE))
            if not relative_matches:
                return "unsupported"
            # Test the actual server-text boundaries, including Unicode word
            # characters. A model quote of "Java" from "JavaScript" or
            # "JavaＳｃｒｉｐｔ" cannot manufacture a standalone skill.
            for start, _ in occurrences:
                standalone = False
                for match in relative_matches:
                    first, last = start + match.start(), start + match.end()
                    if (first == 0 or not _word_character(raw_text[first - 1])) and (
                        last == len(raw_text) or not _word_character(raw_text[last])
                    ):
                        standalone = True
                        break
                if not standalone:
                    requires_review = True
    return "needs_review" if requires_review else "supported"


def ingest_profile_candidates(
    db: Session, *, user_id: UUID, resume_id: UUID, batch_key: str,
    payload: dict | CandidateBatchV1, commit: bool = True,
    candidate_metadata: list[dict] | None = None, run_id: UUID | None = None,
) -> CareerProfileResponse:
    """Trusted server-only boundary for future extraction; never auto-confirms.

    Caller supplies the resolved user, reviewed resume and a stable run key.
    Repeated keys are idempotent only with byte-independent canonical content.
    Model confidences are neither accepted nor used as correctness evidence.
    """
    _owner(user_id)
    if not isinstance(resume_id, UUID) or not isinstance(batch_key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", batch_key):
        raise ProfileError("invalid_candidate_batch", "候选批次参数无效", 422)
    try:
        # Constructed/unchecked model instances are revalidated. Disable
        # serializer warnings because Pydantic may print input_value including
        # private candidate contents before validation can reject the object.
        batch = CandidateBatchV1.model_validate(payload.model_dump(warnings=False) if isinstance(payload, CandidateBatchV1) else payload)
    except ValidationError:
        raise ProfileError("invalid_candidate_batch", "候选内容未通过结构校验", 422) from None
    metadata_items = None
    if candidate_metadata is not None:
        try:
            if len(candidate_metadata) != len(batch.candidates):
                raise ValueError("Candidate metadata length mismatch")
            metadata_items = [CandidateExtractionMetadata.model_validate(item) for item in candidate_metadata]
        except (ValidationError, ValueError, TypeError):
            raise ProfileError("invalid_candidate_metadata", "候选来源信息未通过结构校验", 422) from None
    normalized = batch.model_dump(mode="json")
    if metadata_items is not None:
        normalized["extraction_metadata"] = [item.model_dump(mode="json") for item in metadata_items]
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    try:
        # All flows touching both resume and profile use this order. Deletion
        # therefore fences late extraction before text/source material is read.
        locked = db.execute(update(ResumeDocument).where(
            ResumeDocument.id == resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status == "active", ResumeDocument.processing_status == "parsed",
        ).values(lifecycle_status=ResumeDocument.lifecycle_status).execution_options(synchronize_session=False))
        if locked.rowcount != 1:
            raise _not_found()
        document = db.get(ResumeDocument, resume_id, populate_existing=True)
        if run_id is not None:
            if metadata_items is None or not isinstance(run_id, UUID):
                raise ProfileError("invalid_candidate_run", "抽取运行关联无效", 422)
            run = db.scalar(select(ResumeExtractionRun).where(
                ResumeExtractionRun.id == run_id, ResumeExtractionRun.user_id == user_id,
                ResumeExtractionRun.resume_id == resume_id,
            ))
            if run is None:
                raise _not_found()
            if batch_key != str(run_id):
                raise ProfileError("invalid_candidate_run", "候选批次与抽取运行不一致", 422)
            if (run.status not in ("running", "succeeded") or run.test_result != (run.mode == "mock")
                or any(item.provider != run.mode or item.test_result != run.test_result
                       or (run.mode == "deepseek" and (item.model != run.model or item.run_id != str(run.id))) for item in metadata_items)):
                raise ProfileError("invalid_candidate_run", "抽取运行当前不能接收候选", 409)
            if run.status == "running":
                deadline = run.lease_deadline.replace(tzinfo=timezone.utc) if run.lease_deadline and run.lease_deadline.tzinfo is None else run.lease_deadline
                if deadline is None or deadline <= _now():
                    raise ProfileError("invalid_candidate_run", "抽取运行已超时，不能接收候选", 409)
            from offerpilot_api.agent_runtime.extraction_runs import source_version
            if run.source_version != source_version(document):
                raise ProfileError("candidate_source_version_conflict", "简历版本已变化，请重新抽取", 409)
        prior = db.scalars(select(ProfileCandidate).join(CareerProfile).where(
            ProfileCandidate.resume_id == resume_id, ProfileCandidate.batch_key == batch_key,
            CareerProfile.user_id == user_id,
        ).order_by(ProfileCandidate.item_index)).all()
        if prior:
            if len(prior) != len(batch.candidates) or any(item.batch_digest != digest or (run_id is not None and item.extraction_run_id != run_id) for item in prior):
                raise ProfileError("candidate_batch_conflict", "该候选批次已存在且内容不同", 409)
            if commit:
                db.commit()
            return get_profile(db, user_id=user_id)
        if run_id is not None and run.status == "succeeded":
            raise ProfileError("invalid_candidate_run", "已成功运行不能追加新的候选", 409)
        profile = _claim_revision(db, user_id=user_id, revision=None, rollback_on_error=commit)
        count = db.scalar(select(func.count()).select_from(ProfileCandidate).where(ProfileCandidate.profile_id == profile.id)) or 0
        if count + len(batch.candidates) > MAX_CANDIDATES:
            raise ProfileError("candidate_limit_reached", "待管理候选数量已达到上限，请先清理相关简历", 422)
        for index, item in enumerate(batch.candidates):
            value = item.value.model_dump()
            evidence = [source.model_dump() for source in item.evidence]
            metadata = None
            if metadata_items is not None:
                proposal = metadata_items[index]
                if proposal.category != item.category or proposal.source_text_sha256 != text_fingerprint(document.raw_text or ""):
                    raise ProfileError("candidate_source_version_conflict", "简历版本已变化，请重新抽取", 409)
                mapped, mapped_evidence, metadata = evaluate_metadata(proposal, document.raw_text or "", document.text_layout)
                if proposal.source_layout_sha256 != layout_fingerprint(document.raw_text or "", document.text_layout):
                    raise ProfileError("candidate_source_version_conflict", "简历定位版本已变化，请重新抽取", 409)
                metadata.source_file_sha256 = document.sha256
                if VALUE_MODELS[item.category].model_validate(mapped).model_dump() != value or mapped_evidence != evidence:
                    raise ProfileError("invalid_candidate_mapping", "候选字段映射与来源不一致", 422)
            db.add(ProfileCandidate(
                profile_id=profile.id, resume_id=resume_id, batch_key=batch_key, item_index=index, batch_digest=digest,
                category=item.category, value=value, evidence=evidence,
                evidence_status=metadata.evidence_status if metadata else _evidence_status(value, evidence, document.raw_text or ""), status="pending",
                schema_version=metadata.schema_version if metadata else batch.schema_version, extraction_version=batch.extraction_version,
                extraction_metadata=metadata.model_dump(mode="json") if metadata else {}, extraction_run_id=run_id,
            ))
        db.flush()
        if commit:
            db.commit()
        return get_profile(db, user_id=user_id)
    except Exception:
        if commit:
            db.rollback()
        raise


def review_profile_candidate(
    db: Session, *, user_id: UUID, candidate_id: UUID, payload: ProfileCandidateReviewRequest,
) -> CareerProfileResponse:
    _owner(user_id)
    try:
        candidate = db.scalar(select(ProfileCandidate).join(CareerProfile).where(
            ProfileCandidate.id == candidate_id, CareerProfile.user_id == user_id,
        ))
        if candidate is None:
            raise _not_found()
        locked = db.execute(update(ResumeDocument).where(
            ResumeDocument.id == candidate.resume_id, ResumeDocument.user_id == user_id,
            ResumeDocument.lifecycle_status == "active", ResumeDocument.processing_status == "parsed",
        ).values(lifecycle_status=ResumeDocument.lifecycle_status).execution_options(synchronize_session=False))
        if locked.rowcount != 1:
            raise _not_found()
        profile = _claim_revision(db, user_id=user_id, revision=payload.revision)
        candidate = db.get(ProfileCandidate, candidate_id, populate_existing=True)
        if candidate is None or candidate.profile_id != profile.id:
            raise _not_found()
        if candidate.status != "pending":
            raise ProfileError("candidate_already_reviewed", "该候选已处理，请在正式资料中继续编辑", 409)
        if payload.action == "confirm" and candidate.extraction_metadata:
            document = db.get(ResumeDocument, candidate.resume_id, populate_existing=True)
            if (candidate.extraction_metadata.get("source_text_sha256") != text_fingerprint(document.raw_text or "")
                or candidate.extraction_metadata.get("source_layout_sha256") != layout_fingerprint(document.raw_text or "", document.text_layout)
                or candidate.extraction_metadata.get("source_file_sha256") != document.sha256):
                raise ProfileError("candidate_source_version_conflict", "来源简历已变化，请重新抽取或核对后自行修订", 409)
            _, _, checked_metadata = evaluate_metadata(CandidateExtractionMetadata.model_validate(candidate.extraction_metadata), document.raw_text or "", document.text_layout)
            if checked_metadata.evidence_status != "supported":
                raise ProfileError("candidate_evidence_requires_user_input", "证据需核实，请修改为本人提供的资料或拒绝", 422)
        if payload.action == "confirm" and candidate.evidence_status != "supported":
            raise ProfileError("candidate_evidence_requires_user_input", "证据不足或含否定、未知信息，请修改为本人提供的资料或拒绝", 422)
        if payload.action != "reject":
            count = db.scalar(select(func.count()).select_from(CareerProfileEntry).where(
                CareerProfileEntry.profile_id == profile.id, CareerProfileEntry.category == candidate.category,
            )) or 0
            if count >= LIMITS[candidate.category]:
                raise ProfileError("profile_entry_limit_reached", "该资料分组已达到条目上限", 422)
            modified = payload.action == "modify"
            try:
                value = VALUE_MODELS[candidate.category].model_validate(
                    payload.value.model_dump(exclude_unset=True) if modified else candidate.value,
                ).model_dump()
            except ValidationError:
                raise ProfileError("invalid_profile_value", "修改内容与候选资料分组不一致，请检查字段", 422) from None
            entry = CareerProfileEntry(
                profile_id=profile.id, category=candidate.category, value=value, position=count,
                source_kind="user_modified" if modified else "resume_confirmed", resume_id=candidate.resume_id,
                field_sources=_manual_fields(value) if modified else {field: "resume" for field, provided in value.items() if provided is not None},
                evidence=[{**source, "supports_current_value": not modified} for source in candidate.evidence], updated_at=_now(),
                extraction_metadata={key: candidate.extraction_metadata.get(key) for key in ("test_result", "provider", "model", "run_id")} if candidate.extraction_metadata else {},
            )
            db.add(entry)
            db.flush()
            candidate.entry_id = entry.id
        candidate.status = {"confirm": "confirmed", "modify": "modified", "reject": "rejected"}[payload.action]
        candidate.reviewed_at = _now()
        db.commit()
        return get_profile(db, user_id=user_id)
    except Exception:
        db.rollback()
        raise


def remove_resume_profile_evidence(db: Session, *, user_id: UUID, resume_id: UUID) -> None:
    """Called inside committed deletion intent, after taking the resume lock.

    User-reviewed entries survive. Candidate snapshots, quotes and resume
    references are erased even when physical cleanup subsequently fails.
    Revision changes fence an editor holding the pre-deletion provenance.
    The caller owns commit/rollback with the existing file lifecycle.
    """
    profile = _profile(db, user_id)
    if profile is None:
        return
    entries = db.scalar(select(func.count()).select_from(CareerProfileEntry).where(
        CareerProfileEntry.profile_id == profile.id, CareerProfileEntry.resume_id == resume_id,
    )) or 0
    candidates = db.scalar(select(func.count()).select_from(ProfileCandidate).where(
        ProfileCandidate.profile_id == profile.id, ProfileCandidate.resume_id == resume_id,
    )) or 0
    if not entries and not candidates:
        return
    profile = _claim_revision(db, user_id=user_id, revision=None)
    linked = db.scalars(select(CareerProfileEntry).where(
        CareerProfileEntry.profile_id == profile.id, CareerProfileEntry.resume_id == resume_id,
    )).all()
    for entry in linked:
        entry.resume_id = None
        entry.source_kind = "resume_deleted"
        entry.field_sources = {field: "resume_deleted" if source == "resume" else source for field, source in entry.field_sources.items()}
        entry.evidence = []
        entry.updated_at = _now()
    db.execute(delete(ProfileCandidate).where(ProfileCandidate.profile_id == profile.id, ProfileCandidate.resume_id == resume_id))
    # autoflush=False: persist cleared references before the caller's FK delete.
    db.flush()
