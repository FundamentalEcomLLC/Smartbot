"""add otp fields to conversation state"""

from alembic import op
import sqlalchemy as sa


revision = "0005_conversation_otp"
down_revision = "0004_conversation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_states", sa.Column("otp_email", sa.String(length=255), nullable=True))
    op.add_column(
        "conversation_states",
        sa.Column(
            "otp_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_required",
        ),
    )
    op.add_column(
        "conversation_states",
        sa.Column(
            "otp_consent_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_requested",
        ),
    )
    op.add_column(
        "conversation_states",
        sa.Column(
            "otp_consent_prompted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column("conversation_states", sa.Column("otp_code_hash", sa.String(length=128), nullable=True))
    op.add_column(
        "conversation_states",
        sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_states",
        sa.Column(
            "otp_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "conversation_states",
        sa.Column("otp_last_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_states",
        sa.Column("otp_failure_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "conversation_states",
        sa.Column("otp_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_states", "otp_verified_at")
    op.drop_column("conversation_states", "otp_failure_reason")
    op.drop_column("conversation_states", "otp_last_sent_at")
    op.drop_column("conversation_states", "otp_attempts")
    op.drop_column("conversation_states", "otp_expires_at")
    op.drop_column("conversation_states", "otp_code_hash")
    op.drop_column("conversation_states", "otp_status")
    op.drop_column("conversation_states", "otp_consent_prompted_at")
    op.drop_column("conversation_states", "otp_consent_status")
    op.drop_column("conversation_states", "otp_email")
