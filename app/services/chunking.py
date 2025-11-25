import re
from typing import List

_SENTENCE_RE = re.compile(r"(?<=[.!?]) +")


def _split_unit(text: str, max_chars: int) -> List[str]:
    """Break a long sentence/token into <= max_chars pieces."""

    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= max_chars:
        return [stripped]

    words = stripped.split()
    # If there is no whitespace, fall back to fixed-size slicing.
    if len(words) <= 1:
        return [stripped[i : i + max_chars] for i in range(0, len(stripped), max_chars)]

    parts: List[str] = []
    current: List[str] = []
    current_len = 0
    for word in words:
        word_len = len(word)
        if word_len > max_chars:
            if current:
                parts.append(" ".join(current))
                current = []
                current_len = 0
            parts.extend(word[i : i + max_chars] for i in range(0, word_len, max_chars))
            continue
        added = word_len if not current else word_len + 1
        if current and current_len + added > max_chars:
            parts.append(" ".join(current))
            current = [word]
            current_len = word_len
        else:
            current.append(word)
            current_len += added
    if current:
        parts.append(" ".join(current))
    return parts


def split_into_chunks(text: str, max_chars: int = 3500) -> List[str]:
    """Rudimentary chunker splitting on sentences while respecting max length."""

    sentences = _SENTENCE_RE.split(text.strip())
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for sentence in sentences:
        for fragment in _split_unit(sentence, max_chars):
            if current_len + len(fragment) > max_chars and current:
                chunks.append(" ".join(current))
                current = []
                current_len = 0
            current.append(fragment)
            current_len += len(fragment) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks or [text]
