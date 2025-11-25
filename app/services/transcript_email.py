import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Iterable, Tuple

from sqlalchemy.orm import Session

from ..config import get_settings
from ..enums import TranscriptRecipientType
from ..models import Conversation, Message, Project, ProjectTranscriptRecipient

logger = logging.getLogger(__name__)
_settings = get_settings()


def _format_transcript(messages: Iterable[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        timestamp = message.created_at.isoformat() if message.created_at else ""
        lines.append(f"[{timestamp}] {message.role.value}: {message.content}")
    return "\n".join(lines) or "No messages recorded."


def _send_email(to_list: list[str], bcc_list: list[str], subject: str, body: str) -> bool:
    if not (_settings.smtp_host and _settings.smtp_from_email):
        logger.warning("SMTP settings incomplete; skipping transcript email")
        return False
    if not to_list and not bcc_list:
        logger.info("No active recipients; skipping transcript email")
        return False
    message = EmailMessage()
    message["From"] = _settings.smtp_from_email
    if to_list:
        message["To"] = ", ".join(to_list)
    if bcc_list:
        message["Bcc"] = ", ".join(bcc_list)
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(_settings.smtp_host, _settings.smtp_port, timeout=15) as client:
            if _settings.smtp_use_tls:
                client.starttls()
            if _settings.smtp_username and _settings.smtp_password:
                client.login(
                    _settings.smtp_username,
                    _settings.smtp_password.get_secret_value(),
                )
            client.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send transcript email: %s", exc)
        return False


def _recipient_lists(project: Project) -> Tuple[list[str], list[str]]:
    to_list: list[str] = []
    bcc_list: list[str] = []
    for recipient in project.transcript_recipients:
        if not recipient.is_active:
            continue
        if recipient.type == TranscriptRecipientType.TO:
            to_list.append(recipient.email)
        else:
            bcc_list.append(recipient.email)
    return to_list, bcc_list


def send_transcript_email(db: Session, project: Project, conversation: Conversation) -> None:
    to_list, bcc_list = _recipient_lists(project)
    if not (to_list or bcc_list):
        return
    if conversation.transcript_sent_at is not None:
        logger.info("Transcript already sent for conversation %s", conversation.id)
        return

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .all()
    )

    body_lines = [
        f"Name: {conversation.visitor_name or 'N/A'}",
        f"Email: {conversation.visitor_email or 'N/A'}",
        f"Phone: {conversation.visitor_phone or 'N/A'}",
        "",
        "Chat Transcript:",
        _format_transcript(messages),
    ]
    body = "\n".join(body_lines)
    subject = f"{project.name} - Chat Transcript #{conversation.id}"

    if _send_email(to_list, bcc_list, subject, body):
        conversation.transcript_sent_at = datetime.now(timezone.utc)
        db.add(conversation)
        db.commit()