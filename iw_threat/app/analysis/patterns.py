"""과거 사례 패턴 유사도와 지휘관 3줄 보고 요약 (순수 함수, LLM 미사용 — 수치 변조 방지)."""
from __future__ import annotations

import math
from pathlib import Path

import yaml


def load_cases(path: str | Path = "config/reference_cases.yaml") -> list[dict]:
    p = Path(path)
    return yaml.safe_load(p.read_text(encoding="utf-8")).get("cases", []) if p.exists() else []


def indicator_strengths(hypotheses: list[dict]) -> dict[str, float]:
    """확률을 높인 징후의 강도 (가설 간 최댓값)."""
    out: dict[str, float] = {}
    for h in hypotheses:
        for c in h.get("contributions", []):
            if c["delta_logit"] > 0:
                out[c["indicator_id"]] = max(out.get(c["indicator_id"], 0.0), c["strength"])
    return out


def case_similarity(hypotheses: list[dict], cases: list[dict]) -> list[dict]:
    cur = indicator_strengths(hypotheses)
    out = []
    for cs in cases:
        w = cs["weights"]
        keys = set(w) | set(cur)
        dot = sum(w.get(k, 0) * cur.get(k, 0) for k in keys)
        na = math.sqrt(sum(v * v for v in w.values()))
        nb = math.sqrt(sum(cur.get(k, 0) ** 2 for k in w))        # 사례에 정의된 징후 축만 비교
        sim = dot / (na * nb) if na and nb else 0.0
        cover = sum(1 for k in w if cur.get(k, 0) > 0) / len(w)
        out.append({"id": cs["id"], "name": cs["name"], "hypothesis": cs["hypothesis"],
                    "similarity": round(sim * cover, 3), "matched": sorted(k for k in w if cur.get(k, 0) > 0),
                    "missing": sorted(k for k in w if cur.get(k, 0) == 0), "summary": cs.get("summary", "")})
    return sorted(out, key=lambda x: -x["similarity"])


def commander_briefing(res: dict) -> list[str]:
    """지휘관 보고용 3줄: ① 결론 ② 핵심 근거·유사 사례 ③ 정보요구·수집 조치. 수치는 계산값 그대로."""
    top = max(res["hypotheses"], key=lambda h: h["threat"])
    pos = sorted((c for c in top["contributions"] if c["delta_logit"] > 0), key=lambda c: -c["delta_logit"])[:3]
    l1 = f"① {top['name']} 가능성 {top['probability']:.0%}, 위협 등급 {res['grade']}, 분석 신뢰도 {res['confidence']}."
    sim = (res.get("patterns") or [{}])[0]
    l2 = "② 핵심 근거: " + (", ".join(c["name"] for c in pos) or "식별 징후 없음")
    if sim.get("similarity", 0) >= 0.3:
        l2 += f" — 과거 사례 '{sim['name']}'와 유사도 {sim['similarity']:.0%}"
    l2 += "."
    cr = res.get("commander_requirements") or {}
    alerts = cr.get("ccir_alerts", [])
    l3 = "③ " + ("CCIR 보고 조건 충족: " + "; ".join(f"{a['commander']} {a['id']}" for a in alerts) if alerts else "CCIR 보고 조건 미충족")
    lim = (res.get("weather") or {}).get("collection_limits") or []
    l3 += (", " + lim[0].split("→")[-1].strip() if lim else "") + "."
    return [l1, l2, l3]
