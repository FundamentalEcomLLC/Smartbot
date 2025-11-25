from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin


class CustomQA(TimestampMixin, Base):
    __tablename__ = "custom_qas"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)

    project = relationship("Project", back_populates="custom_qas")
