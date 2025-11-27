import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, Iterable, List, Optional

from openai import OpenAI
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import get_settings
from ..enums import MessageRole
from ..models import BotConfig, Conversation, ConversationState, Message, Project
from .cache import cache
from .embeddings import embed_texts
from .integrations import IntegrationEvent, emit_integration_events
from .rag import fetch_custom_qas, fetch_relevant_chunks

logger = logging.getLogger(__name__)
_settings = get_settings()
_client = OpenAI(api_key=_settings.openai_api_key.get_secret_value())

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,20}")
_NAME_PATTERNS = [
    re.compile(r"(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z\s'\-]{1,60})", re.IGNORECASE),
    re.compile(r"(?:call me|you can call me|name's)\s+([A-Za-z][A-Za-z\s'\-]{1,60})", re.IGNORECASE),
]
_BUDGET_RE = re.compile(
    r"\$?\s*((?:\d{1,3}(?:,\d{3})+)|\d+(?:\.\d+)?)\s*(k|grand|thousand)?",
    re.IGNORECASE,
)
_FINANCING_KEYWORDS = (
    "credit",
    "credit line",
    "financing",
    "payment plan",
    "pay later",
    "line of credit",
)
_REPETITION_PATTERNS = (
    "you already asked",
    "you already said",
    "i already told",
    "i told you already",
    "as i said",
)
_PHONE_DECLINE_PATTERNS = (
    "no need",
    "don't need",
    "do not need",
    "no phone",
    "rather not",
    "prefer not",
    "not necessary",
)
_GOAL_HINT_KEYWORDS = ("need", "want", "goal", "looking", "trying", "just")


class ChatError(Exception):
    """Raised when OpenAI or retrieval fails."""


@dataclass
class SessionState:
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    phone_opt_out: bool = False
    main_goal: Optional[str] = None
    financing_interested: bool = False
    budget: Optional[int] = None
    sandler_stage: str = "greeting"
    last_question_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def summary_bits(self) -> List[str]:
        bits: List[str] = []
        if self.name:
            bits.append(f"Name: {self.name}")
        if self.email:
            bits.append(f"Email: {self.email}")
        if self.phone:
            bits.append(f"Phone: {self.phone}")
        elif self.phone_opt_out:
            bits.append("Phone: visitor declined to share")
        if self.main_goal:
            bits.append(f"Goal: {self.main_goal}")
        if self.financing_interested:
            budget_text = f" around ${self.budget}" if self.budget else ""
            bits.append(f"Financing interest{budget_text}")
        return bits


def _state_from_model(model: ConversationState | None) -> SessionState:
    if not model:
        return SessionState()
    return SessionState(
        name=model.name,
        email=model.email,
        phone=model.phone,
        phone_opt_out=bool(model.phone_opt_out),
        main_goal=model.main_goal,
        financing_interested=bool(model.financing_interested),
        budget=model.budget,
        sandler_stage=model.sandler_stage or "greeting",
        last_question_type=model.last_question_type,
        metadata=dict(model.metadata_json or {}),
    )


def _apply_state_to_model(model: ConversationState, state: SessionState) -> None:
    model.name = state.name
    model.email = state.email
    model.phone = state.phone
    model.phone_opt_out = state.phone_opt_out
    model.main_goal = state.main_goal
    model.financing_interested = state.financing_interested
    model.budget = state.budget
    model.sandler_stage = state.sandler_stage
    model.last_question_type = state.last_question_type
    model.metadata_json = state.metadata or None


def _load_session_state(db: Session, conversation: Conversation) -> tuple[ConversationState, SessionState]:
    model = conversation.state
    if not model:
        model = ConversationState(conversation_id=conversation.id)
        db.add(model)
        db.flush()
        conversation.state = model
    return model, _state_from_model(model)


def _save_session_state(
    db: Session, model: ConversationState, state: SessionState, *, refresh_conversation: bool = False
) -> None:
    _apply_state_to_model(model, state)
    db.add(model)
    db.commit()
    if refresh_conversation:
        db.refresh(model.conversation)


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
    for pattern in _NAME_PATTERNS:
        if match := pattern.search(text):
            name = match.group(1).strip()
            break
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


def _update_state_from_contact_details(state: SessionState, details: Dict[str, Optional[str]]) -> bool:
    changed = False
    if details.get("name") and details["name"] != state.name:
        state.name = details["name"]
        state.last_question_type = None if state.last_question_type == "ask_name" else state.last_question_type
        changed = True
    if details.get("email") and details["email"] != state.email:
        state.email = details["email"]
        state.last_question_type = None if state.last_question_type == "ask_email" else state.last_question_type
        changed = True
    if details.get("phone"):
        cleaned = details["phone"]
        if cleaned != state.phone:
            state.phone = cleaned
            state.phone_opt_out = False
            if state.last_question_type == "ask_phone":
                state.last_question_type = None
            changed = True
    return changed


def _detect_budget_value(text: str) -> Optional[int]:
    if not text:
        return None
    match = _BUDGET_RE.search(text)
    if not match:
        if text.strip().lower().endswith("k") and text[:-1].isdigit():
            return int(float(text[:-1]) * 1000)
        return None
    raw, multiplier = match.groups()
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    if multiplier:
        value *= 1000
    return int(value)


def _detect_financing_intent(*texts: str) -> bool:
    blob = " ".join(filter(None, (text or "" for text in texts))).lower()
    return any(keyword in blob for keyword in _FINANCING_KEYWORDS)


def _detect_main_goal(text: str, last_question: Optional[str]) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    if last_question == "ask_main_goal" and len(stripped) > 3:
        return stripped
    lowered = stripped.lower()
    if len(stripped) < 20:
        return None
    if any(keyword in lowered for keyword in _GOAL_HINT_KEYWORDS):
        return stripped
    return None


def _user_declined_phone(text: str) -> bool:
    lowered = (text or "").lower()
    if not any(word in lowered for word in ("phone", "number")):
        return False
    return any(phrase in lowered for phrase in _PHONE_DECLINE_PATTERNS)


def _update_state_from_user_message(state: SessionState, text: str) -> bool:
    changed = False
    main_goal = _detect_main_goal(text, state.last_question_type)
    if main_goal and main_goal != state.main_goal:
        state.main_goal = main_goal
        if state.sandler_stage in {"greeting", "contract"}:
            state.sandler_stage = "pain"
        state.last_question_type = None
        changed = True
    if _user_declined_phone(text) and not state.phone_opt_out:
        state.phone_opt_out = True
        state.last_question_type = None
        changed = True
    if _detect_financing_intent(text):
        if not state.financing_interested:
            state.financing_interested = True
            state.sandler_stage = "budget"
            changed = True
        budget = _detect_budget_value(text)
        if budget and budget != state.budget:
            state.budget = budget
            changed = True
    else:
        if state.last_question_type == "ask_budget":
            budget = _detect_budget_value(text)
            if budget and budget != state.budget:
                state.budget = budget
                state.last_question_type = None
                changed = True
    if state.last_question_type in {"ask_name", "ask_email", "ask_phone"}:
        # Reset question tracker once visitor responds
        state.last_question_type = None
    return changed


def _record_last_question_type(state: SessionState, assistant_text: str) -> bool:
    lowered = (assistant_text or "").lower()
    question_type: Optional[str] = None
    if any(keyword in lowered for keyword in ("what should i call", "your name")):
        question_type = "ask_name"
    elif "email" in lowered and "best" in lowered:
        question_type = "ask_email"
    elif "phone" in lowered or "number" in lowered:
        question_type = "ask_phone"
    elif "goal" in lowered or "what you're looking" in lowered:
        question_type = "ask_main_goal"
    elif "budget" in lowered:
        question_type = "ask_budget"
    if question_type and question_type != state.last_question_type:
        state.last_question_type = question_type
        return True
    return False


def _user_complaining_about_repetition(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in _REPETITION_PATTERNS)


def _state_summary_for_user(state: SessionState) -> str:
    bits = state.summary_bits()
    return "; ".join(bits) if bits else "what you've shared so far"


def _build_repetition_reply(state: SessionState) -> str:
    summary = _state_summary_for_user(state)
    next_step = (
        "I'll move us forward by outlining the right website plan and how financing works." if state.financing_interested else "I'll jump ahead to the next step so we can keep things moving."
    )
    return (
        "You're right — sorry for repeating myself, and thanks for your patience.\n\n"
        f"Here's what I've captured: {summary}.\n\n"
        f"{next_step} Does that sound good, or is there anything you'd like to adjust?"
    )


def _session_state_instruction(state: SessionState) -> Optional[str]:
    bits = state.summary_bits()
    if not bits and not state.phone_opt_out:
        return None
    details = "\n".join(bits or ["No structured data captured yet."])
    phone_line = "Visitor previously declined to share a phone number; respect that." if state.phone_opt_out else ""
    guardrails = (
        "Never re-ask for information already listed in this session memory unless the visitor explicitly says it changed.",
        "If the visitor says you already asked or told them something, apologize once, summarize what you know, and progress the conversation.",
    )
    financing_line = (
        "Visitor explicitly asked about credit/financing; provide a concrete answer before moving on."
        if state.financing_interested
        else ""
    )
    extras = "\n".join(filter(None, [phone_line, financing_line]))
    instructions = "\n".join(guardrails)
    return f"Session memory:\n{details}\n{extras}\n{instructions}"


def _collect_previous_conversation_context(
    db: Session,
    project_id: int,
    conversation: Conversation,
    *,
    max_conversations: int = 3,
    max_messages: int = 40,
) -> List[Dict[str, str]]:
    """Return prior conversation transcripts for the same visitor."""

    filters = []
    if conversation.visitor_email:
        filters.append(Conversation.visitor_email == conversation.visitor_email)
    if conversation.visitor_phone:
        filters.append(Conversation.visitor_phone == conversation.visitor_phone)
    if conversation.visitor_name:
        filters.append(Conversation.visitor_name.ilike(conversation.visitor_name))
    if not filters:
        return []

    previous_conversations = (
        db.query(Conversation)
        .filter(
            Conversation.project_id == project_id,
            Conversation.id != conversation.id,
            or_(*filters),
        )
        .order_by(Conversation.updated_at.desc())
        .limit(max_conversations)
        .all()
    )

    contexts: List[Dict[str, str]] = []
    for prev in previous_conversations:
        prev_messages = (
            db.query(Message)
            .filter(Message.conversation_id == prev.id)
            .order_by(Message.created_at.asc())
            .limit(max_messages)
            .all()
        )
        if not prev_messages:
            continue
        transcript = "\n".join(
            f"{msg.role.value.capitalize()}: {msg.content}" for msg in prev_messages
        )
        last_updated = (
            prev.updated_at.strftime("%Y-%m-%d %H:%M %Z") if prev.updated_at else "previously"
        )
        contexts.append(
            {
                "role": "system",
                "content": (
                    "Reference from an earlier conversation with this same visitor. "
                    "Use it to avoid re-asking for details they already shared and to maintain continuity. "
                    f"Conversation #{prev.id} last updated {last_updated}.\n{transcript}"
                ),
            }
        )
    return contexts


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
    state_model, session_state = _load_session_state(db, conversation)
    state_dirty = False

    if metadata:
        _apply_contact_metadata(db, conversation, metadata)
        state_dirty |= _update_state_from_contact_details(session_state, metadata)

    contact_details = _extract_contact_details(user_message)
    if _apply_contact_updates(conversation, **contact_details):
        db.add(conversation)
        state_dirty = True
    state_dirty |= _update_state_from_contact_details(session_state, contact_details)
    state_dirty |= _update_state_from_user_message(session_state, user_message)

    _save_message(db, conversation, MessageRole.USER, user_message)

    if state_dirty:
        _save_session_state(db, state_model, session_state)

    if _user_complaining_about_repetition(user_message):
        reply = _build_repetition_reply(session_state)
        session_state.sandler_stage = "solution"
        session_state.metadata["repetition_acknowledged"] = True
        _save_session_state(db, state_model, session_state)
        _apply_typing_delay(reply)
        yield reply
        _save_message(db, conversation, MessageRole.ASSISTANT, reply)
        emit_integration_events(
            db, project, conversation, IntegrationEvent.BOT_REPLY, reply, page_url
        )
        return

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
        .order_by(Message.created_at.asc())
        .all()
    )
    chat_history = list(history)
    if chat_history and chat_history[-1].role == MessageRole.USER and chat_history[-1].content == user_message:
        chat_history = chat_history[:-1]

    session_instruction = _session_state_instruction(session_state)
    if session_instruction:
        system_messages.append({"role": "system", "content": session_instruction})

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

    previous_sessions = _collect_previous_conversation_context(db, project.id, conversation)
    messages.extend(previous_sessions)

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
        state_changed = False
        if _record_last_question_type(session_state, answer):
            state_changed = True
        if session_state.financing_interested and not session_state.metadata.get("financing_acknowledged"):
            session_state.metadata["financing_acknowledged"] = True
            state_changed = True
        if state_changed:
            _save_session_state(db, state_model, session_state)
        emit_integration_events(
            db, project, conversation, IntegrationEvent.BOT_REPLY, answer, page_url
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat completion failed: %s", exc)
        fallback = "I'm having trouble answering right now. Please try again later."
        _apply_typing_delay(fallback)
        yield fallback
        _save_message(db, conversation, MessageRole.ASSISTANT, fallback)
        if _record_last_question_type(session_state, fallback):
            _save_session_state(db, state_model, session_state)
        emit_integration_events(
            db, project, conversation, IntegrationEvent.BOT_REPLY, fallback, page_url
        )
