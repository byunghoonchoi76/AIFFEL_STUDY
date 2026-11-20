"""관측(영상 변화·텍스트 첩보) → 징후 지표 매칭."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

from .catalog import Catalog, Grading
from .change import Change
from ..taxonomy import match_keys
from .scoring import Evidence


def equip_ko(code: str) -> str:
    from ..taxonomy import name_ko
    return name_ko(code)


def _fmt(n: float) -> str:
    return f"{n:g}"


def _imagery_quote(ch: Change) -> str:
    q = (f"영상 판독 {equip_ko(ch.equipment_type)} 기준기간 평균 {_fmt(ch.baseline_level)}대 → "
         f"최근 {ch.recent_level}대 (+{_fmt(ch.delta)}대)")
    if ch.unit_key:
        base = "평시 대비 " + (f"{ch.ratio}배" if ch.ratio else "신규 출현")
        q += f" [식별 부대: {ch.unit_name}({ch.echelon or '제대 미상'}) {ch.dist_km}km, {base}]"
    return q


def match_imagery(changes: list[Change], catalog: Catalog, grading: Grading,
                  source_key: str = "IMINT:default") -> list[Evidence]:
    """규칙 매칭: 장비 분류가 지표에 속하고 증가량이 임계값 이상이면 충족."""
    rel, cred = grading.default_grade("IMINT")
    out: list[Evidence] = []
    for ind in catalog.indicator_defs().values():
        if not ind.equipment:
            continue
        for ch in changes:
            if not (match_keys(ch.equipment_type) & set(ind.equipment)):
                continue
            th = catalog.threshold(ind, ch.echelon if ch.unit_key else None)
            by_delta = ch.delta >= th.min_delta
            by_ratio = (th.min_ratio is not None and ch.ratio is not None and ch.ratio >= th.min_ratio
                        and ch.delta >= max(1, math.ceil(th.min_delta / 2)))
            # 판독 오차·평시 변동을 넘는 증가인지 (대규모 부대에서 수량 오차가 임계값보다 커지는 오탐 방지)
            significant = catalog.min_zscore is None or ch.zscore is None or ch.zscore >= catalog.min_zscore
            strong = (by_delta or by_ratio) and significant
            # 약한 신호: 임계값의 일정 비율 이상 + 평시 변동폭을 낮은 기준으로 넘는 증가 → 가중치를 낮춰 반영
            wk = catalog.weak
            weak = (not strong and wk and ch.delta >= max(1, math.ceil(th.min_delta * wk.get("fraction", 0.5)))
                    and (ch.zscore is None or ch.zscore >= wk.get("zscore", 2.0))
                    and len(ch.refs) >= wk.get("min_obs", 2))       # 약한 신호는 최근 2회 이상 관측돼야 (일회성 잡음 배제)
            if strong or weak:
                out.append(Evidence(
                    indicator_id=ind.id, source_type="IMINT", source_key=source_key,
                    reliability=rel, credibility=cred, observed_at=ch.latest_at,
                    ref=f"targets:{ch.equipment_type}:{','.join(map(str, ch.refs))}",
                    quote=_imagery_quote(ch) + f" (기준 {th.basis}{', 약한 신호' if weak else ''})",
                    scale=1.0 if strong else float(wk.get("scale", 0.5)), unit_key=ch.unit_key,
                ))
    return out


def sync_evidence(evidence: list[Evidence], catalog: Catalog, grading: Grading,
                  names: dict[str, str] | None = None) -> list[Evidence]:
    """동시성 지표: 같은 지표(약한 신호 포함)가 여러 부대에서 함께 식별되면 별도 근거로 추가.
    예) 보병사단 3개가 같은 시기에 야간 분산·위장 시작 → '다수 부대 동시 은밀 전개'."""
    rel, cred = grading.default_grade("IMINT")
    out = []
    for ind in catalog.indicator_defs().values():
        if not ind.sync_of:
            continue
        evs = [e for e in evidence if e.indicator_id == ind.sync_of and e.unit_key]
        units = sorted({e.unit_key for e in evs})
        if len(units) < max(ind.min_units, 2):
            continue
        label = ", ".join((names or {}).get(u, u) for u in units[:4])
        out.append(Evidence(
            indicator_id=ind.id, source_type="IMINT", source_key="IMINT:sync", reliability=rel, credibility=cred,
            observed_at=max(e.observed_at for e in evs), ref="sync:" + ",".join(units),
            quote=f"{len(units)}개 부대 동시 식별: {label}",
            scale=min(1.0, len(units) / (ind.min_units + 1)),
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


def _overlap(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return any(a[i:i + 6] in b for i in range(0, max(len(a) - 5, 1)))   # 6자 이상 연속 일치 = 같은 사실을 가리킴


def hits_to_evidence(chunk: ReportChunk, hits: list[dict], catalog: Catalog) -> list[Evidence]:
    """LLM 분류 결과 {indicator_id, quote} 검증 후 Evidence로 변환.
    같은 구절이 반대 징후로도 분류됐으면 그 반대 징후가 겨냥하는 긍정 지표 분류는 버림
    (예: '전차 상당수가 모형' → 기갑 집결이 아니라 모형 장비 식별)."""
    valid_ids = set(catalog.indicator_defs())
    counters = catalog.counters()
    valid = [h for h in hits if h.get("indicator_id") in valid_ids and quote_in_text(h.get("quote", ""), chunk.text)]
    counter_hits = [h for h in valid if h["indicator_id"] in counters]
    out = []
    for h in valid:
        iid, quote = h["indicator_id"], h["quote"]
        if iid not in counters and any(iid in counters[c["indicator_id"]].discounts and _overlap(quote, c["quote"])
                                       for c in counter_hits):
            continue
        out.append(Evidence(
            indicator_id=iid, source_type=chunk.source_type, source_key=chunk.source_key,
            reliability=chunk.reliability, credibility=chunk.credibility,
            observed_at=chunk.reported_at, ref=f"chunk:{chunk.chunk_id}", quote=_norm(quote),
        ))
    return out


def classification_prompt(chunk: ReportChunk, catalog: Catalog) -> tuple[str, str]:
    inds = "\n".join(
        f"- {i.id}: {i.name}" + (f" (관련: {', '.join(equip_ko(e) for e in i.equipment)})" if i.equipment else "")
        for i in catalog.indicator_defs().values()
    )
    system = (
        "너는 군 정보 분석 보조자다. 주어진 첩보 원문의 문장마다 아래 징후 지표에 해당하는지 판단한다.\n"
        "규칙:\n"
        "1. 원문의 각 문장을 빠짐없이 검토하고, 해당하는 지표를 모두 찾는다(한 원문에서 여러 개 가능).\n"
        "2. 원문에 명시된 사실만 사용한다. 추정·확대 해석 금지.\n"
        "3. quote는 원문에서 그대로 복사한 연속 구절이어야 한다(글자 하나도 바꾸지 않음).\n"
        "4. 부정·반대 사실(모형·가짜 장비, 보급 없음, 통신 평시 수준, 복귀·종료 등)은 C_로 시작하는 반대 징후로 분류하고, "
        "같은 구절을 긍정 징후로 분류하지 않는다. 예) '전차 상당수가 모형' → C_DECOY (I_ARMOR_MASS 아님)\n"
        "5. 해당 지표가 없으면 hits를 빈 배열로 둔다.\n"
        f"징후 지표 목록:\n{inds}"
    )
    user = f"첩보 원문:\n\"\"\"\n{chunk.text}\n\"\"\"\n\nJSON으로 답하라: {{\"hits\": [{{\"indicator_id\": str, \"quote\": str}}]}}"
    return system, user
