from sqlalchemy import JSON, Boolean, Column, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from ..enums import IntegrationType
from .base import Base, TimestampMixin


class IntegrationConfig(TimestampMixin, Base):
    __tablename__ = "integration_configs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    type = Column(Enum(IntegrationType, native_enum=False), nullable=False)
    config_json = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)

    project = relationship("Project", back_populates="integrations")
