"""Add invitation-based identity and nullable resume ownership.

Revision ID: 0002_identity_ownership
Revises: 0001_resume_intelligence
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_identity_ownership"
down_revision = "0001_resume_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("email_normalized", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
    )
    op.create_table(
        "registration_invitations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email_normalized", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_registration_invitations_token_hash"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "auth_throttles",
        sa.Column("key_hash", sa.String(length=64), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_auth_throttles_attempt_count"),
    )

    if op.get_context().dialect.name == "sqlite":
        # Rebuilding the parent table with SQLite foreign keys enabled could
        # cascade-delete the existing profiles and facts. A nullable, inline
        # reference can be added natively without copying or deleting rows.
        op.execute(
            "ALTER TABLE resume_documents ADD COLUMN user_id CHAR(32) "
            "CONSTRAINT fk_resume_documents_user_id_users "
            "REFERENCES users (id) ON DELETE RESTRICT"
        )
    else:
        op.add_column("resume_documents", sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_resume_documents_user_id_users", "resume_documents", "users", ["user_id"], ["id"], ondelete="RESTRICT"
        )
    op.create_index("ix_resume_documents_user_id", "resume_documents", ["user_id"])


def downgrade() -> None:
    # Once identity is in use, dropping these tables or ownership would lose
    # security data. Use a forward repair or restore a verified backup instead.
    connection = op.get_bind()
    for table_name in ("users", "registration_invitations", "auth_sessions", "auth_throttles"):
        if connection.scalar(sa.text(f"SELECT count(*) FROM {table_name}")):
            raise RuntimeError("Identity data exists; use a forward migration or backup restore")
    op.drop_index("ix_resume_documents_user_id", table_name="resume_documents")
    if op.get_context().dialect.name == "sqlite":
        op.execute("ALTER TABLE resume_documents DROP COLUMN user_id")
    else:
        op.drop_constraint("fk_resume_documents_user_id_users", "resume_documents", type_="foreignkey")
        op.drop_column("resume_documents", "user_id")
    op.drop_table("auth_throttles")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("registration_invitations")
    op.drop_table("users")
