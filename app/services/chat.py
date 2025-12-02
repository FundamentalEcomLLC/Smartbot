import hashlib
import logging
import random
import re
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, Iterable, List, Optional

from openai import OpenAI
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import get_settings
from ..enums import MessageRole
from ..models import BotConfig, Conversation, ConversationState, Message, Project
from .cache import cache
from .email_utils import send_email
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
_OTP_CODE_LENGTH = 6
_OTP_TTL_MINUTES = 15
_OTP_MAX_ATTEMPTS = 5
_HISTORY_YES_TOKENS = (
    "yes",
    "yeah",
    "yep",
    "sure",
    "please do",
    "please",
    "go ahead",
    "use it",
    "pull it in",
    "reference it",
)
_HISTORY_NO_TOKENS = (
    "no",
    "nope",
    "nah",
    "start fresh",
    "start over",
    "new conversation",
    "don't",
    "skip",
    "rather not",
)
_HISTORY_REQUEST_PATTERNS = (
    "previous conversation",
    "previous chat",
    "last chat",
    "last conversation",
    "earlier conversation",
    "old chat",
    "past chat",
    "pick up where we left",
    "continue where we left",
    "pull up my chat",
    "pull up my conversation",
)
_HISTORY_FAILURE_TEXT = (
    "No problem, we’ll just continue without using your previous chat history. What would you like to focus on right now?"
)

_LEAD_NAME_REFUSALS = (
    "prefer not to share my name",
    "prefer not to give my name",
    "dont want to share my name",
    "don't want to share my name",
    "rather not share my name",
    "no name",
    "keep my name private",
    "stay anonymous",
    "no need for my name",
)
_LEAD_EMAIL_REFUSALS = (
    "dont want to share my email",
    "don't want to share my email",
    "no email",
    "no need for email",
    "prefer not to give my email",
    "rather not give my email",
    "keep my email private",
    "no emails please",
)
_LEAD_EMAIL_HESITATION = (
    "why do you need my email",
    "why do you need email",
    "why do you need the email",
    "why email",
    "not sure about email",
    "hesitant to share email",
    "do you need my email",
    "is email required",
)
_LEAD_SIGN_OFF_PATTERNS = (
    "thanks",
    "thank you",
    "appreciate it",
    "that's all",
    "thats all",
    "that is all",
    "bye",
    "goodbye",
    "talk soon",
    "talk later",
    "have a good day",
    "have a great day",
    "catch you later",
)


def _preferred_lead_name(state: SessionState) -> str:
    if state.name:
        return state.name
    if state.metadata.get("lead_preferred_name"):
        return state.metadata["lead_preferred_name"]
    return "friend"


def _lead_capture_stage(state: SessionState) -> str:
    metadata = state.metadata
    if not state.name and not metadata.get("lead_name_refused"):
        return "name"
    if not state.email and not metadata.get("lead_email_refused"):
        return "email"
    if not state.phone and not state.phone_opt_out and not metadata.get("lead_phone_refused"):
        return "phone"
    return "complete"


def _user_refused_name(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(pattern in lowered for pattern in _LEAD_NAME_REFUSALS)


def _user_refused_email(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(pattern in lowered for pattern in _LEAD_EMAIL_REFUSALS)


def _user_hesitant_about_email(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(pattern in lowered for pattern in _LEAD_EMAIL_HESITATION)


def _user_signing_off(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(pattern in lowered for pattern in _LEAD_SIGN_OFF_PATTERNS)


def _update_lead_capture_metadata(state: SessionState, user_message: str) -> bool:
    metadata = state.metadata
    dirty = False
    waiting = metadata.get("lead_waiting_for")
    lowered = (user_message or "").strip().lower()

    if state.name and waiting == "name":
        metadata.pop("lead_waiting_for", None)
        metadata.pop("lead_name_attempts", None)
        dirty = True
    elif waiting == "name" and _user_refused_name(lowered):
        metadata["lead_name_refused"] = True
        metadata["lead_name_refusal_ack_needed"] = True
        metadata["lead_preferred_name"] = "friend"
        metadata.pop("lead_waiting_for", None)
        dirty = True

    if state.email and waiting == "email":
        metadata.pop("lead_waiting_for", None)
        metadata.pop("lead_email_attempts", None)
        dirty = True
    elif waiting == "email":
        if _user_refused_email(lowered):
            metadata["lead_email_refused"] = True
            metadata["lead_email_refusal_ack_needed"] = True
            metadata.pop("lead_waiting_for", None)
            dirty = True
        elif _user_hesitant_about_email(lowered):
            metadata["lead_email_reassure_needed"] = True
            dirty = True

    if (state.phone or state.phone_opt_out) and waiting == "phone":
        metadata.pop("lead_waiting_for", None)
        metadata.pop("lead_phone_attempts", None)
        dirty = True

    if state.phone_opt_out and not metadata.get("lead_phone_refused"):
        metadata["lead_phone_refused"] = True
        metadata["lead_phone_refusal_ack_needed"] = True
        dirty = True

    if _lead_capture_stage(state) == "complete" and metadata.get("lead_waiting_for"):
        metadata.pop("lead_waiting_for", None)
        dirty = True

    return dirty


def _build_name_prompt() -> str:
    return "Thanks for replying! May I have your name so I can address you properly?"


def _build_email_prompt(state: SessionState, *, reminder: bool = False, reassurance: bool = False) -> str:
    salutation = _preferred_lead_name(state)
    prefix = "Thank you" if not reminder else "Just a quick reminder — thank you"
    base = f"{prefix}, {salutation}. What's the best email to reach you at so I can make sure you don't miss anything important if the chat disconnects?"
    if reassurance:
        base += "\n\nThis helps me send you a quick summary in case the connection drops."
    return base


def _build_phone_prompt(reminder: bool = False) -> str:
    if reminder:
        return "Just checking again — if you're comfortable, may I have your phone number for quick updates or follow-up?"
    return "If you're comfortable, may I also have your phone number for quick updates or follow-up?"


def _build_email_fail_safe_prompt() -> str:
    return "Before we wrap up, would you like me to send a quick summary and next steps to your email?"


def _prepare_lead_capture_prompts(
    state: SessionState,
    *,
    first_reply: bool,
    user_signing_off: bool,
) -> tuple[str, bool]:
    metadata = state.metadata
    prompts: List[str] = []
    dirty = False

    if metadata.get("lead_name_refusal_ack_needed"):
        prompts.append("No worries at all — I'll just call you 'friend.'")
        metadata["lead_name_refusal_ack_needed"] = False
        metadata.setdefault("lead_preferred_name", "friend")
        dirty = True
    if metadata.get("lead_email_refusal_ack_needed"):
        prompts.append("No problem — we can continue here in the chat.")
        metadata["lead_email_refusal_ack_needed"] = False
        dirty = True
    if metadata.get("lead_phone_refusal_ack_needed"):
        prompts.append("No problem at all — we'll continue by chat and email.")
        metadata["lead_phone_refusal_ack_needed"] = False
        dirty = True

    stage = _lead_capture_stage(state)

    if stage == "complete":
        metadata.pop("lead_waiting_for", None)
        return "\n\n".join(prompts), dirty

    if stage == "name":
        attempts = metadata.get("lead_name_attempts", 0)
        if attempts < 2 and (first_reply or metadata.get("lead_waiting_for") == "name" or attempts == 0):
            prompts.append(_build_name_prompt())
            metadata["lead_waiting_for"] = "name"
            metadata["lead_name_attempts"] = attempts + 1
            dirty = True
        return "\n\n".join(prompts), dirty

    if stage == "email":
        reassurance_needed = metadata.pop("lead_email_reassure_needed", False)
        attempts = metadata.get("lead_email_attempts", 0)
        reminder = metadata.get("lead_waiting_for") == "email" and attempts > 0
        prompts.append(_build_email_prompt(state, reminder=reminder, reassurance=reassurance_needed))
        metadata["lead_waiting_for"] = "email"
        metadata["lead_email_attempts"] = attempts + 1
        dirty = True
        if (
            user_signing_off
            and not state.email
            and not metadata.get("lead_email_refused")
            and not metadata.get("lead_email_fail_safe_sent")
        ):
            prompts.append(_build_email_fail_safe_prompt())
            metadata["lead_email_fail_safe_sent"] = True
            dirty = True
        return "\n\n".join(prompts), dirty

    if stage == "phone":
        attempts = metadata.get("lead_phone_attempts", 0)
        reminder = metadata.get("lead_waiting_for") == "phone" and attempts > 0
        prompts.append(_build_phone_prompt(reminder=reminder))
        metadata["lead_waiting_for"] = "phone"
        metadata["lead_phone_attempts"] = attempts + 1
        dirty = True
        return "\n\n".join(prompts), dirty

    return "\n\n".join(prompts), dirty
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
    otp_email: Optional[str] = None
    otp_status: str = "not_required"
    otp_code_hash: Optional[str] = None
    otp_attempts: int = 0
    otp_expires_at: Optional[datetime] = None
    otp_last_sent_at: Optional[datetime] = None
    otp_failure_reason: Optional[str] = None
    otp_verified_at: Optional[datetime] = None
    otp_consent_status: str = "not_requested"
    otp_consent_prompted_at: Optional[datetime] = None

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
        otp_email=model.otp_email,
        otp_status=model.otp_status or "not_required",
        otp_code_hash=model.otp_code_hash,
        otp_attempts=model.otp_attempts or 0,
        otp_expires_at=model.otp_expires_at,
        otp_last_sent_at=model.otp_last_sent_at,
        otp_failure_reason=model.otp_failure_reason,
        otp_verified_at=model.otp_verified_at,
        otp_consent_status=model.otp_consent_status or "not_requested",
        otp_consent_prompted_at=model.otp_consent_prompted_at,
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
    model.otp_email = state.otp_email
    model.otp_status = state.otp_status
    model.otp_code_hash = state.otp_code_hash
    model.otp_attempts = state.otp_attempts
    model.otp_expires_at = state.otp_expires_at
    model.otp_last_sent_at = state.otp_last_sent_at
    model.otp_failure_reason = state.otp_failure_reason
    model.otp_verified_at = state.otp_verified_at
    model.otp_consent_status = state.otp_consent_status
    model.otp_consent_prompted_at = state.otp_consent_prompted_at


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


@dataclass
class OTPResult:
    allow_history: bool
    should_halt: bool = False
    bot_reply: Optional[str] = None
    state_dirty: bool = False
    status: str = "not_required"
    just_verified: bool = False


@dataclass
class HistoryConsentResult:
    should_halt: bool = False
    bot_reply: Optional[str] = None
    state_dirty: bool = False
    trigger_otp_send: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reset_otp_state(state: SessionState, *, reset_consent: bool = True) -> None:
    state.otp_email = None
    state.otp_status = "not_required"
    state.otp_code_hash = None
    state.otp_attempts = 0
    state.otp_expires_at = None
    state.otp_last_sent_at = None
    state.otp_failure_reason = None
    state.otp_verified_at = None
    if reset_consent:
        state.otp_consent_status = "not_requested"
        state.otp_consent_prompted_at = None
    state.metadata.pop("otp_failure_announced", None)


def _hash_otp_code(code: str) -> str:
    secret = _settings.secret_key.get_secret_value()
    return hashlib.sha256(f"{code}:{secret}".encode("utf-8")).hexdigest()


def _generate_otp_code(length: int = _OTP_CODE_LENGTH) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(length))


def _extract_otp_from_text(text: str) -> Optional[str]:
    digits = re.sub(r"\D", "", text or "")
    return digits if len(digits) == _OTP_CODE_LENGTH else None


def _history_email(conversation: Conversation, state: SessionState) -> Optional[str]:
    return conversation.visitor_email or state.email


def _interpret_history_consent(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return "unknown"
    for token in _HISTORY_NO_TOKENS:
        if token in lowered:
            return "no"
    for phrase in _HISTORY_REQUEST_PATTERNS:
        if phrase in lowered:
            return "yes"
    for token in _HISTORY_YES_TOKENS:
        if token in lowered:
            return "yes"
    return "unknown"


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


def _handle_history_consent(
    session_state: SessionState,
    conversation: Conversation,
    *,
    has_prior_sessions: bool,
    user_message: str,
) -> HistoryConsentResult:
    result = HistoryConsentResult()
    email = _history_email(conversation, session_state)
    status = session_state.otp_consent_status or "not_requested"
    tracked_email = session_state.metadata.get("otp_history_email")

    if email and tracked_email != email:
        session_state.metadata["otp_history_email"] = email
        if status != "not_requested":
            _reset_otp_state(session_state, reset_consent=False)
            session_state.otp_consent_status = "not_requested"
            session_state.otp_consent_prompted_at = None
            status = "not_requested"
            result.state_dirty = True

    if not email or not has_prior_sessions:
        if status != "not_requested":
            session_state.otp_consent_status = "not_requested"
            session_state.otp_consent_prompted_at = None
            result.state_dirty = True
        return result

    if status == "declined":
        decision = _interpret_history_consent(user_message)
        if decision == "yes":
            session_state.otp_consent_status = "pending"
            session_state.otp_consent_prompted_at = _now()
            result.state_dirty = True
            result.bot_reply = (
                "Sure thing. Do you want me to pull in your previous conversation, or would you prefer to start fresh today?\n\n"
                "If you’d like me to reference it, I’ll send a quick one-time verification code to that email."
            )
            result.should_halt = True
            return result
        return result

    if status == "not_requested":
        session_state.otp_consent_status = "pending"
        session_state.otp_consent_prompted_at = _now()
        result.state_dirty = True
        result.bot_reply = (
            "It looks like you’ve reached out to us before using the email we have on file. "
            "Do you want me to pull in your previous conversation, or would you prefer to start fresh today?\n\n"
            "Just a heads-up: if you’d like me to reference the previous chat, I’ll send a one-time verification code (OTP) "
            "to that email to confirm it’s really you."
        )
        result.should_halt = True
        return result

    if status == "pending":
        decision = _interpret_history_consent(user_message)
        if decision == "yes":
            session_state.otp_consent_status = "granted"
            session_state.otp_consent_prompted_at = None
            result.state_dirty = True
            result.bot_reply = (
                "Great, I’ll send a one-time verification code to that email now. Once you enter it here, "
                "I can pull up your previous conversation so we don’t have to start from scratch."
            )
            result.should_halt = True
            result.trigger_otp_send = True
            return result
        if decision == "no":
            session_state.otp_consent_status = "declined"
            session_state.otp_consent_prompted_at = None
            _reset_otp_state(session_state, reset_consent=False)
            result.state_dirty = True
            result.bot_reply = (
                "Got it, we’ll start fresh. Let’s focus on what you need right now. What would you like to work on?"
            )
            result.should_halt = True
            return result
        result.bot_reply = (
            "No problem—just let me know if you’d like me to pull in your previous conversation or start fresh today."
        )
        result.should_halt = True
        return result

    return result


def _has_prior_sessions(db: Session, project_id: int, conversation: Conversation) -> bool:
    email = conversation.visitor_email
    if not email:
        return False
    match = (
        db.query(Conversation.id)
        .filter(
            Conversation.project_id == project_id,
            Conversation.id != conversation.id,
            Conversation.visitor_email == email,
        )
        .limit(1)
        .first()
    )
    return match is not None


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


def _create_and_send_otp(
    project: Project,
    conversation: Conversation,
    session_state: SessionState,
    *,
    email: str,
    now: datetime,
) -> bool:
    code = _generate_otp_code()
    email_subject = f"{project.name} chat verification code"
    greeting = conversation.visitor_name or session_state.name or "there"
    email_body = (
        f"Hi {greeting},\n\n"
        "Use this one-time code to verify your identity so we can securely share your previous chat history:\n\n"
        f"{code}\n\n"
        f"This code expires in {_OTP_TTL_MINUTES} minutes."
    )
    if not send_email(email_subject, email_body, to_list=[email]):
        session_state.otp_status = "send_failed"
        session_state.otp_failure_reason = "smtp_error"
        session_state.metadata.pop("otp_failure_announced", None)
        return False
    session_state.otp_email = email
    session_state.otp_status = "pending"
    session_state.otp_code_hash = _hash_otp_code(code)
    session_state.otp_attempts = 0
    session_state.otp_expires_at = now + timedelta(minutes=_OTP_TTL_MINUTES)
    session_state.otp_last_sent_at = now
    session_state.otp_failure_reason = None
    session_state.metadata.pop("otp_failure_announced", None)
    return True


def _shorten_snippet(text: str, max_len: int = 150) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + "..."


def _previous_chat_summary(
    db: Session,
    project_id: int,
    conversation: Conversation,
) -> list[str]:
    email = conversation.visitor_email
    if not email:
        return []
    prev = (
        db.query(Conversation)
        .filter(
            Conversation.project_id == project_id,
            Conversation.id != conversation.id,
            Conversation.visitor_email == email,
        )
        .order_by(Conversation.updated_at.desc())
        .first()
    )
    if not prev:
        return []
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == prev.id)
        .order_by(Message.created_at.asc())
        .limit(50)
        .all()
    )
    summary_points: list[str] = []
    if prev.updated_at:
        summary_points.append(
            f"Date noted: {prev.updated_at.strftime('%b %d, %Y')} (local time)."
        )
    if not messages:
        summary_points.append("General check-in about your goals and next steps.")
        return summary_points
    user_msgs = [m.content for m in messages if m.role == MessageRole.USER and m.content]
    assistant_msgs = [m.content for m in messages if m.role == MessageRole.ASSISTANT and m.content]
    if user_msgs:
        opener = _shorten_snippet(user_msgs[0], 110)
        summary_points.append(f"Visitor opened with: {opener}.")
        if len(user_msgs) > 1:
            recent = _shorten_snippet(user_msgs[-1], 110)
            summary_points.append(f"Most recent visitor update: {recent}.")
    if assistant_msgs:
        closing = _shorten_snippet(assistant_msgs[-1], 120)
        summary_points.append(f"Assistant guidance: {closing}.")
    if not summary_points:
        summary_points.append("Conversation captured general project requirements and next steps.")
    return summary_points


def _build_history_success_message(summary_points: Optional[list[str]]) -> str:
    points = summary_points or ["We briefly connected but didn’t go into much detail at that time."]
    bullets = "\n".join(f"- {point}" for point in points)
    return (
        "You’re successfully verified—thank you.\n\n"
        "Here’s a quick summary of our previous conversation:\n"
        f"{bullets}\n\n"
        "Would you like to continue from where we left off?"
    )


def _handle_otp_gate(
    project: Project,
    conversation: Conversation,
    session_state: SessionState,
    *,
    has_prior_sessions: bool,
    user_message: str,
) -> OTPResult:
    email = conversation.visitor_email or session_state.email
    requires_otp = bool(email) and has_prior_sessions
    state_dirty = False
    now = _now()

    if not requires_otp:
        if session_state.otp_status != "not_required" or session_state.otp_email:
            _reset_otp_state(session_state)
            state_dirty = True
        return OTPResult(True, state_dirty=state_dirty, status="not_required")

    if session_state.otp_status == "verified":
        # Already verified during this conversation; no need to re-request.
        return OTPResult(True, state_dirty=state_dirty, status="verified")

    if session_state.otp_consent_status != "granted":
        return OTPResult(False, state_dirty=state_dirty, status="consent_not_granted")

    if email and session_state.otp_email and session_state.otp_email != email:
        _reset_otp_state(session_state, reset_consent=False)
        state_dirty = True

    def fail(reason: str) -> OTPResult:
        _reset_otp_state(session_state, reset_consent=False)
        session_state.otp_consent_status = "declined"
        session_state.otp_consent_prompted_at = None
        session_state.otp_failure_reason = reason
        return OTPResult(
            False,
            True,
            _HISTORY_FAILURE_TEXT,
            state_dirty=True,
            status=reason,
        )

    if session_state.otp_code_hash and session_state.otp_expires_at and session_state.otp_expires_at <= now:
        return fail("expired")

    otp_code = _extract_otp_from_text(user_message)
    if otp_code and session_state.otp_code_hash:
        hashed = _hash_otp_code(otp_code)
        if hashed == session_state.otp_code_hash:
            session_state.otp_status = "verified"
            session_state.otp_verified_at = now
            session_state.otp_code_hash = None
            session_state.otp_attempts = 0
            session_state.otp_failure_reason = None
            state_dirty = True
            return OTPResult(True, True, None, state_dirty=state_dirty, status="verified", just_verified=True)
        session_state.otp_attempts += 1
        state_dirty = True
        if session_state.otp_attempts >= _OTP_MAX_ATTEMPTS:
            return fail("max_attempts")
        reminder = (
            f"That code didn’t match what we sent to {email}. Please double-check and enter the 6-digit code."
            if email
            else "That code didn’t match. Please re-enter the 6-digit verification code."
        )
        return OTPResult(False, True, reminder, state_dirty=state_dirty, status="pending")

    if not session_state.otp_code_hash:
        if not email:
            return fail("missing_email")
        if not _create_and_send_otp(project, conversation, session_state, email=email, now=now):
            return fail("send_failed")
        state_dirty = True
        prompt = (
            f"For your privacy, I emailed a 6-digit code to {email}. "
            "Please type it here so I can pull up our previous chats."
        )
        return OTPResult(False, True, prompt, state_dirty=state_dirty, status="pending")

    reminder = (
        f"I still need the 6-digit code we sent to {email}. Please enter it here so I can load our previous chats."
        if email
        else "I still need the 6-digit verification code we sent."
    )
    return OTPResult(False, True, reminder, state_dirty=state_dirty, status="pending")


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


def _exclusive_sales_instruction(project: Project) -> Optional[str]:
    return (
        f"You represent {project.name}. Never mention, recommend, or compare with any other provider, community, or competitor. "
        f"If someone asks about alternatives, politely explain you can only discuss {project.name}'s services and keep the conversation focused on them."
    )


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
    user_signing_off_flag = _user_signing_off(user_message)
    conversation, is_new = _get_or_create_conversation(db, project, session_id)
    has_prior_messages = (
        db.query(Message.id)
        .filter(Message.conversation_id == conversation.id)
        .limit(1)
        .first()
        is not None
    )

    first_user_reply = not has_prior_messages

    cache_key = f"{project.id}:{normalized_question}"
    cached_answer = cache.get(cache_key) if not has_prior_messages else None
    lead_prompt_text = ""

    bot_config: BotConfig = project.bot_config or BotConfig(system_prompt="You are a helpful assistant.")
    system_prompt = bot_config.system_prompt or "You are a helpful assistant."
    extra_instructions = []
    if bot_config.additional_instructions:
        extra_instructions.append(bot_config.additional_instructions)
    if learning_instruction := _learning_tone_instruction(project):
        extra_instructions.append(learning_instruction)
    if exclusive_instruction := _exclusive_sales_instruction(project):
        extra_instructions.append(exclusive_instruction)
    extra_instructions.append(
        "When you share any URL, show the full link in plain text (e.g., https://example.com) and do not wrap it in markdown link syntax."
    )
    extra_instructions.append(
        "Sound like a real human teammate: vary greetings, use natural contractions or casual phrases when appropriate (e.g., 'lemme' for 'let me'), and avoid repeating the exact same sentence if the visitor repeats themselves."
    )
    extra_instructions.append(
        "Avoid markdown styling (no **bold**, _italics_, or similar). Present service names and lists as clear plain text so they render cleanly in the chat widget."
    )
    extra_instructions.append(
        "Lead capture prompts (name, email, phone) are injected automatically. Do not ask for that contact information yourself—focus on helping once you've acknowledged anything the visitor already shared."
    )
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
    state_dirty |= _update_lead_capture_metadata(session_state, user_message)

    _save_message(db, conversation, MessageRole.USER, user_message)

    if state_dirty:
        _save_session_state(db, state_model, session_state)

    has_prior_sessions = _has_prior_sessions(db, project.id, conversation)
    consent_result = _handle_history_consent(
        session_state,
        conversation,
        has_prior_sessions=has_prior_sessions,
        user_message=user_message,
    )
    otp_send_state_dirty = False
    if consent_result.trigger_otp_send:
        email_for_otp = _history_email(conversation, session_state)
        if email_for_otp and _create_and_send_otp(
            project,
            conversation,
            session_state,
            email=email_for_otp,
            now=_now(),
        ):
            otp_send_state_dirty = True
        else:
            _reset_otp_state(session_state, reset_consent=False)
            session_state.otp_consent_status = "declined"
            session_state.otp_consent_prompted_at = None
            consent_result.bot_reply = _HISTORY_FAILURE_TEXT
            consent_result.should_halt = True
            consent_result.trigger_otp_send = False
            otp_send_state_dirty = True

    if consent_result.state_dirty or otp_send_state_dirty:
        _save_session_state(db, state_model, session_state)

    if consent_result.should_halt and consent_result.bot_reply:
        reply = consent_result.bot_reply
        _apply_typing_delay(reply)
        yield reply
        _save_message(db, conversation, MessageRole.ASSISTANT, reply)
        emit_integration_events(
            db,
            project,
            conversation,
            IntegrationEvent.BOT_REPLY,
            reply,
            page_url,
        )
        return

    if (
        session_state.otp_consent_status == "granted"
        and not _extract_otp_from_text(user_message)
        and _interpret_history_consent(user_message) == "no"
    ):
        session_state.otp_consent_status = "declined"
        session_state.otp_consent_prompted_at = None
        _reset_otp_state(session_state, reset_consent=False)
        _save_session_state(db, state_model, session_state)
        reply = _HISTORY_FAILURE_TEXT
        _apply_typing_delay(reply)
        yield reply
        _save_message(db, conversation, MessageRole.ASSISTANT, reply)
        emit_integration_events(
            db,
            project,
            conversation,
            IntegrationEvent.BOT_REPLY,
            reply,
            page_url,
        )
        return

    otp_history_allowed = False
    if session_state.otp_consent_status == "granted":
        otp_result = _handle_otp_gate(
            project,
            conversation,
            session_state,
            has_prior_sessions=has_prior_sessions,
            user_message=user_message,
        )
        otp_history_allowed = otp_result.allow_history
        if otp_result.state_dirty:
            _save_session_state(db, state_model, session_state)
        if otp_result.just_verified:
            success_summary = _previous_chat_summary(db, project.id, conversation)
            success_reply = _build_history_success_message(success_summary)
            _apply_typing_delay(success_reply)
            yield success_reply
            _save_message(db, conversation, MessageRole.ASSISTANT, success_reply)
            emit_integration_events(
                db,
                project,
                conversation,
                IntegrationEvent.BOT_REPLY,
                success_reply,
                page_url,
            )
            return
        if otp_result.should_halt:
            reply = otp_result.bot_reply or "Please use the 6-digit code we emailed to continue."
            _apply_typing_delay(reply)
            yield reply
            _save_message(db, conversation, MessageRole.ASSISTANT, reply)
            emit_integration_events(
                db,
                project,
                conversation,
                IntegrationEvent.BOT_REPLY,
                reply,
                page_url,
            )
            return


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

    lead_prompt, lead_dirty = _prepare_lead_capture_prompts(
        session_state,
        first_reply=first_user_reply,
        user_signing_off=user_signing_off_flag,
    )
    if lead_prompt:
        lead_prompt_text = lead_prompt
    if lead_dirty:
        _save_session_state(db, state_model, session_state)

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

    previous_sessions: List[Dict[str, str]] = []
    if otp_history_allowed:
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
        final_text: List[str] = []
        preface_output = lead_prompt_text.strip()
        preface_present = bool(preface_output)
        if preface_present:
            spacer = "" if preface_output.endswith("\n") else "\n\n"
            preface_chunk = preface_output + spacer
            _apply_typing_delay(preface_chunk)
            yield preface_chunk
            final_text.append(preface_chunk)

        use_cached = bool(cached_answer) and not preface_present
        if use_cached:
            logger.info(
                "Cache hit for project %s question '%s'", project.id, normalized_question
            )
            _apply_typing_delay(cached_answer)
            yield cached_answer
            final_text.append(cached_answer)
            answer = "".join(final_text)
        else:
            stream = _client.chat.completions.create(
                model=_settings.default_model,
                messages=messages,
                max_tokens=bot_config.max_tokens,
                temperature=bot_config.temperature,
                stream=True,
            )
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
        final_text.append(fallback)
        combined_fallback = "".join(final_text)
        _save_message(db, conversation, MessageRole.ASSISTANT, combined_fallback)
        if _record_last_question_type(session_state, combined_fallback):
            _save_session_state(db, state_model, session_state)
        emit_integration_events(
            db, project, conversation, IntegrationEvent.BOT_REPLY, combined_fallback, page_url
        )
