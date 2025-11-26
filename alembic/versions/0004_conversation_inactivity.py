"""conversation inactivity tracking"""

from alembic import op
import sqlalchemy as sa


revision = "0004_conversation_inactivity"
down_revision = "0003_learning_and_recipients"
branch_labels = None
depends_on = None


ConversationStatus = sa.Enum(
    "ACTIVE",
    "WARNING",
    "CLOSED",
    name="conversationstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "status",
            ConversationStatus,
            nullable=False,
            server_default="ACTIVE",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("last_user_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("last_bot_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("inactivity_warning_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("closed_reason", sa.String(length=255), nullable=True),
    )

    op.execute("UPDATE conversations SET status='ACTIVE' WHERE status IS NULL")


def downgrade() -> None:
    op.drop_column("conversations", "closed_reason")
    op.drop_column("conversations", "closed_at")
    op.drop_column("conversations", "inactivity_warning_sent_at")
    op.drop_column("conversations", "last_bot_message_at")
    op.drop_column("conversations", "last_user_message_at")
    op.drop_column("conversations", "status")

