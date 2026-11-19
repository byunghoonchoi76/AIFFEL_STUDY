"""Qdrant 벡터 저장소 — 청크 payload에 출처 등급·지역·보고일시를 함께 저장."""
from __future__ import annotations

import uuid
from datetime import datetime

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from ..config import get_settings


class VectorStore:
    def __init__(self):
        s = get_settings()
        self.client = QdrantClient(url=s.qdrant_url)
        self.col = s.qdrant_collection
        if not self.client.collection_exists(self.col):
            self.client.create_collection(
                self.col, vectors_config=qm.VectorParams(size=s.embed_dim, distance=qm.Distance.COSINE),
            )
            for key, schema in [("source_type", "keyword"), ("area_id", "keyword"), ("reported_ts", "float")]:
                self.client.create_payload_index(self.col, key, field_schema=schema)

    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        points = [
            qm.PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, ch["chunk_id"])), vector=v, payload=ch)
            for ch, v in zip(chunks, vectors)
        ]
        self.client.upsert(self.col, points=points)

    def search(self, vector: list[float], k: int, source_types: list[str] | None = None,
               area_id: str | None = None, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        must = []
        if source_types:
            must.append(qm.FieldCondition(key="source_type", match=qm.MatchAny(any=source_types)))
        if area_id:
            must.append(qm.FieldCondition(key="area_id", match=qm.MatchValue(value=area_id)))
        if since or until:
            must.append(qm.FieldCondition(key="reported_ts", range=qm.Range(
                gt=since.timestamp() if since else None, lte=until.timestamp() if until else None)))
        res = self.client.query_points(self.col, query=vector, limit=k,
                                       query_filter=qm.Filter(must=must) if must else None)
        return [{**p.payload, "score": p.score} for p in res.points]

    def delete_area(self, area_id: str) -> bool:
        self.client.delete(self.col, points_selector=qm.FilterSelector(filter=qm.Filter(
            must=[qm.FieldCondition(key="area_id", match=qm.MatchValue(value=area_id))])))
        return True
