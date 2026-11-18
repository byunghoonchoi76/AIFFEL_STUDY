"""근거 기반 해설: LLM이 쓴 문장 중 허용된 근거(청크 ID)를 인용한 문장만 남김."""
from __future__ import annotations

from .scoring import AssessmentResult


def explanation_prompt(result: AssessmentResult, doctrine: list[dict], evidence_quotes: list[dict]) -> tuple[str, str]:
    """doctrine/evidence_quotes: [{"id": 근거ID, "text": 내용}]"""
    top = result.top
    contrib = "\n".join(
        f"- {c.name}(LR {c.lr}, 충족강도 {c.strength}, 출처 {'/'.join(c.source_types)})"
        for c in top.contributions
    ) or "- 충족된 지표 없음"
    refs = "\n".join(f"[{d['id']}] {d['text']}" for d in doctrine + evidence_quotes)
    system = (
        "너는 군 정보 분석 보고서 초안 작성 보조자다. 계산된 위협도 결과를 바꾸지 말고 근거를 설명한다.\n"
        "규칙: 모든 문장은 아래 근거 목록의 ID를 citations에 1개 이상 포함해야 한다. 근거에 없는 내용은 쓰지 않는다. "
        "최종 판단은 분석관이 한다는 전제로 단정적 표현을 피한다."
    )
    user = (
        f"평가 결과: 가설 '{top.name}', 확률 {top.probability}, 위협 점수 {result.threat_score}, "
        f"등급 {result.grade}, 분석 신뢰도 {result.confidence}\n충족 지표:\n{contrib}\n\n근거 목록:\n{refs}\n\n"
        "JSON으로 답하라: {\"sentences\": [{\"text\": str, \"citations\": [str]}]}"
    )
    return system, user


def verify_sentences(sentences: list[dict], allowed_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """(통과 문장, 제거 문장). 인용이 없거나 허용 목록 밖 ID를 인용하면 제거."""
    kept, dropped = [], []
    for s in sentences:
        cites = s.get("citations") or []
        text = (s.get("text") or "").strip()
        if text and cites and all(c in allowed_ids for c in cites):
            kept.append({"text": text, "citations": cites})
        else:
            dropped.append(s)
    return kept, dropped
