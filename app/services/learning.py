import logging
from statistics import mean
from typing import Dict, Iterable

from sqlalchemy.orm import Session

from ..models import Conversation, Message, Project

logger = logging.getLogger(__name__)


ToneKeywords = {
    "professional": ["regards", "per our", "schedule"],
    "friendly": ["hi", "hello", "glad"],
    "playful": ["haha", "lol", "😊", "😉"],
}


def _infer_tone(messages: Iterable[Message]) -> str:
    scores: Dict[str, int] = {"friendly": 0, "professional": 0, "playful": 0}
    for message in messages:
        if message.role.value != "USER":
            continue
        content = (message.content or "").lower()
        for tone, keywords in ToneKeywords.items():
            if any(keyword in content for keyword in keywords):
                scores[tone] += 1
    return max(scores, key=scores.get)


def _humor_score(messages: Iterable[Message]) -> float:
    indicators = ["lol", "haha", "😂", "😊", "😉"]
    hits = 0
    total = 0
    for message in messages:
        if message.role.value != "USER":
            continue
        total += 1
        content = message.content.lower()
        if any(token in content for token in indicators):
            hits += 1
    return hits / total if total else 0.0


def _emoji_ratio(messages: Iterable[Message]) -> float:
    emojis = 0
    total_chars = 0
    for message in messages:
        content = message.content
        emojis += sum(1 for ch in content if ch in {"😊", "😂", "😉", "🙂", "😄"})
        total_chars += len(content)
    if total_chars == 0:
        return 0.0
    return emojis / total_chars


def update_learning_stats(db: Session, project: Project, conversation: Conversation) -> None:
    if not project.learning_enabled:
        return
    history = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    if not history:
        return
    stats = project.learning_stats or {}
    tone = _infer_tone(history)
    humor = _humor_score(history)
    emoji_ratio = _emoji_ratio(history)
    stats.setdefault("tone_samples", []).append(tone)
    stats.setdefault("humor_samples", []).append(humor)
    stats.setdefault("emoji_samples", []).append(emoji_ratio)

    samples_limit = 200
    stats["tone_samples"] = stats["tone_samples"][-samples_limit:]
    stats["humor_samples"] = stats["humor_samples"][-samples_limit:]
    stats["emoji_samples"] = stats["emoji_samples"][-samples_limit:]

    stats["dominant_tone"] = max(set(stats["tone_samples"]), key=stats["tone_samples"].count)
    stats["humor_level"] = mean(stats["humor_samples"])
    stats["emoji_usage"] = mean(stats["emoji_samples"])

    project.learning_stats = stats
    db.add(project)
    db.commit()
