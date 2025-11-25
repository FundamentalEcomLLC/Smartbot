from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin


class BotConfig(TimestampMixin, Base):
    __tablename__ = "bot_configs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    system_prompt = Column(Text, nullable=False, default="You are a helpful website assistant.")
    additional_instructions = Column(Text, nullable=True)
    temperature = Column(Float, nullable=False, default=0.2)
    max_tokens = Column(Integer, nullable=False, default=700)
    language = Column(String(32), nullable=True)
    allowed_sources = Column(JSON, nullable=True)

    project = relationship("Project", back_populates="bot_config")
