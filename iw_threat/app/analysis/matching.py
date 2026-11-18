"""관측(영상 변화·텍스트 첩보) → 징후 지표 매칭."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .catalog import Catalog, Grading
from .change import Change
from .scoring import Evidence


def match_imagery(changes: list[Change], catalog: Catalog, grading: Grading,
                  source_key: str = "IMINT:default") -> list[Evidence]:
    """규칙 매칭: 장비 분류가 지표에 속하고 증가량이 임계값 이상이면 충족."""
    rel, cred = grading.default_grade("IMINT")
    out: list[Evidence] = []
    for ind in catalog.indicator_defs().values():
        if not ind.equipment:
            continue
        for ch in changes:
            if ch.equipment_type in ind.equipment and ch.delta >= ind.min_delta:
                out.append(Evidence(
                    indicator_id=ind.id, source_type="IMINT", source_key=source_key,
                    reliability=rel, credibility=cred, observed_at=ch.latest_at,
                    ref=f"targets:{ch.equipment_type}:{','.join(map(str, ch.refs))}",
                    quote=f"{ch.equipment_type} 기준 {ch.baseline_level} → 최근 {ch.recent_level} (Δ{ch.delta})",
                ))
    return out


@dataclass
class ReportChunk:
    chunk_id: str
    text: str
    source_type: str
    source_key: str
    reliability: str
    credibility: int
    reported_at: datetime


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """LLM이 제시한 인용문이 원문에 실제로 있는지(공백 정규화 후 부분 문자열) 검사 — 환각 차단."""
    q = _norm(quote)
    return len(q) >= 4 and q in _norm(text)


def hits_to_evidence(chunk: ReportChunk, hits: list[dict], catalog: Catalog) -> list[Evidence]:
    """LLM 분류 결과 {indicator_id, quote} 검증 후 Evidence로 변환."""
    valid_ids = set(catalog.indicator_defs())
    out = []
    for h in hits:
        iid, quote = h.get("indicator_id"), h.get("quote", "")
        if iid not in valid_ids or not quote_in_text(quote, chunk.text):
            continue
        out.append(Evidence(
            indicator_id=iid, source_type=chunk.source_type, source_key=chunk.source_key,
            reliability=chunk.reliability, credibility=chunk.credibility,
            observed_at=chunk.reported_at, ref=f"chunk:{chunk.chunk_id}", quote=_norm(quote),
        ))
    return out


def classification_prompt(chunk: ReportChunk, catalog: Catalog) -> tuple[str, str]:
    inds = "\n".join(f"- {i.id}: {i.name}" for i in catalog.indicator_defs().values())
    system = (
        "너는 군 정보 분석 보조자다. 주어진 첩보 문장이 아래 징후 지표에 해당하는지 판단한다.\n"
        "규칙: 원문에 명시된 사실만 사용한다. quote는 원문에서 그대로 복사한 연속 구절이어야 한다. "
        "해당 지표가 없으면 hits를 빈 배열로 둔다.\n"
        f"징후 지표 목록:\n{inds}"
    )
    user = f"첩보 원문:\n\"\"\"\n{chunk.text}\n\"\"\"\n\nJSON으로 답하라: {{\"hits\": [{{\"indicator_id\": str, \"quote\": str}}]}}"
    return system, user
