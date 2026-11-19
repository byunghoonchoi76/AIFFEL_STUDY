"""근거 기반 해설.

문장마다 다루는 지표(indicator_id)를 밝히게 하고, 그 지표에 실제로 연결된 근거(또는 교리)만 인용할 수 있게 검증.
→ 인용 ID가 존재하더라도 다른 지표의 근거를 끌어다 쓴 문장(근거-주장 불일치)은 제거.
"""
from __future__ import annotations

from .scoring import AssessmentResult, Evidence

STRENGTH_WORDS = [(0.7, "강하게"), (0.4, "상당히"), (0.0, "약하게")]


def _strength_word(s: float) -> str:
    return next(w for t, w in STRENGTH_WORDS if s >= t)


def build_evidence_map(result: AssessmentResult, evidence: list[Evidence]) -> dict[str, list[dict]]:
    """최상위 가설의 충족 지표별 근거 목록 {indicator_id: [{"id", "text"}]}."""
    ids = {c.indicator_id for c in result.top.contributions}
    out: dict[str, list[dict]] = {i: [] for i in ids}
    for e in evidence:
        if e.indicator_id in ids and e.quote:
            out[e.indicator_id].append({"id": e.ref, "text": f"[{e.source_type} {e.reliability}{e.credibility}] {e.quote}"})
    return out


def explanation_prompt(result: AssessmentResult, ev_map: dict[str, list[dict]], doctrine: list[dict]) -> tuple[str, str]:
    top = result.top
    blocks = []
    for c in top.contributions:
        refs = "\n".join(f"    [{d['id']}] {d['text']}" for d in ev_map.get(c.indicator_id, []))
        blocks.append(f"- 지표 {c.indicator_id} '{c.name}': {_strength_word(c.strength)} 충족, "
                      f"출처 {'/'.join(c.source_types)}\n  근거:\n{refs}")
    doc = "\n".join(f"[{d['id']}] {d['text']}" for d in doctrine) or "(교리 근거 없음)"
    system = (
        "너는 군 정보 분석 보고서 초안 작성 보조자다. 반드시 한국어 개조식 문장으로 쓴다.\n"
        "규칙:\n"
        "1. 계산된 등급·확률을 바꾸거나 새로 판단하지 않는다.\n"
        "2. 지표 하나당 한 문장. 각 문장에 indicator_id를 적고, citations에는 그 지표의 근거 ID 또는 교리 ID만 넣는다.\n"
        "3. 근거에 없는 수치·사실은 쓰지 않는다. LR, 충족강도 같은 내부 계산 용어는 쓰지 않는다.\n"
        "4. 최종 판단은 분석관이 하므로 '~로 판단됨', '~가능성 있음' 등 추정 표현을 쓴다."
    )
    user = (
        f"평가 결과: 가설 '{top.name}', 확률 {top.probability:.0%}, 등급 {result.grade}, 분석 신뢰도 {result.confidence}\n\n"
        f"충족 지표와 근거:\n" + "\n".join(blocks) + f"\n\n교리 근거:\n{doc}\n\n"
        'JSON으로 답하라: {"sentences": [{"indicator_id": str, "text": str, "citations": [str]}]}'
    )
    return system, user


def verify_sentences(sentences: list[dict], ev_map: dict[str, list[dict]], doctrine_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """(통과, 제거). 통과 조건: 알려진 지표 + 인용 1개 이상 + 모든 인용이 그 지표 근거 또는 교리."""
    kept, dropped = [], []
    for s in sentences:
        iid = s.get("indicator_id")
        cites = s.get("citations") or []
        text = (s.get("text") or "").strip()
        allowed = {d["id"] for d in ev_map.get(iid, [])} | doctrine_ids
        if text and iid in ev_map and cites and all(c in allowed for c in cites):
            kept.append({"indicator_id": iid, "text": text, "citations": cites})
        else:
            dropped.append(s)
    return kept, dropped
