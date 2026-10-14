"""Explicit one-call DeepSeek consent and metering, no credential persistence.

Revision ID: 0006_deepseek_byok
Revises: 0005_resume_extraction_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_deepseek_byok"
down_revision = "0005_resume_extraction_runs"
branch_labels = None
depends_on = None

PROVIDER_CHECK = "(mode = 'mock' AND test_result = true) OR (mode = 'deepseek' AND test_result = false AND max_calls = 1 AND max_attempts = 1 AND session_id IS NOT NULL AND key_reference IS NOT NULL AND authorization_id IS NOT NULL AND sent_text_sha256 IS NOT NULL AND consent_version IS NOT NULL AND model = 'deepseek-flash')"


def _links():
    # SQLite batch replacement invokes ON DELETE SET NULL on this inbound FK.
    # Preserve and restore the exact existing references with FK checks enabled.
    return op.get_bind().execute(sa.text("SELECT id, extraction_run_id FROM profile_candidates WHERE extraction_run_id IS NOT NULL")).all()


def _restore(links):
    for cid, rid in links:
        op.get_bind().execute(sa.text("UPDATE profile_candidates SET extraction_run_id = :rid WHERE id = :cid"), {"rid": rid, "cid": cid})


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError("BYOK migration requires online preservation of candidate references")
    links = _links()
    with op.batch_alter_table("resume_extraction_runs") as batch:
        for name in ("session_id", "key_reference", "authorization_id"):
            batch.add_column(sa.Column(name, sa.Uuid(as_uuid=True), nullable=True))
        for name in ("model", "consent_version"):
            batch.add_column(sa.Column(name, sa.String(100), nullable=True))
        batch.add_column(sa.Column("sent_text_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("billing_status", sa.String(24), nullable=False, server_default=sa.text("'not_sent'")))
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            batch.add_column(sa.Column(name, sa.Integer(), nullable=True))
        batch.drop_constraint("ck_resume_extraction_runs_mock_only", type_="check")
        batch.create_check_constraint("ck_resume_extraction_runs_provider", PROVIDER_CHECK)
        batch.create_check_constraint("ck_resume_extraction_runs_billing", "billing_status IN ('not_sent', 'unknown', 'usage_reported')")
        batch.create_check_constraint("ck_resume_extraction_runs_tokens", "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND (completion_tokens IS NULL OR completion_tokens >= 0) AND (total_tokens IS NULL OR total_tokens >= 0)")
    _restore(links)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("BYOK downgrade requires online data safety check")
    if op.get_bind().execute(sa.text("SELECT id FROM resume_extraction_runs WHERE mode = 'deepseek' LIMIT 1")).first():
        raise RuntimeError("DeepSeek data exists; refusing to discard consent and billing history")
    links = _links()
    with op.batch_alter_table("resume_extraction_runs") as batch:
        for name in ("provider", "billing", "tokens"):
            batch.drop_constraint(f"ck_resume_extraction_runs_{name}", type_="check")
        for name in ("session_id", "key_reference", "authorization_id", "model", "consent_version", "sent_text_sha256", "billing_status", "prompt_tokens", "completion_tokens", "total_tokens"):
            batch.drop_column(name)
        batch.create_check_constraint("ck_resume_extraction_runs_mock_only", "mode = 'mock' AND test_result = true")
    _restore(links)
