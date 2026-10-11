"""Add managed file metadata and durable resume cleanup operations.

Revision ID: 0003_resume_file_lifecycle
Revises: 0002_identity_ownership
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_resume_file_lifecycle"
down_revision = "0002_identity_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        # Keep the existing parent table in place. Rebuilding it with foreign
        # keys enabled can cascade-delete its profiles and profile facts.
        for statement in (
            "ALTER TABLE resume_documents ADD COLUMN object_key VARCHAR(64)",
            "ALTER TABLE resume_documents ADD COLUMN byte_size INTEGER "
            "CONSTRAINT ck_resume_documents_byte_size CHECK (byte_size >= 0)",
            "ALTER TABLE resume_documents ADD COLUMN sha256 VARCHAR(64)",
            "ALTER TABLE resume_documents ADD COLUMN processing_status VARCHAR(24) "
            "NOT NULL DEFAULT 'parsed' CONSTRAINT ck_resume_documents_processing_status "
            "CHECK (processing_status IN ('uploading', 'uploaded', 'parsed', 'failed'))",
            "ALTER TABLE resume_documents ADD COLUMN lifecycle_status VARCHAR(24) "
            "NOT NULL DEFAULT 'active' CONSTRAINT ck_resume_documents_lifecycle_status "
            "CHECK (lifecycle_status IN ('active', 'deleting', 'delete_failed'))",
        ):
            op.execute(statement)
    else:
        op.add_column("resume_documents", sa.Column("object_key", sa.String(length=64), nullable=True))
        op.add_column("resume_documents", sa.Column("byte_size", sa.Integer(), nullable=True))
        op.add_column("resume_documents", sa.Column("sha256", sa.String(length=64), nullable=True))
        op.add_column(
            "resume_documents",
            sa.Column("processing_status", sa.String(length=24), server_default=sa.text("'parsed'"), nullable=False),
        )
        op.add_column(
            "resume_documents",
            sa.Column("lifecycle_status", sa.String(length=24), server_default=sa.text("'active'"), nullable=False),
        )
        op.create_check_constraint("ck_resume_documents_byte_size", "resume_documents", "byte_size >= 0")
        op.create_check_constraint(
            "ck_resume_documents_processing_status", "resume_documents",
            "processing_status IN ('uploading', 'uploaded', 'parsed', 'failed')",
        )
        op.create_check_constraint(
            "ck_resume_documents_lifecycle_status", "resume_documents",
            "lifecycle_status IN ('active', 'deleting', 'delete_failed')",
        )
    op.create_index("uq_resume_documents_object_key", "resume_documents", ["object_key"], unique=True)

    op.create_table(
        "resume_file_operations",
        sa.Column("resume_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(length=64), nullable=True),
        sa.Column("legacy_path", sa.String(length=2048), nullable=True),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("reason IN ('upload', 'delete')", name="ck_resume_file_operations_reason"),
        sa.CheckConstraint(
            "status IN ('uploading', 'deleting', 'delete_failed', 'deleted')",
            name="ck_resume_file_operations_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_resume_file_operations_attempt_count"),
    )
    op.create_index("ix_resume_file_operations_user_id", "resume_file_operations", ["user_id"])


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("File lifecycle downgrade requires an online data safety check")
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT resume_id FROM resume_file_operations LIMIT 1")).first() is not None:
        raise RuntimeError("File lifecycle data exists; refusing to discard operations during downgrade")
    managed = connection.execute(sa.text(
        "SELECT id FROM resume_documents WHERE object_key IS NOT NULL "
        "OR byte_size IS NOT NULL OR sha256 IS NOT NULL "
        "OR processing_status <> 'parsed' OR lifecycle_status <> 'active' LIMIT 1"
    )).first()
    if managed is not None:
        raise RuntimeError("File lifecycle data exists; refusing to discard metadata or states during downgrade")

    op.drop_index("ix_resume_file_operations_user_id", table_name="resume_file_operations")
    op.drop_table("resume_file_operations")
    op.drop_index("uq_resume_documents_object_key", table_name="resume_documents")
    if op.get_context().dialect.name != "sqlite":
        for name in (
            "ck_resume_documents_byte_size", "ck_resume_documents_processing_status",
            "ck_resume_documents_lifecycle_status",
        ):
            op.drop_constraint(name, "resume_documents", type_="check")
    for column_name in ("lifecycle_status", "processing_status", "sha256", "byte_size", "object_key"):
        if op.get_context().dialect.name == "sqlite":
            op.execute(f"ALTER TABLE resume_documents DROP COLUMN {column_name}")
        else:
            op.drop_column("resume_documents", column_name)
