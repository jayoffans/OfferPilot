"""Read-only, bounded workbench projections of the existing owner-scoped data.

No counters or secondary action state are persisted. Queries select lightweight
columns, cap every collection, and never load JD/resume/evaluation/event bodies.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from offerpilot_api.application_tracker.schemas import (
    DashboardActionGroup, DashboardActionItem, DashboardActions, DashboardActivity,
    DashboardJobItem, DashboardJobs, DashboardOnboarding, DashboardResponse, DashboardSummary,
)
from offerpilot_api.application_tracker.services import (
    ApplicationError, SHANGHAI, _action, _now, _overdue_predicate, _owner, _pending_action_predicate, _utc,
)
from offerpilot_api.db.models import (
    Application, ApplicationEvent, CareerProfile, CareerProfileEntry, JobJDSnapshot,
    JobMatch, JobRequirement, ResumeDocument, SavedJob,
)
from offerpilot_api.job_intelligence import matching
from offerpilot_api.profile.schemas import ProfilePreferences


def _actions(db, user_id, now, limit):
    today = now.astimezone(SHANGHAI).date()
    end = min(today.toordinal() + 7, date.max.toordinal())
    pending, overdue = _pending_action_predicate(), _overdue_predicate(now)
    bucket = case(
        (Application.action_status == "completed", "completed"),
        (Application.action_status == "cancelled", "cancelled"),
        (overdue, "overdue"),
        (and_(pending, Application.action_due_date == today), "today"),
        (and_(pending, Application.action_due_date > today,
              Application.action_due_date <= date.fromordinal(end)), "upcoming"),
        (and_(pending, Application.action_due_date.is_(None)), "undated"),
        else_=None,
    )
    filters = [Application.user_id == user_id, Application.action_title.is_not(None)]
    counts = dict(db.execute(select(bucket, func.count()).where(*filters, bucket.is_not(None)).group_by(bucket)).all())
    columns = [Application.id.label("application_id"), Application.job_id, Application.revision,
        Application.status.label("application_status"), Application.job_title, Application.company,
        Application.action_title, Application.action_notes, Application.action_due_date,
        Application.action_due_time, Application.action_status, bucket.label("bucket")]
    # Completed/cancelled rows use most recently updated first. Pending rows
    # use their calendar deadline; date-only deadlines follow timed 23:59.
    order = [case((pending, Application.action_due_date), else_=None).asc().nullslast(),
             case((pending, func.coalesce(Application.action_due_time, "24:00")), else_=None).asc(),
             Application.updated_at.desc(), Application.id]
    ranked = select(*columns, func.row_number().over(partition_by=bucket, order_by=order).label("position"))\
        .where(*filters, bucket.is_not(None)).subquery()
    rows = db.execute(select(ranked).where(ranked.c.position <= limit)
                      .order_by(ranked.c.bucket, ranked.c.position)).mappings()
    groups = {key: DashboardActionGroup(total=counts.get(key, 0), items=[])
              for key in ("overdue", "today", "upcoming", "undated", "completed", "cancelled")}
    for row in rows:
        action = _action(SimpleNamespace(**row))
        groups[row["bucket"]].items.append(DashboardActionItem(
            **{key: row[key] for key in ("application_id", "job_id", "revision", "application_status", "job_title", "company")},
            next_action=action, is_overdue=row["bucket"] == "overdue"))
    return DashboardActions(**groups)


def _summary(db, user_id):
    counts = dict(db.execute(select(Application.status, func.count()).where(
        Application.user_id == user_id).group_by(Application.status)).all())
    return DashboardSummary(total=sum(counts.values()), preparing=counts.get("preparing", 0),
        submitted=counts.get("submitted", 0), assessment_interview=counts.get("assessment", 0) + counts.get("interview", 0),
        offer=counts.get("offer", 0), ended=sum(counts.get(key, 0) for key in ("rejected", "withdrawn", "closed")))


def _jobs(db, user_id, profile_revision, limit):
    pending = exists(select(JobRequirement.id).join(JobJDSnapshot, JobJDSnapshot.id == JobRequirement.snapshot_id)
        .where(JobRequirement.job_id == SavedJob.id, JobJDSnapshot.job_id == SavedJob.id,
               or_(JobRequirement.review_status != "confirmed", JobJDSnapshot.version != SavedJob.jd_version)))
    missing = ~exists(select(JobRequirement.id).where(JobRequirement.job_id == SavedJob.id))
    reviewed = ~missing & ~pending
    has_application = exists(select(Application.id).where(Application.user_id == user_id,
                                                         Application.job_id == SavedJob.id))
    # Follow the authoritative S3-2 latest_match ordering, including rule-version
    # preference on a tied basis. Only metadata is selected, never evaluation.
    ranked = select(JobMatch.id, JobMatch.job_id, JobMatch.profile_revision, JobMatch.job_revision,
        JobMatch.jd_version, JobMatch.rule_version,
        func.row_number().over(partition_by=JobMatch.job_id, order_by=[JobMatch.profile_revision.desc(),
            JobMatch.job_revision.desc(), (JobMatch.rule_version == matching.RULE_VERSION).desc(),
            JobMatch.created_at.desc(), JobMatch.id.desc()]).label("position"))\
        .where(JobMatch.user_id == user_id).subquery()
    favorite = SavedJob.is_favorite.is_(True) & ~has_application
    stale = and_(ranked.c.id.is_not(None), or_(ranked.c.profile_revision != profile_revision,
        ranked.c.job_revision != SavedJob.revision, ranked.c.jd_version != SavedJob.jd_version,
        ranked.c.rule_version != matching.RULE_VERSION))
    unmatched = reviewed & ranked.c.id.is_(None)
    fields = [SavedJob.id, SavedJob.title, SavedJob.company, SavedJob.updated_at,
              favorite.label("favorite"), pending.label("review"), missing.label("missing"),
              stale.label("stale"), unmatched.label("unmatched")]
    base = select(*fields).outerjoin(ranked, and_(ranked.c.job_id == SavedJob.id, ranked.c.position == 1))\
        .where(SavedJob.user_id == user_id, or_(favorite, pending, missing, stale, unmatched))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.execute(base.order_by(case((favorite, 0), (pending | missing, 1), (stale, 2), else_=3),
                                   SavedJob.updated_at.desc(), SavedJob.id).limit(limit)).mappings()
    items = []
    for row in rows:
        reasons = [reason for field, reason in (("favorite", "favorite_without_application"),
            ("review", "review_pending"), ("missing", "requirements_missing"),
            ("stale", "match_stale"), ("unmatched", "unmatched")) if row[field]]
        items.append(DashboardJobItem(id=row["id"], title=row["title"], company=row["company"],
            updated_at=_utc(row["updated_at"]), reasons=reasons,
            next_step="review" if row["review"] or row["missing"] else "match" if row["stale"] or row["unmatched"] else "apply"))
    return DashboardJobs(total=total, items=items)


def _activities(db, user_id, limit):
    action_status = ApplicationEvent.changes["after"]["status"].as_string()
    event_status = case((ApplicationEvent.type == "status_changed", ApplicationEvent.changes["after"].as_string()),
        (ApplicationEvent.type == "application_created", action_status), else_=Application.status)
    rows = db.execute(select(ApplicationEvent.id, ApplicationEvent.application_id, ApplicationEvent.type,
        ApplicationEvent.recorded_at, Application.job_title, Application.company,
        event_status.label("application_status"),
        case((ApplicationEvent.type == "next_action_changed", action_status), else_=None).label("action_status"))
        .join(Application, Application.id == ApplicationEvent.application_id)
        .where(ApplicationEvent.user_id == user_id, Application.user_id == user_id,
            or_(ApplicationEvent.type.in_(["application_created", "status_changed"]),
                and_(ApplicationEvent.type == "next_action_changed", action_status == "completed")))
        .order_by(ApplicationEvent.recorded_at.desc(), ApplicationEvent.revision.desc(), ApplicationEvent.id.desc())
        .limit(limit)).mappings()
    return [DashboardActivity(**{**row, "recorded_at": _utc(row["recorded_at"])}) for row in rows]


def get_dashboard(db: Session, *, user_id: UUID, limit: int = 5) -> DashboardResponse:
    _owner(user_id)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Dashboard limit must be between 1 and 20")
    now = _now()
    profile = db.execute(select(CareerProfile.id, CareerProfile.revision, CareerProfile.preferences)
                         .where(CareerProfile.user_id == user_id)).first()
    # Formal entries already represent user-provided/reviewed facts; there is
    # no invented profile-complete flag and candidates never count as facts.
    entry_count = db.scalar(select(func.count()).select_from(CareerProfileEntry)
        .join(CareerProfile, CareerProfile.id == CareerProfileEntry.profile_id)
        .where(CareerProfile.user_id == user_id))
    resume_count = db.scalar(select(func.count()).select_from(ResumeDocument).where(
        ResumeDocument.user_id == user_id, ResumeDocument.lifecycle_status == "active"))
    job_count = db.scalar(select(func.count()).select_from(SavedJob).where(SavedJob.user_id == user_id))
    try:
        preferences = ProfilePreferences.model_validate(profile.preferences if profile else {})
    except ValidationError:
        raise ApplicationError("profile_data_invalid", "正式资料结构无法读取，请联系管理员核对", 409) from None
    profile_ready = bool(entry_count or any(preferences.model_dump(mode="json").values()))
    summary = _summary(db, user_id)
    jobs = _jobs(db, user_id, profile.revision if profile else 0, limit)
    job_id = None
    # Existing progress takes precedence over optional earlier steps. A manual
    # profile or existing applications never sends users back to PDF upload.
    if summary.total:
        step = "manage_actions"
    elif job_count:
        first = jobs.items[0] if jobs.items else None
        job_id = first.id if first else db.scalar(select(SavedJob.id).where(SavedJob.user_id == user_id)
                                                  .order_by(SavedJob.updated_at.desc(), SavedJob.id).limit(1))
        step = ("review_job" if first and first.next_step == "review" else
                "complete_profile" if first and first.next_step == "match" and not profile_ready else
                "match_job" if first and first.next_step == "match" else "create_application")
    elif profile_ready:
        step = "import_job"
    elif resume_count:
        step = "complete_profile"
    else:
        step = "upload_resume"
    return DashboardResponse(generated_at=now, today=now.astimezone(SHANGHAI).date(),
        actions=_actions(db, user_id, now, limit), summary=summary, jobs=jobs,
        recent_activity=_activities(db, user_id, limit),
        onboarding=DashboardOnboarding(resume_count=resume_count, profile_entry_count=entry_count,
            job_count=job_count, application_count=summary.total, next_step=step, job_id=job_id))
