"""add transcript recipient + visitor contact fields"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_transcript_emails"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("transcript_recipient_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("visitor_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("visitor_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("visitor_phone", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("transcript_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "transcript_sent_at")
    op.drop_column("conversations", "visitor_phone")
    op.drop_column("conversations", "visitor_email")
    op.drop_column("conversations", "visitor_name")
    op.drop_column("projects", "transcript_recipient_email")
