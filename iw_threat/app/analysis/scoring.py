"""위협도 산정 엔진 — 결정적(deterministic) 계산. LLM은 관여하지 않음.

1) 첩보 가중치 w = 출처신뢰도 × 정보신빙성 × exp(-경과일/τ)
2) 지표 충족 강도: 같은 출처는 최댓값만(중복 보고 배제), 출처 유형 간에는 독립 확인으로 결합(noisy-OR)
3) 가설 확률: logit(P) = logit(prior) + Σ 강도 × ln(LR)
4) 위협 점수 = P × 영향도, 등급은 위협징후목록의 grades 구간
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .catalog import Catalog, Grading, Hypothesis

SOURCE_TYPES = ("IMINT", "HUMINT", "SIGINT", "OSINT", "DOCTRINE")


@dataclass(frozen=True)
class Evidence:
    indicator_id: str
    source_type: str
    source_key: str          # 같은 출처 판별용(출처 ID 등)
    reliability: str         # A~F
    credibility: int         # 1~6
    observed_at: datetime
    ref: str                 # 근거 참조(target_event id, chunk id 등)
    quote: str | None = None
    scale: float = 1.0       # 약한 신호 등 가중 배율 (0~1)
    unit_key: str | None = None   # 영상 근거의 식별 부대 (동시성 판단용)
    gap_days: float = 0.0          # 이 근거 이후 해당 지역 영상 관측이 없었던 기간(일) — 관측 공백 보정용


@dataclass
class IndicatorContribution:
    indicator_id: str
    name: str
    lr: float
    strength: float
    delta_logit: float
    source_types: list[str]
    refs: list[str]
    discount: float = 1.0          # 반대 증거로 깎인 강도 배율 (1 = 영향 없음)
    details: list = field(default_factory=list)   # 근거별 계산 내역 (가시화용)


@dataclass
class HypothesisResult:
    hypothesis_id: str
    name: str
    prior: float
    probability: float
    impact: float
    threat: float
    contributions: list[IndicatorContribution] = field(default_factory=list)


@dataclass
class Conflict:
    """반대 징후와 그것이 신뢰를 깎은 지표 (출처 간 모순)."""
    counter_id: str
    counter_name: str
    strength: float
    source_types: list[str]
    discounted: dict[str, float]          # 대상 지표 → 강도 배율(0~1)
    refs: list[str]


@dataclass
class AssessmentResult:
    as_of: datetime
    hypotheses: list[HypothesisResult]
    top: HypothesisResult
    threat_score: float
    grade: str
    confidence: str
    conflicts: list[Conflict] = field(default_factory=list)


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


GAP_HOLD = 0.5   # 관측 공백 기간은 시의성 경과를 절반만 인정 (새 관측으로 반박될 기회가 없었으므로)


def effective_age(ev: Evidence, as_of: datetime) -> float:
    """시의성 경과일. 근거 이후 새 영상이 없던 기간(관측 공백)은 GAP_HOLD 비율만큼 경과로 덜 셈."""
    age = max(0.0, (as_of - ev.observed_at).total_seconds() / 86400)
    return max(0.0, age - GAP_HOLD * min(ev.gap_days, age))


def evidence_weight(ev: Evidence, grading: Grading, as_of: datetime, tau_days: float) -> float:
    age_days = effective_age(ev, as_of)
    return grading.weight(ev.reliability, ev.credibility) * math.exp(-age_days / tau_days) * ev.scale


def evidence_detail(ev: Evidence, grading: Grading, as_of: datetime, tau_days: float) -> dict:
    """근거 1건의 가중치 계산 내역: 출처등급 가중 × 시의성 감쇠 × 신호 배율."""
    age = effective_age(ev, as_of)
    base = grading.weight(ev.reliability, ev.credibility)
    decay = math.exp(-age / tau_days)
    return {"ref": ev.ref, "source_type": ev.source_type, "grade": f"{ev.reliability}{ev.credibility}",
            "grade_weight": round(base, 3), "age_days": round(age, 1), "tau_days": tau_days, "decay": round(decay, 3), "gap_days": round(ev.gap_days, 1),
            "scale": ev.scale, "weight": round(base * decay * ev.scale, 3), "quote": (ev.quote or "")[:160],
            "unit_key": ev.unit_key}


def indicator_strength(weighted: list[tuple[str, str, float]]) -> float:
    """weighted: (source_type, source_key, w) 목록 → 0~1 충족 강도."""
    best_by_source: dict[str, tuple[str, float]] = {}
    for stype, skey, w in weighted:
        if skey not in best_by_source or w > best_by_source[skey][1]:
            best_by_source[skey] = (stype, w)
    best_by_type: dict[str, float] = {}
    for stype, w in best_by_source.values():
        best_by_type[stype] = max(best_by_type.get(stype, 0.0), w)
    miss = 1.0
    for w in best_by_type.values():
        miss *= 1 - min(max(w, 0.0), 1.0)
    return 1 - miss


def _strength(ind, evidence: list[Evidence], grading: Grading, as_of: datetime) -> tuple[float, list[Evidence]]:
    evs = [e for e in evidence if e.indicator_id == ind.id and e.observed_at <= as_of]
    if not evs:
        return 0.0, []
    weighted = [(e.source_type, e.source_key, evidence_weight(e, grading, as_of, ind.tau_days)) for e in evs]
    return indicator_strength(weighted), evs


def detect_conflicts(catalog: Catalog, evidence: list[Evidence], grading: Grading, as_of: datetime) -> list[Conflict]:
    """반대 징후(모형 장비·보급 부재·통신 평시 수준 등)를 찾아 대상 지표의 할인 배율 계산."""
    out = []
    for c in catalog.counters().values():
        s, evs = _strength(c, evidence, grading, as_of)
        if s <= 0:
            continue
        mult = round(1 - c.discount_factor * s, 4)
        out.append(Conflict(c.id, c.name, round(s, 4), sorted({e.source_type for e in evs}),
                            {t: mult for t in c.discounts}, [e.ref for e in evs]))
    return out


def discount_map(conflicts: list[Conflict]) -> dict[str, float]:
    m: dict[str, float] = {}
    for c in conflicts:
        for t, mult in c.discounted.items():
            m[t] = m.get(t, 1.0) * mult
    return m


def score_hypothesis(h: Hypothesis, evidence: list[Evidence], grading: Grading, as_of: datetime,
                     discounts: dict[str, float] | None = None) -> HypothesisResult:
    """반대 징후(lr<1)는 logit을 낮추고, discounts는 반대 증거로 신뢰가 깎인 지표의 강도에 곱함."""
    logit = _logit(h.prior)
    contribs: list[IndicatorContribution] = []
    for ind in h.indicators:
        s, evs = _strength(ind, evidence, grading, as_of)
        if not evs:
            continue
        disc = (discounts or {}).get(ind.id, 1.0)
        s *= disc
        delta = s * math.log(ind.lr)
        logit += delta
        contribs.append(IndicatorContribution(
            indicator_id=ind.id, name=ind.name, lr=ind.lr, strength=round(s, 4),
            delta_logit=round(delta, 4),
            source_types=sorted({e.source_type for e in evs}),
            refs=[e.ref for e in evs], discount=round(disc, 4),
            details=[evidence_detail(e, grading, as_of, ind.tau_days) for e in evs],
        ))
    p = _sigmoid(logit)
    contribs.sort(key=lambda c: c.delta_logit, reverse=True)
    return HypothesisResult(h.id, h.name, h.prior, round(p, 4), h.impact, round(p * h.impact, 4), contribs)


def confidence_level(r: HypothesisResult, conflicts: list[Conflict] | None = None) -> str:
    """분석 신뢰도(확률과 별개): 독립 출처 유형 수와 충족 지표 수 기준.
    출처 간 모순(강도 0.3 이상 반대 징후)이 있으면 한 단계 낮춤."""
    level = _base_confidence(r)
    if any(c.strength >= 0.3 for c in (conflicts or [])):
        level = {"상": "중", "중": "하"}.get(level, "하")
    return level


def _base_confidence(r: HypothesisResult) -> str:
    types = {t for c in r.contributions if c.delta_logit > 0 for t in c.source_types}
    n_ind = sum(1 for c in r.contributions if c.strength >= 0.3 and c.delta_logit > 0)
    if len(types) >= 3 and n_ind >= 3:
        return "상"
    if len(types) >= 2 or n_ind >= 2:
        return "중"
    return "하"


def assess(catalog: Catalog, evidence: list[Evidence], grading: Grading, as_of: datetime) -> AssessmentResult:
    conflicts = detect_conflicts(catalog, evidence, grading, as_of)
    disc = discount_map(conflicts)
    results = [score_hypothesis(h, evidence, grading, as_of, disc) for h in catalog.hypotheses]
    results.sort(key=lambda r: r.threat, reverse=True)
    top = results[0]
    return AssessmentResult(
        as_of=as_of, hypotheses=results, top=top, threat_score=top.threat,
        grade=catalog.grade_for(top.threat), confidence=confidence_level(top, conflicts), conflicts=conflicts,
    )
