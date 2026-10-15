"""Versioned deterministic matches and minimal confirmed job preferences.

Revision ID: 0008_trusted_job_matches
Revises: 0007_private_jobs
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_trusted_job_matches"
down_revision = "0007_private_jobs"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False)


def _time():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False)


def upgrade():
    # Native ADD preserves old profile parent rows and candidate FK chains.
    op.add_column("career_profiles", sa.Column("preferences", sa.JSON(), server_default=sa.text("'{}'"), nullable=False))
    op.create_table("job_matches", _id(),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), sa.ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("job_revision", sa.Integer(), nullable=False),
        sa.Column("jd_version", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("evaluation", sa.JSON(), nullable=False), _time(),
        sa.UniqueConstraint("user_id", "job_id", "profile_revision", "job_revision", "rule_version", name="uq_job_matches_basis"),
        sa.CheckConstraint("profile_revision >= 1 AND job_revision >= 1 AND jd_version >= 1", name="ck_job_matches_versions"),
    )
    op.create_index("ix_job_matches_job_id", "job_matches", ["job_id"])
    op.create_index("ix_job_matches_owner_job_created", "job_matches", ["user_id", "job_id", "created_at"])
    op.create_table("job_match_receipts", _id(),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), sa.ForeignKey("saved_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("match_id", sa.Uuid(as_uuid=True), sa.ForeignKey("job_matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False), _time(),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_job_match_receipts_request"),
    )
    for field in ("job_id", "match_id"):
        op.create_index(f"ix_job_match_receipts_{field}", "job_match_receipts", [field])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Trusted match downgrade requires an online data safety check")
    for table in ("job_matches", "job_match_receipts"):
        if op.get_bind().execute(sa.text(f"SELECT id FROM {table} LIMIT 1")).first():
            raise RuntimeError("Trusted match data exists; refusing destructive downgrade")
    # Compare parsed JSON; whitespace and all-default preferences are harmless.
    import json
    defaults = {"locations": [], "work_types": [], "target_roles": [], "salary": None,
                "weekly_days": None, "internship_months": None}
    for value in op.get_bind().execute(sa.text("SELECT preferences FROM career_profiles")).scalars():
        parsed = json.loads(value) if isinstance(value, str) else value
        if parsed != {} and parsed != defaults:
            raise RuntimeError("Confirmed preferences exist; refusing destructive downgrade")
    op.drop_table("job_match_receipts")
    op.drop_table("job_matches")
    # Modern bundled SQLite supports DROP COLUMN without rebuilding FK parents.
    op.drop_column("career_profiles", "preferences")
