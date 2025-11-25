import logging
import secrets
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...dependencies import get_db
from ...models import Conversation, Project
from ...schemas import ChatRequest, CloseSessionRequest
from ...services.chat import stream_chat_response
from ...services.integrations import IntegrationEvent, emit_integration_events
from ...services.learning import update_learning_stats
from ...services.transcript_email import send_transcript_email
from ...services.rate_limit import RateLimitExceeded, rate_limiter

logger = logging.getLogger(__name__)
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

    emit_integration_events(
        db,
        project,
        conversation,
        IntegrationEvent.CONVERSATION_ENDED,
        "Conversation closed by user",
    )
    try:
        update_learning_stats(db, project, conversation)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Learning stats update failed: %s", exc)
    try:
        send_transcript_email(db, project, conversation)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Transcript email failed: %s", exc)
    return {"status": "closed"}
