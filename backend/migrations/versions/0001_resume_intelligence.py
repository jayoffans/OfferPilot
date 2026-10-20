"""Create resume intelligence tables.

Revision ID: 0001_resume_intelligence
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_resume_intelligence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_documents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=2048), nullable=False),
        sa.Column("file_type", sa.String(length=100), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("resume_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("education", sa.JSON(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("projects", sa.JSON(), nullable=False),
        sa.Column("internships", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resume_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("resume_id", name="uq_profiles_resume_id"),
    )
    op.create_table(
        "profile_facts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("profile_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_profile_facts_confidence"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_profile_facts_profile_id", "profile_facts", ["profile_id"])


def downgrade() -> None:
    op.drop_index("ix_profile_facts_profile_id", table_name="profile_facts")
    op.drop_table("profile_facts")
    op.drop_table("profiles")
    op.drop_table("resume_documents")
