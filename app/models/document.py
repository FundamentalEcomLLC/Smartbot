from sqlalchemy import JSON, Column, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from ..enums import DocumentSourceType
from .base import Base, TimestampMixin


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_type = Column(Enum(DocumentSourceType, native_enum=False), nullable=False)
    url_or_name = Column(String(1024), nullable=False)
    raw_content = Column(Text, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)

    project = relationship("Project", back_populates="documents")
    chunks = relationship("Chunk", back_populates="document", cascade="all,delete")
