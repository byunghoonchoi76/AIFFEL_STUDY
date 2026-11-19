"""문서 수집: 파싱 → 청크 → 임베딩 → Qdrant, (선택) LLM 개체·관계 추출 → Neo4j."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..embed import embed_passages
from ..stores import pg
from .chunk import chunk_text
from .parse import parse_file

EXTRACT_SYSTEM = (
    "너는 군사 문서에서 개체와 관계를 추출한다. 원문에 있는 것만 추출한다.\n"
    "label은 Unit(부대), Equipment(장비), Location(지역), Activity(활동) 중 하나.\n"
    'JSON: {"entities":[{"name":str,"label":str}], "relations":[{"head":str,"rel":str,"tail":str}]}'
)


def ingest_document(path: str, *, title: str, source_name: str, source_type: str, reliability: str,
                    credibility: int, area_id: str | None, reported_at: datetime | None,
                    classification: str = "UNCLASSIFIED", vector=None, graph=None, llm=None) -> dict:
    text = parse_file(path)
    source_id = pg.upsert_source(source_name, source_type, reliability)
    doc_id = pg.insert_document(source_id, title, str(path), credibility, area_id, reported_at, classification)

    pieces = chunk_text(text)
    chunks = [{
        "chunk_id": f"d{doc_id}:{i}", "doc_id": doc_id, "title": title, "text": t,
        "source_type": source_type, "source_key": f"src{source_id}",
        "reliability": reliability, "credibility": credibility, "area_id": area_id,
        "reported_at": reported_at.isoformat() if reported_at else None,
        "reported_ts": reported_at.timestamp() if reported_at else None,
        "classification": classification, "file": Path(path).name,
    } for i, t in enumerate(pieces)]

    if vector is not None and chunks:
        vector.upsert(chunks, embed_passages([c["text"] for c in chunks]))

    extracted = 0
    if graph is not None and llm is not None:
        for c in chunks:
            try:
                out = llm.chat_json(EXTRACT_SYSTEM, c["text"])
                graph.save_extraction(f"d{doc_id}", c["chunk_id"], out.get("entities", []), out.get("relations", []))
                extracted += 1
            except Exception:  # noqa: BLE001 — 추출 실패는 수집을 막지 않음
                continue

    pg.audit("system", "ingest", {"doc_id": doc_id, "chunks": len(chunks), "extracted": extracted, "file": str(path)})
    return {"doc_id": doc_id, "chunks": len(chunks), "extracted_chunks": extracted}
