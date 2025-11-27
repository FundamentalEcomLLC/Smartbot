from sqlalchemy import Boolean, Column, ForeignKey, Integer, JSON, String, Text, text
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

    conversation = relationship("Conversation", back_populates="state")
