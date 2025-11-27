from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from ..enums import MessageRole
from .base import Base, TimestampMixin


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    external_session_id = Column(String(255), nullable=False)
    crm_contact_id = Column(String(255), nullable=True)
    visitor_name = Column(String(255), nullable=True)
    visitor_email = Column(String(255), nullable=True)
    visitor_phone = Column(String(64), nullable=True)
    transcript_sent_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all,delete")
    state = relationship(
        "ConversationState",
        back_populates="conversation",
        uselist=False,
        cascade="all,delete",
    )


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(Enum(MessageRole, native_enum=False), nullable=False)
    content = Column(Text, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")
