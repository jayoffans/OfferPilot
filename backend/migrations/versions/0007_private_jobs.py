"""User-private jobs, immutable JD snapshots and reviewed requirements.

Revision ID: 0007_private_jobs
Revises: 0006_deepseek_byok
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_private_jobs"
down_revision = "0006_deepseek_byok"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False)


def _time(name):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False)


def upgrade():
    op.create_table("saved_jobs", _id(),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("salary", sa.String(200), nullable=True),
        sa.Column("work_type", sa.String(20), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("jd_version", sa.Integer(), nullable=False),
        sa.Column("is_favorite", sa.Boolean(), server_default=sa.false(), nullable=False),
        _time("created_at"), _time("updated_at"),
        sa.UniqueConstraint("user_id", "content_digest", name="uq_saved_jobs_owner_content"),
        sa.CheckConstraint("revision >= 1 AND jd_version >= 1", name="ck_saved_jobs_versions"),
        sa.CheckConstraint("work_type IN ('unknown', 'internship', 'campus', 'full_time')", name="ck_saved_jobs_work_type"),
        sa.CheckConstraint("source_kind IN ('pasted', 'manual')", name="ck_saved_jobs_source_kind"),
    )
    op.create_index("ix_saved_jobs_user_id", "saved_jobs", ["user_id"])
    op.create_table("job_jd_snapshots", _id(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), sa.ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False), _time("created_at"),
        sa.UniqueConstraint("job_id", "version", name="uq_job_jd_snapshots_version"),
        sa.CheckConstraint("version >= 1", name="ck_job_jd_snapshots_version"),
    )
    op.create_index("ix_job_jd_snapshots_job_id", "job_jd_snapshots", ["job_id"])
    op.create_table("job_requirements", _id(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), sa.ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), sa.ForeignKey("job_jd_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False), sa.Column("review_status", sa.String(20), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("source_start", sa.Integer(), nullable=True), sa.Column("source_end", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.String(20), nullable=False), sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=True), _time("created_at"), _time("updated_at"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("type IN ('education', 'major', 'graduation', 'experience', 'location', 'internship_duration', 'technical_skill', 'project', 'internship_experience', 'language', 'other', 'salary', 'work_type', 'responsibility')", name="ck_job_requirements_type"),
        sa.CheckConstraint("priority IN ('must', 'preferred', 'uncertain')", name="ck_job_requirements_priority"),
        sa.CheckConstraint("review_status IN ('pending', 'confirmed')", name="ck_job_requirements_review_status"),
        sa.CheckConstraint("source_kind IN ('rule', 'manual', 'user_modified')", name="ck_job_requirements_source_kind"),
        sa.CheckConstraint("position >= 0 AND revision >= 1", name="ck_job_requirements_versions"),
        sa.CheckConstraint("(evidence_text IS NULL AND source_start IS NULL AND source_end IS NULL) OR (evidence_text IS NOT NULL AND source_start IS NOT NULL AND source_end IS NOT NULL AND source_start >= 0 AND source_end > source_start)", name="ck_job_requirements_evidence"),
    )
    for field in ("job_id", "snapshot_id"):
        op.create_index(f"ix_job_requirements_{field}", "job_requirements", [field])
    op.create_table("job_create_receipts", _id(),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), sa.ForeignKey("saved_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False), _time("created_at"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_job_create_receipts_request"),
    )
    for field in ("user_id", "job_id"):
        op.create_index(f"ix_job_create_receipts_{field}", "job_create_receipts", [field])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Private job downgrade requires an online data safety check")
    for table in ("saved_jobs", "job_jd_snapshots", "job_requirements", "job_create_receipts"):
        if op.get_bind().execute(sa.text(f"SELECT id FROM {table} LIMIT 1")).first():
            raise RuntimeError("Private job data exists; refusing destructive downgrade")
    for table in ("job_create_receipts", "job_requirements", "job_jd_snapshots", "saved_jobs"):
        op.drop_table(table)
