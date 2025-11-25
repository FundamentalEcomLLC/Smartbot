from typing import Dict, Iterable

from sqlalchemy.orm import Session

from ..models import Chunk, Document
from .chunking import split_into_chunks
from .embeddings import embed_texts


def _chunk_metadata(document: Document) -> Dict:
    document_meta = document.metadata_json or {}
    metadata = dict(document_meta)
    metadata.setdefault("url", document_meta.get("url") if document_meta else document.url_or_name)
    metadata.setdefault("title", metadata.get("title") or document.url_or_name)
    return metadata


def index_document_chunks(db: Session, project_id: int, document: Document) -> None:
    """Split a document into chunks, create embeddings, and store Chunk rows."""

    db.flush()  # ensure document.id exists
    chunks = split_into_chunks(document.raw_content)
    if not chunks:
        return
    embeddings = embed_texts(chunks)
    metadata = _chunk_metadata(document)
    for chunk_text, embedding in zip(chunks, embeddings):
        db.add(
            Chunk(
                project_id=project_id,
                document_id=document.id,
                content=chunk_text,
                embedding=embedding,
                metadata_json=metadata,
            )
        )


def reembed_project_documents(db: Session, project_id: int) -> None:
    """Rebuild embeddings for every document in a project."""

    db.query(Chunk).filter(Chunk.project_id == project_id).delete()
    documents: Iterable[Document] = (
        db.query(Document).filter(Document.project_id == project_id).order_by(Document.id.asc())
    )
    for document in documents:
        index_document_chunks(db, project_id, document)
    db.commit()
