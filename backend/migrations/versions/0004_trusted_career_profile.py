"""Add a single reviewed career profile and an isolated candidate pool.

Revision ID: 0004_trusted_career_profile
Revises: 0003_resume_file_lifecycle

Legacy resume-derived profiles/facts are deliberately neither migrated nor
promoted: their rows have no user-confirmation proof.
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_trusted_career_profile"
down_revision = "0003_resume_file_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "career_profiles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("schema_version", sa.String(20), server_default=sa.text("'1.0'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", name="uq_career_profiles_user_id"),
        sa.CheckConstraint("revision >= 0", name="ck_career_profiles_revision"),
    )
    op.create_table(
        "career_profile_entries",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("profile_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("resume_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("field_sources", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resume_documents.id"], ondelete="SET NULL"),
        sa.CheckConstraint("category IN ('education', 'skill', 'project', 'experience')", name="ck_career_profile_entries_category"),
        sa.CheckConstraint("source_kind IN ('manual', 'resume_confirmed', 'user_modified', 'resume_deleted')", name="ck_career_profile_entries_source_kind"),
        sa.CheckConstraint("position >= 0", name="ck_career_profile_entries_position"),
    )
    op.create_index("ix_career_profile_entries_profile_id", "career_profile_entries", ["profile_id"])
    op.create_index("ix_career_profile_entries_resume_id", "career_profile_entries", ["resume_id"])
    op.create_table(
        "profile_candidates",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("profile_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resume_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("batch_key", sa.String(100), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("batch_digest", sa.String(64), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_status", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("extraction_version", sa.String(100), nullable=False),
        sa.Column("entry_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resume_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["career_profile_entries.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("resume_id", "batch_key", "item_index", name="uq_profile_candidates_batch_item"),
        sa.CheckConstraint("category IN ('education', 'skill', 'project', 'experience')", name="ck_profile_candidates_category"),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'modified', 'rejected')", name="ck_profile_candidates_status"),
        sa.CheckConstraint("evidence_status IN ('supported', 'unsupported', 'needs_review')", name="ck_profile_candidates_evidence_status"),
        sa.CheckConstraint("item_index >= 0", name="ck_profile_candidates_item_index"),
    )
    op.create_index("ix_profile_candidates_profile_id", "profile_candidates", ["profile_id"])
    op.create_index("ix_profile_candidates_resume_id", "profile_candidates", ["resume_id"])


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("Career profile downgrade requires an online data safety check")
    for table in ("career_profiles", "career_profile_entries", "profile_candidates"):
        if op.get_bind().execute(sa.text(f"SELECT id FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError("Career profile data exists; refusing to discard reviewed data during downgrade")
    op.drop_index("ix_profile_candidates_resume_id", table_name="profile_candidates")
    op.drop_index("ix_profile_candidates_profile_id", table_name="profile_candidates")
    op.drop_table("profile_candidates")
    op.drop_index("ix_career_profile_entries_resume_id", table_name="career_profile_entries")
    op.drop_index("ix_career_profile_entries_profile_id", table_name="career_profile_entries")
    op.drop_table("career_profile_entries")
    op.drop_table("career_profiles")
