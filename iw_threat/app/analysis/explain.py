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
        blocks.append(f"- indicator_id={c.indicator_id} '{c.name}': {_strength_word(c.strength)} 확인됨, "
                      f"출처 {'/'.join(c.source_types)}\n  근거:\n{refs}")
    doc = "\n".join(f"{d['id']}: {d['text']}" for d in doctrine) or "(교리 근거 없음)"
    system = (
        "너는 군 정보 분석 보고서 초안 작성 보조자다. 반드시 한국어 개조식 문장으로 쓴다.\n"
        "규칙:\n"
        "1. 계산된 등급·확률을 바꾸거나 새로 판단하지 않는다.\n"
        "2. 지표 하나당 한 문장. indicator_id는 별도 필드에만 쓰고 text에는 넣지 않는다.\n"
        "3. text에는 인용한 근거의 구체적 사실(장비 종류, 수량 변화, 출처 유형)을 반드시 포함한다.\n"
        "   예) '영상 판독상 야전병원이 0대에서 2대로 늘었고 현지 보도도 천막 3동 설치를 전함 — 의무지원 준비 정황'\n"
        "   지표 문장 끝에 가설 결론('공격 준비 가능성 있음')을 반복하지 말고, 그 징후가 뜻하는 바만 짧게 쓴다.\n"
        "4. citations에는 그 지표 아래 나열된 근거 ID(E숫자) 또는 교리 ID(D숫자)만 그대로 넣는다.\n"
        "5. 근거에 없는 수치·사실은 쓰지 않는다. '충족', 'LR', '강도' 같은 내부 계산 용어는 쓰지 않는다.\n"
        "6. 최종 판단은 분석관이 하므로 '~로 판단됨', '~가능성 있음' 등 추정 표현을 쓴다.\n"
        "7. summary는 전체 종합 판단 1문장: 출처 유형 수, 핵심 징후 2~3개, 가설과 확률(주어진 값 그대로)을 담고 "
        "citations에 핵심 징후의 근거 ID를 넣는다."
    )
    user = (
        f"평가 결과: 가설 '{top.name}', 확률 {top.probability:.0%}, 등급 {result.grade}, 분석 신뢰도 {result.confidence}\n\n"
        "확인된 징후와 근거:\n" + "\n".join(blocks) + f"\n\n교리 근거:\n{doc}\n\n"
        'JSON으로 답하라: {"summary": {"text": "...", "citations": ["E1"]}, '
        '"sentences": [{"indicator_id": "I_...", "text": "...", "citations": ["E1"]}]}'
    )
    return system, user


def _norm_id(c) -> str:
    m = re.search(r"[ED]\d+", str(c).upper())
    return m.group(0) if m else str(c).strip()


_TOKEN_RE = re.compile(r"[가-힣A-Za-z]{2,}|\d+(?:\.\d+)?")
_JARGON = ("충족", "LR", "강도")


def _content_tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text))


def reflects_evidence(text: str, cited_texts: list[str], indicator_name: str = "") -> bool:
    """문장이 인용한 근거의 내용을 실제로 담고 있는지 검사.
    지표 이름·상투어를 뺀 단어/수치가 근거와 겹쳐야 함. 한국어 조사 차이를 고려해 부분 문자열 포함도 일치로 봄."""
    if any(j in text for j in _JARGON):
        return False
    stop = {"영상", "판독", "기준기간", "평균", "최근", "IMINT", "OSINT", "HUMINT", "SIGINT", "가능성", "판단됨"}
    name_toks = _content_tokens(indicator_name)
    ev = {t for c in cited_texts for t in _content_tokens(c)} - stop
    for t in _content_tokens(text) - stop:
        if any(t in n or n in t for n in name_toks if len(n) >= 2):
            continue                                   # 지표 이름 되풀이는 근거 반영으로 치지 않음
        if t.isdigit() or t.replace(".", "", 1).isdigit():
            if t in ev:
                return True
        elif any(t in e or e in t for e in ev if len(e) >= 2):
            return True
    return False


def verify_sentences(sentences: list[dict], ev_map: dict[str, list[dict]],
                     doctrine: list[dict], names: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """(통과, 제거). 통과 조건: 알려진 지표 + 인용 1개 이상 + 모든 인용이 그 지표 근거 또는 교리
    + 문장이 인용 근거의 구체적 내용을 담음(지표 이름만 되풀이한 상투 문장 제거).
    통과 문장의 citations는 원 참조(ref)로 변환."""
    doc_refs = {d["id"]: d["ref"] for d in doctrine}
    texts = {d["id"]: d["text"] for d in doctrine} | {d["id"]: d["text"] for v in ev_map.values() for d in v}
    kept, dropped = [], []
    for s in sentences:
        iid = str(s.get("indicator_id") or "").strip()
        cites = [_norm_id(c) for c in (s.get("citations") or [])]
        text = re.sub(r"^\s*I_[A-Z_]+\s*[:：-]\s*", "", (s.get("text") or "").strip())
        allowed = {d["id"]: d["ref"] for d in ev_map.get(iid, [])} | doc_refs
        reason = None
        if not text:
            reason = "빈 문장"
        elif iid not in ev_map:
            reason = f"알 수 없는 지표 {iid!r}"
        elif not cites:
            reason = "인용 없음"
        elif not all(c in allowed for c in cites):
            reason = f"허용되지 않은 인용 {[c for c in cites if c not in allowed]}"
        elif not reflects_evidence(text, [texts[c] for c in cites], (names or {}).get(iid, "")):
            reason = "내부 용어 사용" if any(j in text for j in _JARGON) else "근거 내용 미반영"
        if reason is None:
            kept.append({"indicator_id": iid, "text": text, "citations": [allowed[c] for c in cites],
                         "generated": "llm"})
        else:
            dropped.append({**s, "_reason": reason})
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


def verify_summary(summary: dict | None, result: AssessmentResult, ev_map: dict[str, list[dict]],
                   doctrine: list[dict]) -> dict | None:
    """종합 판단 검증: 인용이 실제 근거·교리 ID이고, 근거 내용을 담고, 문장 속 백분율이 계산값과 같아야 통과."""
    if not summary or not (summary.get("text") or "").strip():
        return None
    text = summary["text"].strip()
    allowed = {d["id"]: d for v in ev_map.values() for d in v} | {d["id"]: d for d in doctrine}
    cites = [_norm_id(c) for c in (summary.get("citations") or [])]
    if not cites or not all(c in allowed for c in cites):
        return None
    if not reflects_evidence(text, [allowed[c]["text"] for c in cites]):
        return None
    pct = round(result.top.probability * 100)
    for m in re.findall(r"(\d+(?:\.\d+)?)\s*%", text):
        if abs(float(m) - pct) > 1:                     # 확률을 바꿔 쓴 경우 차단
            return None
    return {"text": text, "citations": [allowed[c]["ref"] for c in cites], "generated": "llm"}


def template_summary(result: AssessmentResult) -> dict:
    """종합 판단 정형 문장 (LLM 요약이 검증을 통과하지 못했을 때)."""
    top = result.top
    types = sorted({t for c in top.contributions for t in c.source_types})
    key = [c.name for c in top.contributions[:3]]
    if not key:
        text = f"충족된 징후가 없어 '{top.name}' 가능성은 사전 수준({top.probability:.0%})으로 판단됨."
    else:
        text = (f"{len(types)}개 출처 유형({'/'.join(types)})에서 {', '.join(key)} 등 {len(top.contributions)}개 징후가 "
                f"식별되어 '{top.name}' 가능성 {top.probability:.0%}(등급 {result.grade}, 분석 신뢰도 {result.confidence})로 판단됨.")
    refs = [r for c in top.contributions[:3] for r in c.refs]
    return {"text": text, "citations": refs, "generated": "template"}
