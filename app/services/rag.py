from math import sqrt
from typing import Iterable, List, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Chunk, CustomQA


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0

    dot = sum(l * r for l, r in zip(left, right))
    norm_left = sqrt(sum(l * l for l in left))
    norm_right = sqrt(sum(r * r for r in right))
    denom = norm_left * norm_right
    if denom == 0:
        return 0.0
    return dot / denom


def fetch_relevant_chunks(db: Session, project_id: int, embedding: Sequence[float], limit: int = 8) -> List[Chunk]:
    candidate_limit = max(limit * 20, 200)
    stmt = (
        select(Chunk)
        .where(Chunk.project_id == project_id)
        .order_by(Chunk.updated_at.desc())
        .limit(candidate_limit)
    )
    candidates = list(db.scalars(stmt))
    scored = sorted(
        (
            (_cosine_similarity(chunk.embedding or [], embedding), chunk)
            for chunk in candidates
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    return [chunk for _, chunk in scored[:limit]]


def rank_custom_qas(qas: Iterable[CustomQA], query: str) -> List[CustomQA]:
    terms = [term for term in query.lower().split() if term]
    if not terms:
        return list(qas)

    def score(entry: CustomQA) -> float:
        haystack = f"{entry.question} {entry.answer}".lower()
        return sum(haystack.count(term) for term in terms)

    return sorted(qas, key=score, reverse=True)


def fetch_custom_qas(db: Session, project_id: int, query: str, limit: int = 3) -> List[CustomQA]:
    stmt = (
        select(CustomQA)
        .where(CustomQA.project_id == project_id)
        .order_by(CustomQA.created_at.desc())
        .limit(limit)
    )
    qas = list(db.scalars(stmt))
    ranked = rank_custom_qas(qas, query)
    return ranked[:limit]
