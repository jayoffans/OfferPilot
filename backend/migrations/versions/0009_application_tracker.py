"""Manual application attempts, append-only history and safe retry receipts.

Revision ID: 0009_application_tracker
Revises: 0008_trusted_job_matches
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_application_tracker"
down_revision = "0008_trusted_job_matches"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False)


def _owner():
    return sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


def _time(name):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False)


def upgrade():
    op.create_table("applications", _id(), _owner(),
        sa.Column("job_id", sa.Uuid(as_uuid=True), sa.ForeignKey("saved_jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_title", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("match_id", sa.Uuid(as_uuid=True), sa.ForeignKey("job_matches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("action_title", sa.String(300), nullable=True),
        sa.Column("action_notes", sa.Text(), nullable=True),
        sa.Column("action_due_date", sa.Date(), nullable=True),
        sa.Column("action_due_time", sa.String(5), nullable=True),
        sa.Column("action_status", sa.String(16), nullable=True),
        _time("created_at"), _time("updated_at"),
        sa.UniqueConstraint("user_id", "job_id", "attempt_number", name="uq_applications_attempt"),
        sa.UniqueConstraint("user_id", "job_id", "active_slot", name="uq_applications_active"),
        sa.CheckConstraint("status IN ('preparing', 'submitted', 'assessment', 'interview', 'offer', 'rejected', 'withdrawn', 'closed')", name="ck_applications_status"),
        sa.CheckConstraint("revision >= 1 AND attempt_number >= 1", name="ck_applications_versions"),
        sa.CheckConstraint("(status IN ('rejected', 'withdrawn', 'closed') AND active_slot IS NULL) OR (status NOT IN ('rejected', 'withdrawn', 'closed') AND active_slot = 1 AND active_slot IS NOT NULL)", name="ck_applications_active"),
        sa.CheckConstraint("(action_title IS NULL AND action_notes IS NULL AND action_due_date IS NULL AND action_due_time IS NULL AND action_status IS NULL) OR (action_title IS NOT NULL AND action_notes IS NOT NULL AND action_status IS NOT NULL AND action_status IN ('pending', 'completed', 'cancelled'))", name="ck_applications_action"),
        sa.CheckConstraint("action_due_time IS NULL OR action_due_date IS NOT NULL", name="ck_applications_action_time"),
    )
    op.create_index("ix_applications_owner_updated", "applications", ["user_id", "updated_at"])
    op.create_index("ix_applications_job_id", "applications", ["job_id"])
    op.create_table("application_events", _id(), _owner(),
        sa.Column("application_id", sa.Uuid(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("application_id", "revision", "type", name="uq_application_events_change"),
        sa.CheckConstraint("revision >= 1", name="ck_application_events_revision"),
        sa.CheckConstraint("type IN ('application_created', 'status_changed', 'notes_changed', 'next_action_changed', 'submitted_at_changed')", name="ck_application_events_type"),
    )
    op.create_index("ix_application_events_owner_application", "application_events", ["user_id", "application_id", "recorded_at"])
    op.create_table("application_receipts", _id(), _owner(),
        sa.Column("application_id", sa.Uuid(as_uuid=True), sa.ForeignKey("applications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False), _time("created_at"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_application_receipts_request"),
        sa.CheckConstraint("operation IN ('create', 'update', 'delete')", name="ck_application_receipts_operation"),
    )
    op.create_index("ix_application_receipts_application_id", "application_receipts", ["application_id"])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Application downgrade requires an online data safety check")
    for table in ("applications", "application_events", "application_receipts"):
        if op.get_bind().execute(sa.text(f"SELECT id FROM {table} LIMIT 1")).first():
            raise RuntimeError("Application data or retry receipts exist; refusing destructive downgrade")
    op.drop_table("application_receipts")
    op.drop_table("application_events")
    op.drop_table("applications")
