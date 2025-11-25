from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from ..enums import CrawlStatus
from .base import Base, TimestampMixin


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    primary_domain = Column(String(255), nullable=False)
    crawl_status = Column(
        Enum(CrawlStatus, native_enum=False),
        nullable=False,
        default=CrawlStatus.PENDING,
        server_default=CrawlStatus.PENDING.value,
    )
    last_crawled_at = Column(DateTime(timezone=True), nullable=True)
    public_token = Column(String(64), unique=True, nullable=False)
    learning_enabled = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    learning_sample_rate = Column(Integer, nullable=False, default=100, server_default="100")
    learning_stats = Column(JSON, nullable=True)

    owner = relationship("User", back_populates="projects")
    documents = relationship("Document", back_populates="project", cascade="all,delete")
    chunks = relationship("Chunk", back_populates="project", cascade="all,delete")
    bot_config = relationship(
        "BotConfig", back_populates="project", uselist=False, cascade="all,delete"
    )
    custom_qas = relationship("CustomQA", back_populates="project", cascade="all,delete")
    conversations = relationship(
        "Conversation", back_populates="project", cascade="all,delete"
    )
    integrations = relationship(
        "IntegrationConfig", back_populates="project", cascade="all,delete"
    )
    transcript_recipients = relationship(
        "ProjectTranscriptRecipient", back_populates="project", cascade="all,delete"
    )
