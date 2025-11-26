"""Helpers that keep conversations in sync when closing or injecting system messages."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import ConversationStatus, MessageRole
from ..models import Conversation, Message, Project
from .integrations import IntegrationEvent, emit_integration_events
from .learning import update_learning_stats
from .transcript_email import send_transcript_email

logger = logging.getLogger(__name__)


def record_system_message(
    db: Session,
    conversation: Conversation,
    content: str,
    *,
    commit: bool = True,
) -> Message:
    """Persist a system-authored message and update bot timestamps."""

    message = Message(
        conversation_id=conversation.id,
        role=MessageRole.SYSTEM,
        content=content,
    )
    conversation.last_bot_message_at = datetime.now(timezone.utc)
    db.add(conversation)
    db.add(message)
    if commit:
        db.commit()
    else:
        db.flush()
    return message


def close_conversation(
    db: Session,
    project: Project,
    conversation: Conversation,
    *,
    reason: str,
    latest_message: str,
    closing_message: Optional[str] = None,
) -> bool:
    """Mark a conversation closed, emit integrations, and dispatch transcripts."""

    if conversation.status == ConversationStatus.CLOSED:
        logger.info(
            "Conversation %s already closed; skipping duplicate close",
            conversation.id,
        )
        return False

    conversation.status = ConversationStatus.CLOSED
    conversation.closed_at = datetime.now(timezone.utc)
    conversation.closed_reason = reason
    conversation.inactivity_warning_sent_at = None
    db.add(conversation)

    if closing_message:
        record_system_message(db, conversation, closing_message, commit=False)

    db.flush()
    db.commit()

    emit_integration_events(
        db,
        project,
        conversation,
        IntegrationEvent.CONVERSATION_ENDED,
        latest_message,
    )
    try:
        update_learning_stats(db, project, conversation)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Learning stats update failed during close: %s", exc)
    try:
        send_transcript_email(db, project, conversation)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Transcript email failed during close: %s", exc)
    return True
