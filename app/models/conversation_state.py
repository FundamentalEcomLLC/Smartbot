from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, text
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin


class ConversationState(TimestampMixin, Base):
    __tablename__ = "conversation_states"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(
        Integer,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(64), nullable=True)
    phone_opt_out = Column(Boolean, nullable=False, server_default=text("false"))
    main_goal = Column(Text, nullable=True)
    financing_interested = Column(Boolean, nullable=False, server_default=text("false"))
    budget = Column(Integer, nullable=True)
    sandler_stage = Column(String(32), nullable=False, server_default="greeting")
    last_question_type = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    otp_email = Column(String(255), nullable=True)
    otp_status = Column(String(32), nullable=False, server_default="not_required")
    otp_code_hash = Column(String(128), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    otp_attempts = Column(Integer, nullable=False, server_default=text("0"))
    otp_last_sent_at = Column(DateTime(timezone=True), nullable=True)
    otp_failure_reason = Column(String(255), nullable=True)
    otp_verified_at = Column(DateTime(timezone=True), nullable=True)
    otp_consent_status = Column(String(32), nullable=False, server_default="not_requested")
    otp_consent_prompted_at = Column(DateTime(timezone=True), nullable=True)

    conversation = relationship("Conversation", back_populates="state")
