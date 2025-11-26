import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...dependencies import get_db
from ...models import Conversation, Project
from ...schemas import ChatRequest, CloseSessionRequest
from ...services.chat import stream_chat_response
from ...services.conversation_lifecycle import close_conversation
from ...services.rate_limit import RateLimitExceeded, rate_limiter
router = APIRouter()


@router.post("/start-session")
def start_session():
    return {"session_id": secrets.token_hex(16)}


@router.post("/chat")
def public_chat(payload: ChatRequest, request: Request, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.public_token == payload.bot_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Bot not found")

    rate_key = f"chat:{payload.session_id}:{request.client.host if request.client else 'unknown'}"
    try:
        rate_limiter.check(rate_key, limit=20, window_seconds=30)
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    def token_stream():
        for delta in stream_chat_response(
            db=db,
            project=project,
            session_id=payload.session_id,
            user_message=payload.message,
            page_url=payload.page_url,
            metadata=payload.metadata,
        ):
            yield delta

    return StreamingResponse(token_stream(), media_type="text/plain")


@router.post("/close-session")
def close_session(payload: CloseSessionRequest, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.public_token == payload.bot_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Bot not found")
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.project_id == project.id,
            Conversation.external_session_id == payload.session_id,
        )
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Session not found")

    closed = close_conversation(
        db,
        project,
        conversation,
        reason="user_requested",
        latest_message="Conversation closed by user",
    )
    return {"status": "closed" if closed else "already_closed"}
