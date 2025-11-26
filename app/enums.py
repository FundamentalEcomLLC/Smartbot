import enum


class CrawlStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class DocumentSourceType(str, enum.Enum):
    CRAWLED_PAGE = "CRAWLED_PAGE"
    UPLOADED_FILE = "UPLOADED_FILE"
    MANUAL_ENTRY = "MANUAL_ENTRY"


class MessageRole(str, enum.Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class ConversationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    WARNING = "WARNING"
    CLOSED = "CLOSED"


class IntegrationType(str, enum.Enum):
    WEBHOOK = "WEBHOOK"
    ZOHO_SALES_IQ = "ZOHO_SALES_IQ"
    HUBSPOT = "HUBSPOT"
    CUSTOM = "CUSTOM"


class TranscriptRecipientType(str, enum.Enum):
    TO = "TO"
    BCC = "BCC"
