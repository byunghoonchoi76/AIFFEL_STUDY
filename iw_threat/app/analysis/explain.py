"""근거 기반 해설.

- 근거마다 짧은 ID(E1, E2… / 교리 D1, D2…)를 부여해 LLM이 인용 ID를 변형하지 않게 함
- 문장마다 다루는 지표(indicator_id)를 밝히고, 그 지표의 근거 또는 교리만 인용 가능 (근거-주장 불일치 차단)
- 검증을 통과한 문장이 없는 지표는 계산 결과로 만든 정형 문장으로 대체 (해설이 비지 않도록)
"""
from __future__ import annotations

import re

from .scoring import AssessmentResult, Evidence

STRENGTH_WORDS = [(0.7, "강하게"), (0.4, "다수"), (0.0, "약하게")]


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
    """사실(수량·출처) 문장은 코드가 정형으로 만들고, LLM은 ① 종합 판단 ② 지표별 의미(해석)만 작성."""
    top = result.top
    types = sorted({t for c in top.contributions for t in c.source_types})
    blocks = []
    for c in top.contributions:
        refs = "\n".join(f"    {d['id']}: {d['text']}" for d in ev_map.get(c.indicator_id, []))
        blocks.append(f"- indicator_id={c.indicator_id} '{c.name}' (출처 {'/'.join(c.source_types)})\n  근거:\n{refs}")
    doc = "\n".join(f"{d['id']}: {d['text']}" for d in doctrine) or "(교리 근거 없음)"
    system = (
        "너는 군 정보 분석 보고서 초안 작성 보조자다. 반드시 한국어로 쓴다.\n"
        "규칙:\n"
        "1. 계산된 등급·확률을 바꾸거나 새로 판단하지 않는다. 근거에 없는 사실은 쓰지 않는다.\n"
        "2. implications: 지표마다 그 징후가 군사적으로 뜻하는 바를 30자 이내 구절로 쓴다. 숫자는 쓰지 않는다.\n"
        "   예) '의무지원 준비 정황', '하천 도하 공격 대비 가능성', '공격 제대 화력 보강 정황'\n"
        "3. summary: 전체 종합 판단 1문장. 출처 유형 수(주어진 값 그대로), 핵심 징후 2~3개를 장비·사실로 구체적으로, "
        "가설과 확률(주어진 값 그대로)을 담고 citations에 핵심 징후의 근거 ID(E숫자)를 넣는다.\n"
        "   예) '영상·현지 보도 2개 출처 유형에서 전차 집결, 도하장비 전방 이동, 야전병원 설치가 동시에 식별되어 공격 준비 가능성 87%로 판단됨'\n"
        "4. '충족', 'LR', '강도' 같은 내부 계산 용어는 쓰지 않는다. 추정 표현('~로 판단됨', '~정황')을 쓴다."
    )
    user = (
        f"평가 결과: 가설 '{top.name}', 확률 {top.probability:.0%}, 등급 {result.grade}, 분석 신뢰도 {result.confidence}, "
        f"출처 유형 {len(types)}개({'/'.join(types)})\n\n"
        "식별된 징후와 근거:\n" + "\n".join(blocks) + f"\n\n교리 근거:\n{doc}\n\n"
        'JSON으로 답하라: {"summary": {"text": "...", "citations": ["E1"]}, '
        '"implications": [{"indicator_id": "I_...", "text": "..."}]}'
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


_IMINT_RE = re.compile(r"영상 판독 (\S+) 기준기간 평균 ([\d.]+)대 → 최근 (\d+)대")


def _compact(ev_text: str) -> str:
    """근거 문구를 짧은 사실로: '영상: 전차 13→40대' / 'OSINT(C3): “…”'."""
    m = _IMINT_RE.search(ev_text)
    if m:
        return f"영상: {m.group(1)} {m.group(2)}→{m.group(3)}대"
    g = re.match(r"\[(\w+) (\w)(\d)\]\s*(.*)", ev_text)
    if g:
        body = g.group(4)
        return f"{g.group(1)}({g.group(2)}{g.group(3)}): “{body[:45]}{'…' if len(body) > 45 else ''}”"
    return ev_text


def verify_implications(items: list[dict] | None, ev_map: dict[str, list[dict]]) -> tuple[dict[str, str], list[dict]]:
    """지표별 의미 구절 검증: 알려진 지표, 30자 내외, 숫자·내부 용어 없음 (사실은 정형 문장이 담당)."""
    ok, dropped = {}, []
    for it in items or []:
        iid = str(it.get("indicator_id") or "").strip()
        text = re.sub(r"^\s*I_[A-Z_]+\s*[:：-]\s*", "", (it.get("text") or "").strip()).rstrip(".")
        reason = None
        if iid not in ev_map:
            reason = f"알 수 없는 지표 {iid!r}"
        elif not text or len(text) > 40:
            reason = "길이 초과 또는 빈 구절"
        elif re.search(r"\d", text):
            reason = "숫자 포함(사실은 정형 문장에서만)"
        elif any(j in text for j in _JARGON):
            reason = "내부 용어 사용"
        if reason:
            dropped.append({**it, "_reason": reason})
        elif iid not in ok:
            ok[iid] = text
    return ok, dropped


def indicator_lines(result: AssessmentResult, ev_map: dict[str, list[dict]], implications: dict[str, str]) -> list[dict]:
    """지표별 해설: 사실은 코드가 근거에서 정형으로(수치 변조 불가), 의미는 검증된 LLM 구절로."""
    out = []
    for c in result.top.contributions:
        ev = ev_map.get(c.indicator_id, [])
        if not ev:
            continue
        facts = "; ".join(_compact(d["text"]) for d in ev)
        meaning = implications.get(c.indicator_id)
        out.append({
            "indicator_id": c.indicator_id,
            "text": f"{c.name} 징후 {_strength_word(c.strength)} 식별됨 — {facts}" + (f" → {meaning}" if meaning else ""),
            "citations": [d["ref"] for d in ev], "generated": "template+llm" if meaning else "template",
        })
    return out


def _word_overlap(text: str, cited_texts: list[str]) -> int:
    """숫자를 뺀 단어(조사 차이 허용) 중 근거와 겹치는 개수."""
    ev = {t for c in cited_texts for t in _content_tokens(c) if not t.replace(".", "", 1).isdigit() and len(t) >= 2}
    toks = {t for t in _content_tokens(text) if not t.replace(".", "", 1).isdigit() and len(t) >= 2}
    toks -= {"출처", "유형", "징후", "핵심", "가설", "확률", "공격", "준비", "가능성", "판단됨", "식별됨"}
    return sum(1 for t in toks if any(t in e or e in t for e in ev))


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
    if _word_overlap(text, [allowed[c]["text"] for c in cites]) < 2:   # 핵심 징후를 구체적으로 2개 이상 언급해야 함
        return None
    n_types = len({t for c in result.top.contributions for t in c.source_types})
    for m in re.findall(r"(\d+)\s*개\s*(?:출처|정보원)", text):
        if int(m) != n_types:                           # 출처 수를 틀리게 쓴 경우 차단
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
