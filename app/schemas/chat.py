from typing import Any, Dict, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    bot_id: str
    session_id: str
    message: str
    page_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ChatResponseChunk(BaseModel):
    message: str
    done: bool = False


class CloseSessionRequest(BaseModel):
    bot_id: str
    session_id: str
