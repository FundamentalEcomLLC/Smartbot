import logging
import random
import re
import time
from typing import Dict, Generator, Iterable, List, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from ..config import get_settings
from ..enums import MessageRole
from ..models import BotConfig, Conversation, Message, Project
from .cache import cache
from .embeddings import embed_texts
from .integrations import IntegrationEvent, emit_integration_events
from .rag import fetch_custom_qas, fetch_relevant_chunks

logger = logging.getLogger(__name__)
_settings = get_settings()
_client = OpenAI(api_key=_settings.openai_api_key.get_secret_value())

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,20}")
_NAME_RE = re.compile(r"(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z\s'\-]{1,60})", re.IGNORECASE)


class ChatError(Exception):
    """Raised when OpenAI or retrieval fails."""


def _apply_typing_delay(sample: str, *, cps: float = 28.0) -> None:
    """Sleep briefly so replies feel like a human typing."""

    if not sample:
        time.sleep(0.1)
        return
    delay = len(sample) / max(cps, 1.0)
    delay += random.uniform(-0.05, 0.05)
    time.sleep(max(0.08, min(delay, 1.3)))


def _clean_phone(raw: str) -> Optional[str]:
    digits = re.sub(r"[^\d+]", "", raw)
    if len(re.sub(r"\D", "", digits)) < 6:
        return None
    return digits


def _extract_contact_details(text: str) -> Dict[str, Optional[str]]:
    if not text:
        return {"name": None, "email": None, "phone": None}
    name = None
    email = None
    phone = None
    if (match := _NAME_RE.search(text)):
        name = match.group(1).strip()
    if (match := _EMAIL_RE.search(text)):
        email = match.group(0).strip().rstrip(".,")
    if (match := _PHONE_RE.search(text)):
        phone = _clean_phone(match.group(0))
    return {"name": name, "email": email, "phone": phone}


def _apply_contact_updates(
    conversation: Conversation,
    *,
    name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
) -> bool:
    changed = False
    if name and name != conversation.visitor_name:
        conversation.visitor_name = name
        changed = True
    if email and email != conversation.visitor_email:
        conversation.visitor_email = email
        changed = True
    if phone and phone != conversation.visitor_phone:
        conversation.visitor_phone = phone
        changed = True
    return changed


def _apply_contact_metadata(
    db: Session, conversation: Conversation, metadata: Dict[str, Optional[str]]
) -> None:
    candidate = {
        "name": metadata.get("name")
        or metadata.get("visitor_name")
        or metadata.get("full_name"),
        "email": metadata.get("email") or metadata.get("visitor_email"),
        "phone": metadata.get("phone") or metadata.get("visitor_phone"),
    }
    if _apply_contact_updates(conversation, **candidate):
        db.add(conversation)
        db.commit()


def _get_or_create_conversation(
    db: Session, project: Project, session_id: str
) -> tuple[Conversation, bool]:
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.project_id == project.id,
            Conversation.external_session_id == session_id,
        )
        .first()
    )
    if conversation:
        return conversation, False

    conversation = Conversation(
        project_id=project.id,
        external_session_id=session_id,
    )
    db.add(conversation)
    db.flush()
    return conversation, True


def _save_message(
    db: Session,
    conversation: Conversation,
    role: MessageRole,
    content: str,
) -> Message:
    message = Message(conversation_id=conversation.id, role=role, content=content)
    changed = False
    if role == MessageRole.USER:
        changed = _apply_contact_updates(conversation, **_extract_contact_details(content))
    if changed:
        db.add(conversation)
    db.add(message)
    db.commit()
    return message


def _build_context(blocks: Iterable[str]) -> str:
    context = []
    for idx, block in enumerate(blocks, start=1):
        context.append(f"[Chunk {idx}]\n{block}")
    return "\n\n".join(context)


def _learning_tone_instruction(project: Project) -> str:
    if not project.learning_enabled:
        return ""
    stats = project.learning_stats or {}
    tone = stats.get("dominant_tone", "friendly")
    humor = stats.get("humor_level", 0)
    emojis = stats.get("emoji_usage", 0)
    instructions = [
        f"Use a {tone} tone tailored to this site's audience.",
    ]
    if humor > 0.5:
        instructions.append("Sprinkle light humor when appropriate.")
    if emojis > 0.4:
        instructions.append("Occasionally include relevant emojis.")
    return " ".join(instructions)


def stream_chat_response(
    db: Session,
    project: Project,
    session_id: str,
    user_message: str,
    page_url: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> Generator[str, None, None]:
    """Stream assistant response using OpenAI completions."""

    normalized_question = user_message.strip().lower()
    logger.info(
        "Incoming chat message | project=%s session=%s user=%s",
        project.id,
        session_id,
        user_message,
    )
    cache_key = f"{project.id}:{normalized_question}"
    cached = cache.get(cache_key)
    if cached:
        logger.info("Cache hit for project %s question '%s'", project.id, normalized_question)
        _apply_typing_delay(cached)
        yield cached
        return

    bot_config: BotConfig = project.bot_config or BotConfig(system_prompt="You are a helpful assistant.")
    system_prompt = bot_config.system_prompt or "You are a helpful assistant."
    extra_instructions = []
    if bot_config.additional_instructions:
        extra_instructions.append(bot_config.additional_instructions)
    if learning_instruction := _learning_tone_instruction(project):
        extra_instructions.append(learning_instruction)
    system_messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]
    if extra_instructions:
        system_messages.append(
            {
                "role": "system",
                "content": "CRITICAL: Follow these additional instructions exactly."
                + " \n"
                + "\n".join(extra_instructions),
            }
        )
    logger.debug(
        "Using bot instructions for project %s: %s",
        project.id,
        system_prompt[:200].replace("\n", " "),
    )
    conversation, is_new = _get_or_create_conversation(db, project, session_id)
    if metadata:
        _apply_contact_metadata(db, conversation, metadata)
    _save_message(db, conversation, MessageRole.USER, user_message)

    if is_new:
        emit_integration_events(
            db,
            project,
            conversation,
            IntegrationEvent.CONVERSATION_STARTED,
            user_message,
            page_url,
        )
    emit_integration_events(
        db, project, conversation, IntegrationEvent.USER_MESSAGE, user_message, page_url
    )

    chunks = []
    qas = []
    try:
        embedding = embed_texts([user_message])[0]
        chunks = fetch_relevant_chunks(db, project.id, embedding)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build retrieval context: %s", exc)
    try:
        qas = fetch_custom_qas(db, project.id, user_message)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch custom Q&A entries: %s", exc)

    context_parts = [chunk.content for chunk in chunks]
    qa_parts = [f"Q: {qa.question}\nA: {qa.answer}" for qa in qas]
    combined_context = _build_context(context_parts + qa_parts)

    history = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(6)
        .all()
    )
    chat_history = list(reversed(history))
    if chat_history and chat_history[-1].role == MessageRole.USER and chat_history[-1].content == user_message:
        chat_history = chat_history[:-1]

    messages = system_messages + [
        {
            "role": "system",
            "content": "Use only the provided context. If unsure, say you do not know.",
        },
        {
            "role": "system",
            "content": f"Context:\n{combined_context or 'No context available.'}",
        },
    ]

    for past in chat_history:
        messages.append({"role": past.role.value.lower(), "content": past.content})

    messages.append({"role": "user", "content": user_message})

    try:
        logger.info(
            "OpenAI request | project=%s model=%s max_tokens=%s temperature=%s history=%s",
            project.id,
            _settings.default_model,
            bot_config.max_tokens,
            bot_config.temperature,
            len(messages),
        )
        stream = _client.chat.completions.create(
            model=_settings.default_model,
            messages=messages,
            max_tokens=bot_config.max_tokens,
            temperature=bot_config.temperature,
            stream=True,
        )
        final_text = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            final_text.append(delta)
            _apply_typing_delay(delta)
            yield delta
        answer = "".join(final_text)
        logger.info(
            "OpenAI response | project=%s chars=%s",
            project.id,
            len(answer),
        )
        cache.set(cache_key, answer)
        _save_message(db, conversation, MessageRole.ASSISTANT, answer)
        emit_integration_events(
            db, project, conversation, IntegrationEvent.BOT_REPLY, answer, page_url
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat completion failed: %s", exc)
        fallback = "I'm having trouble answering right now. Please try again later."
        _apply_typing_delay(fallback)
        yield fallback
        _save_message(db, conversation, MessageRole.ASSISTANT, fallback)
        emit_integration_events(
            db, project, conversation, IntegrationEvent.BOT_REPLY, fallback, page_url
        )
