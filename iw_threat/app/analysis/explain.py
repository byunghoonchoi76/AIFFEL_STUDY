"""근거 기반 해설.

- 근거마다 짧은 ID(E1, E2… / 교리 D1, D2…)를 부여해 LLM이 인용 ID를 변형하지 않게 함
- 문장마다 다루는 지표(indicator_id)를 밝히고, 그 지표의 근거 또는 교리만 인용 가능 (근거-주장 불일치 차단)
- 검증을 통과한 문장이 없는 지표는 계산 결과로 만든 정형 문장으로 대체 (해설이 비지 않도록)
"""
from __future__ import annotations

import re

from .scoring import AssessmentResult, Evidence

STRENGTH_WORDS = [(0.7, "강하게"), (0.4, "상당히"), (0.0, "약하게")]


def _strength_word(s: float) -> str:
    return next(w for t, w in STRENGTH_WORDS if s >= t)


def build_evidence_map(result: AssessmentResult, evidence: list[Evidence]) -> dict[str, list[dict]]:
    """최상위 가설의 충족 지표별 근거 {indicator_id: [{"id": "E1", "ref": 원 참조, "text"}]}."""
    ids = [c.indicator_id for c in result.top.contributions]
    out: dict[str, list[dict]] = {i: [] for i in ids}
    n = 0
    for e in evidence:
        if e.indicator_id in out and e.quote:
            n += 1
            out[e.indicator_id].append({"id": f"E{n}", "ref": e.ref,
                                        "text": f"[{e.source_type} {e.reliability}{e.credibility}] {e.quote}"})
    return out


def label_doctrine(docs: list[dict]) -> list[dict]:
    """[{"ref", "text"}] → D1, D2… 부여."""
    return [{"id": f"D{i}", "ref": d["ref"], "text": d["text"]} for i, d in enumerate(docs, 1)]


def explanation_prompt(result: AssessmentResult, ev_map: dict[str, list[dict]], doctrine: list[dict]) -> tuple[str, str]:
    top = result.top
    blocks = []
    for c in top.contributions:
        refs = "\n".join(f"    {d['id']}: {d['text']}" for d in ev_map.get(c.indicator_id, []))
        blocks.append(f"- indicator_id={c.indicator_id} '{c.name}': {_strength_word(c.strength)} 충족, "
                      f"출처 {'/'.join(c.source_types)}\n  근거:\n{refs}")
    doc = "\n".join(f"{d['id']}: {d['text']}" for d in doctrine) or "(교리 근거 없음)"
    system = (
        "너는 군 정보 분석 보고서 초안 작성 보조자다. 반드시 한국어 개조식 문장으로 쓴다.\n"
        "규칙:\n"
        "1. 계산된 등급·확률을 바꾸거나 새로 판단하지 않는다.\n"
        "2. 지표 하나당 한 문장. indicator_id는 주어진 값을 그대로 쓴다.\n"
        "3. citations에는 그 지표 아래 나열된 근거 ID(E숫자) 또는 교리 ID(D숫자)만 그대로 넣는다.\n"
        "4. 근거에 없는 수치·사실은 쓰지 않는다. LR, 충족강도 같은 내부 계산 용어는 쓰지 않는다.\n"
        "5. 최종 판단은 분석관이 하므로 '~로 판단됨', '~가능성 있음' 등 추정 표현을 쓴다."
    )
    user = (
        f"평가 결과: 가설 '{top.name}', 확률 {top.probability:.0%}, 등급 {result.grade}, 분석 신뢰도 {result.confidence}\n\n"
        "충족 지표와 근거:\n" + "\n".join(blocks) + f"\n\n교리 근거:\n{doc}\n\n"
        'JSON으로 답하라: {"sentences": [{"indicator_id": "I_...", "text": "...", "citations": ["E1"]}]}'
    )
    return system, user


def _norm_id(c) -> str:
    m = re.search(r"[ED]\d+", str(c).upper())
    return m.group(0) if m else str(c).strip()


def verify_sentences(sentences: list[dict], ev_map: dict[str, list[dict]],
                     doctrine: list[dict]) -> tuple[list[dict], list[dict]]:
    """(통과, 제거). 통과 조건: 알려진 지표 + 인용 1개 이상 + 모든 인용이 그 지표 근거 또는 교리.
    통과 문장의 citations는 원 참조(ref)로 변환."""
    doc_refs = {d["id"]: d["ref"] for d in doctrine}
    kept, dropped = [], []
    for s in sentences:
        iid = str(s.get("indicator_id") or "").strip()
        cites = [_norm_id(c) for c in (s.get("citations") or [])]
        text = (s.get("text") or "").strip()
        allowed = {d["id"]: d["ref"] for d in ev_map.get(iid, [])} | doc_refs
        if text and iid in ev_map and cites and all(c in allowed for c in cites):
            kept.append({"indicator_id": iid, "text": text, "citations": [allowed[c] for c in cites],
                         "generated": "llm"})
        else:
            dropped.append(s)
    return kept, dropped


def template_sentences(result: AssessmentResult, ev_map: dict[str, list[dict]], covered: set[str]) -> list[dict]:
    """LLM 문장이 검증을 통과하지 못한 지표에 대해 계산 결과 기반 정형 문장 생성."""
    out = []
    for c in result.top.contributions:
        if c.indicator_id in covered or not ev_map.get(c.indicator_id):
            continue
        ev = ev_map[c.indicator_id]
        out.append({
            "indicator_id": c.indicator_id,
            "text": f"{c.name} 징후가 {_strength_word(c.strength)} 확인됨 ({'/'.join(c.source_types)}: {ev[0]['text']})",
            "citations": [d["ref"] for d in ev], "generated": "template",
        })
    return out
