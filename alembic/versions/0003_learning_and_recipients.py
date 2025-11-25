"""learning flags + transcript recipients"""

from alembic import op
import sqlalchemy as sa


revision = "0003_learning_and_recipients"
down_revision = "0002_transcript_emails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("learning_enabled", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "projects",
        sa.Column(
            "learning_sample_rate", sa.Integer(), nullable=False, server_default="100"
        ),
    )
    op.add_column("projects", sa.Column("learning_stats", sa.JSON(), nullable=True))
    op.drop_column("projects", "transcript_recipient_email")

    op.create_table(
        "project_transcript_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False, server_default="TO"),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("project_transcript_recipients")
    op.add_column(
        "projects",
        sa.Column("transcript_recipient_email", sa.String(length=255), nullable=True),
    )
    op.drop_column("projects", "learning_stats")
    op.drop_column("projects", "learning_sample_rate")
    op.drop_column("projects", "learning_enabled")
