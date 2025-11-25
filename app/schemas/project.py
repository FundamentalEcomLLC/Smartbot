from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict

from ..enums import CrawlStatus, TranscriptRecipientType


class TranscriptRecipient(BaseModel):
    id: int
    email: str
    type: TranscriptRecipientType
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    name: str
    primary_domain: str = Field(..., description="Base domain or https URL to crawl")


class ProjectRead(BaseModel):
    id: int
    name: str
    primary_domain: str
    crawl_status: CrawlStatus
    last_crawled_at: Optional[datetime]
    public_token: str
    learning_enabled: bool
    learning_sample_rate: int
    transcript_recipients: List[TranscriptRecipient] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ProjectLearningUpdate(BaseModel):
    learning_enabled: bool = Field(True, description="Allow chatbot to adapt tone per project")
    learning_sample_rate: int = Field(
        100,
        ge=1,
        le=100,
        description="Percent of conversations to include in learning stats",
    )

    model_config = ConfigDict(from_attributes=True)


class ProjectCrawlStatus(BaseModel):
    status: CrawlStatus
    last_crawled_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)
