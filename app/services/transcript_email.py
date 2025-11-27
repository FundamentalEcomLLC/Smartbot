import logging
from datetime import datetime, timezone
from typing import Iterable, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import get_settings
from ..enums import MessageRole, TranscriptRecipientType
from ..models import Conversation, Message, Project, ProjectTranscriptRecipient
from .email_utils import send_email

logger = logging.getLogger(__name__)
_settings = get_settings()
_TZ_EST = ZoneInfo("America/New_York")


def _format_timestamp_est(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    as_est = aware.astimezone(_TZ_EST)
    return as_est.strftime("%Y-%m-%d %I:%M %p EST")


def _format_transcript(messages: Iterable[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        timestamp = _format_timestamp_est(message.created_at)
        lines.append(f"[{timestamp}] {message.role.value}: {message.content}")
    return "\n".join(lines) or "No messages recorded."


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


def _clip(text: str, max_len: int = 240) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _summarize_conversation(conversation: Conversation, messages: Iterable[Message]) -> str:
    user_msgs = [m.content.strip() for m in messages if m.role == MessageRole.USER and m.content]
    assistant_msgs = [m.content.strip() for m in messages if m.role == MessageRole.ASSISTANT and m.content]
    summary_parts: list[str] = []
    visitor_label = conversation.visitor_name or "The visitor"
    if user_msgs:
        summary_parts.append(f"{visitor_label} opened the chat saying \"{_clip(user_msgs[0])}\".")
        if len(user_msgs) > 1:
            summary_parts.append(f"Later they added \"{_clip(user_msgs[-1])}\".")
    if assistant_msgs:
        summary_parts.append(
            "Our assistant responded with guidance such as \"{}\".".format(_clip(assistant_msgs[-1]))
        )
    if not summary_parts:
        return "No substantive conversation content was recorded."
    summary_parts.append("We'll follow up with the visitor to keep the next steps moving.")
    return " ".join(summary_parts)


def _build_transcript_attachment(conversation: Conversation, messages: Iterable[Message]) -> dict:
    transcript_text = _format_transcript(messages)
    timestamp = datetime.now(timezone.utc).astimezone(_TZ_EST).strftime("%Y%m%d%H%M%S")
    session_fragment = conversation.external_session_id or str(conversation.id)
    safe_fragment = "".join(ch for ch in session_fragment if ch.isalnum()) or str(conversation.id)
    filename = f"chat-transcript-{safe_fragment}-{timestamp}.txt"
    return {
        "filename": filename,
        "content": transcript_text.encode("utf-8"),
        "maintype": "text",
        "subtype": "plain",
    }


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

    summary = _summarize_conversation(conversation, messages)
    admin_body = "\n".join(
        [
            f"Name: {conversation.visitor_name or 'N/A'}",
            f"Email: {conversation.visitor_email or 'N/A'}",
            f"Phone: {conversation.visitor_phone or 'N/A'}",
            "",
            "Chat Summary:",
            summary,
        ]
    )
    subject = f"{project.name} - Chat Transcript #{conversation.id}"
    attachment = _build_transcript_attachment(conversation, messages)

    admin_sent = False
    if to_list or bcc_list:
        admin_sent = send_email(
            subject,
            admin_body,
            to_list=to_list,
            bcc_list=bcc_list,
            attachments=[attachment],
        )

    visitor_sent = False
    if conversation.visitor_email:
        visitor_body = "\n".join(
            [
                f"Hi {conversation.visitor_name or 'there'},",
                "",
                "Here is a quick recap of our conversation:",
                summary,
                "",
                "We will follow up shortly with the next steps.",
            ]
        )
        visitor_sent = send_email(
            f"{project.name} - Your Chat Summary",
            visitor_body,
            to_list=[conversation.visitor_email],
            attachments=[attachment],
        )

    if admin_sent or visitor_sent:
        conversation.transcript_sent_at = datetime.now(timezone.utc)
        db.add(conversation)
        db.commit()