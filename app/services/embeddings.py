from typing import Iterable, List

from openai import OpenAI

from ..config import get_settings

_settings = get_settings()
_client = OpenAI(api_key=_settings.openai_api_key.get_secret_value())


def embed_texts(texts: Iterable[str]) -> List[List[float]]:
    """Call OpenAI embeddings API and return dense vectors."""

    response = _client.embeddings.create(
        input=list(texts), model=_settings.embedding_model
    )
    return [item.embedding for item in response.data]
