import hashlib
import hmac
import json
import logging
from typing import Dict

import httpx
from sqlalchemy.orm import Session

from ..models import Conversation, IntegrationConfig, Project

logger = logging.getLogger(__name__)


class IntegrationEvent(str):
    CONVERSATION_STARTED = "conversation_started"
    USER_MESSAGE = "user_message"
    BOT_REPLY = "bot_reply"
    CONVERSATION_ENDED = "conversation_ended"


def _sign_payload(secret: str, payload: Dict) -> str:
    body = json.dumps(payload).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _dispatch_webhook(config: IntegrationConfig, payload: Dict) -> None:
    url = config.config_json.get("url")
    secret = config.config_json.get("secret", "")
    if not url:
        logger.warning("Webhook config missing URL for integration %s", config.id)
        return
    signature = _sign_payload(secret, payload)
    try:
        response = httpx.post(url, json=payload, headers={"X-Signature": signature}, timeout=10)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Webhook dispatch failed: %s", exc)


def _log_stub(destination: str, payload: Dict) -> None:
    logger.info("[Integration:%s] %s", destination, payload)


def emit_integration_events(
    db: Session,
    project: Project,
    conversation: Conversation,
    event_type: IntegrationEvent,
    latest_message: str,
    page_url: str | None = None,
) -> None:
    payload = {
        "project_id": project.id,
        "conversation_id": conversation.id,
        "session_id": conversation.external_session_id,
        "event_type": event_type,
        "latest_message": latest_message,
        "page_url": page_url,
        "visitor": {
            "name": conversation.visitor_name,
            "email": conversation.visitor_email,
            "phone": conversation.visitor_phone,
        },
    }
    integrations = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.project_id == project.id,
            IntegrationConfig.is_active.is_(True),
        )
        .all()
    )
    for config in integrations:
        if config.type.value == "WEBHOOK":
            _dispatch_webhook(config, payload)
        else:
            _log_stub(config.type.value, payload)
