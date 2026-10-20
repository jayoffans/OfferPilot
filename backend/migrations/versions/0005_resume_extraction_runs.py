"""Persist bounded extraction runs and server-authored PDF text locations.

Revision ID: 0005_resume_extraction_runs
Revises: 0004_trusted_career_profile

Legacy text is deliberately left without invented page coordinates. Old
candidate/confirmed values are neither re-extracted nor promoted by migration.
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_resume_extraction_runs"
down_revision = "0004_trusted_career_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resume_documents", sa.Column("text_layout", sa.JSON(), nullable=True))
    op.add_column("career_profile_entries", sa.Column("extraction_metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False))
    op.create_table(
        "resume_extraction_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resume_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("mode", sa.String(20), server_default=sa.text("'mock'"), nullable=False),
        sa.Column("test_result", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("call_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("execution_token", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("lease_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("extraction_version", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("provider_version", sa.String(100), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_resume_extraction_runs_request"),
        sa.UniqueConstraint("resume_id", "source_version", "extraction_version", name="uq_resume_extraction_runs_source"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_resume_extraction_runs_status"),
        sa.CheckConstraint("mode = 'mock' AND test_result = true", name="ck_resume_extraction_runs_mock_only"),
        sa.CheckConstraint("revision >= 0 AND attempt_count >= 0 AND attempt_count <= max_attempts", name="ck_resume_extraction_runs_attempts"),
        sa.CheckConstraint("call_count >= 0 AND call_count <= max_calls AND max_attempts BETWEEN 1 AND 2 AND max_calls BETWEEN 1 AND 4", name="ck_resume_extraction_runs_calls"),
        sa.CheckConstraint("candidate_count >= 0 AND timeout_seconds BETWEEN 1 AND 60", name="ck_resume_extraction_runs_limits"),
        sa.CheckConstraint("(status = 'running' AND execution_token IS NOT NULL AND lease_deadline IS NOT NULL) OR (status <> 'running' AND execution_token IS NULL AND lease_deadline IS NULL)", name="ck_resume_extraction_runs_lease"),
    )
    for field in ("user_id", "resume_id", "status"):
        op.create_index(f"ix_resume_extraction_runs_{field}", "resume_extraction_runs", [field])
    op.add_column("profile_candidates", sa.Column("extraction_metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False))
    # SQLite's native ADD REFERENCES avoids rebuilding the parent candidate
    # table and accidentally triggering existing entry/source FK behavior.
    if op.get_context().dialect.name == "sqlite":
        op.execute("ALTER TABLE profile_candidates ADD COLUMN extraction_run_id CHAR(32) CONSTRAINT fk_profile_candidates_extraction_run_id_runs REFERENCES resume_extraction_runs (id) ON DELETE SET NULL")
    else:
        op.add_column("profile_candidates", sa.Column("extraction_run_id", sa.Uuid(as_uuid=True), nullable=True))
        op.create_foreign_key("fk_profile_candidates_extraction_run_id_runs", "profile_candidates", "resume_extraction_runs", ["extraction_run_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_profile_candidates_extraction_run_id", "profile_candidates", ["extraction_run_id"])


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("Extraction downgrade requires an online data safety check")
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT id FROM resume_extraction_runs LIMIT 1")).first() is not None:
        raise RuntimeError("Extraction data exists; refusing to discard run recovery history")
    if bind.execute(sa.text("SELECT id FROM resume_documents WHERE text_layout IS NOT NULL LIMIT 1")).first() is not None:
        raise RuntimeError("Extraction data exists; refusing to discard text locations")
    candidates = sa.table("profile_candidates", sa.column("extraction_run_id"), sa.column("extraction_metadata", sa.JSON()))
    for row in bind.execute(sa.select(candidates.c.extraction_run_id, candidates.c.extraction_metadata)):
        if row[0] is not None or row[1] not in ({}, None):
            raise RuntimeError("Extraction data exists; refusing to discard candidate evidence")
    entries = sa.table("career_profile_entries", sa.column("extraction_metadata", sa.JSON()))
    if any(row[0] not in ({}, None) for row in bind.execute(sa.select(entries.c.extraction_metadata))):
        raise RuntimeError("Extraction data exists; refusing to discard reviewed provenance")
    op.drop_index("ix_profile_candidates_extraction_run_id", table_name="profile_candidates")
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_profile_candidates_extraction_run_id_runs", "profile_candidates", type_="foreignkey")
    op.drop_column("profile_candidates", "extraction_run_id")
    op.drop_column("profile_candidates", "extraction_metadata")
    for field in ("status", "resume_id", "user_id"):
        op.drop_index(f"ix_resume_extraction_runs_{field}", table_name="resume_extraction_runs")
    op.drop_table("resume_extraction_runs")
    op.drop_column("resume_documents", "text_layout")
    op.drop_column("career_profile_entries", "extraction_metadata")
