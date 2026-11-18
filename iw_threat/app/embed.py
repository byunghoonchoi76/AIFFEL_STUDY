"""임베딩 (multilingual-e5-large-instruct, CPU). 질의에는 instruct 접두어, 문서에는 접두어 없음."""
from __future__ import annotations

from functools import lru_cache

from .config import get_settings

QUERY_TASK = "주어진 질의와 관련된 군사 교리·첩보 문단을 검색"


@lru_cache
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(get_settings().embed_model, device="cpu")


def embed_passages(texts: list[str]) -> list[list[float]]:
    return _model().encode(texts, normalize_embeddings=True, batch_size=16).tolist()


def embed_query(q: str) -> list[float]:
    return _model().encode([f"Instruct: {QUERY_TASK}\nQuery: {q}"], normalize_embeddings=True)[0].tolist()
