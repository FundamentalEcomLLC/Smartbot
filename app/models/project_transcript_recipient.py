from sqlalchemy import Column, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from ..enums import TranscriptRecipientType
from .base import Base, TimestampMixin


class ProjectTranscriptRecipient(TimestampMixin, Base):
    __tablename__ = "project_transcript_recipients"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(255), nullable=False)
    type = Column(
        Enum(TranscriptRecipientType, native_enum=False),
        nullable=False,
        default=TranscriptRecipientType.TO,
        server_default=TranscriptRecipientType.TO.value,
    )
    is_active = Column(Integer, nullable=False, default=1, server_default="1")

    project = relationship("Project", back_populates="transcript_recipients")
