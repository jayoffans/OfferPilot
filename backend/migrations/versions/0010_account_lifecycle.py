"""Durable private exports and account deletion receipts.

Revision ID: 0010_account_lifecycle
Revises: 0009_application_tracker
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_account_lifecycle"
down_revision = "0009_application_tracker"
branch_labels = None
depends_on = None


def _time(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=None if nullable else sa.text("CURRENT_TIMESTAMP"))


def upgrade():
    op.create_table("account_exports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cleanup_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("execution_token", sa.Uuid(), nullable=True),
        _time("lease_deadline", True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        _time("created_at"), _time("updated_at"), _time("completed_at", True), _time("expires_at", True),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_account_exports_request"),
        sa.CheckConstraint("status IN ('queued','running','ready','failed','expired','cancelled')", name="ck_account_exports_status"),
        sa.CheckConstraint("attempt_count >= 0 AND attempt_count <= max_attempts AND max_attempts BETWEEN 1 AND 2", name="ck_account_exports_attempts"),
        sa.CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_account_exports_size"),
        sa.CheckConstraint("cleanup_attempt_count BETWEEN 0 AND 5", name="ck_account_exports_cleanup_attempts"),
        sa.CheckConstraint("(status = 'running' AND execution_token IS NOT NULL AND lease_deadline IS NOT NULL) OR (status <> 'running' AND execution_token IS NULL AND lease_deadline IS NULL)", name="ck_account_exports_lease"),
    )
    op.create_index("ix_account_exports_user_id", "account_exports", ["user_id"])
    op.create_index("ix_account_exports_status", "account_exports", ["status"])
    op.create_table("account_deletions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        _time("lease_deadline", True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("status_token_hash", sa.String(64), nullable=True),
        sa.Column("status_session_hash", sa.String(64), nullable=True),
        _time("status_expires_at", True), _time("created_at"), _time("updated_at"), _time("completed_at", True),
        sa.UniqueConstraint("user_id", name="uq_account_deletions_user"),
        sa.CheckConstraint("status IN ('pending','processing','failed','manual_review','completed')", name="ck_account_deletions_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_account_deletions_attempts"),
    )
    op.create_index("ix_account_deletions_status", "account_deletions", ["status"])
    op.create_index("ix_account_deletions_status_token_hash", "account_deletions", ["status_token_hash"])
    op.create_index("ix_account_deletions_status_session_hash", "account_deletions", ["status_session_hash"])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Lifecycle downgrade requires an online data safety check")
    for table in ("account_deletions", "account_exports"):
        if op.get_bind().execute(sa.text(f"SELECT id FROM {table} LIMIT 1")).first():
            raise RuntimeError("Lifecycle receipts exist; use a forward repair")
    op.drop_table("account_deletions")
    op.drop_table("account_exports")
