from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import ARRAY

from .base import Base, TimestampMixin


class Chunk(TimestampMixin, Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(ARRAY(Float), nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)

    project = relationship("Project", back_populates="chunks")
    document = relationship("Document", back_populates="chunks")
