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


@dataclass
class IndicatorContribution:
    indicator_id: str
    name: str
    lr: float
    strength: float
    delta_logit: float
    source_types: list[str]
    refs: list[str]


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
class AssessmentResult:
    as_of: datetime
    hypotheses: list[HypothesisResult]
    top: HypothesisResult
    threat_score: float
    grade: str
    confidence: str


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def evidence_weight(ev: Evidence, grading: Grading, as_of: datetime, tau_days: float) -> float:
    age_days = max(0.0, (as_of - ev.observed_at).total_seconds() / 86400)
    return grading.weight(ev.reliability, ev.credibility) * math.exp(-age_days / tau_days)


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


def score_hypothesis(h: Hypothesis, evidence: list[Evidence], grading: Grading, as_of: datetime) -> HypothesisResult:
    logit = _logit(h.prior)
    contribs: list[IndicatorContribution] = []
    for ind in h.indicators:
        evs = [e for e in evidence if e.indicator_id == ind.id and e.observed_at <= as_of]
        if not evs:
            continue
        weighted = [(e.source_type, e.source_key, evidence_weight(e, grading, as_of, ind.tau_days)) for e in evs]
        s = indicator_strength(weighted)
        delta = s * math.log(ind.lr)
        logit += delta
        contribs.append(IndicatorContribution(
            indicator_id=ind.id, name=ind.name, lr=ind.lr, strength=round(s, 4),
            delta_logit=round(delta, 4),
            source_types=sorted({e.source_type for e in evs}),
            refs=[e.ref for e in evs],
        ))
    p = _sigmoid(logit)
    contribs.sort(key=lambda c: c.delta_logit, reverse=True)
    return HypothesisResult(h.id, h.name, h.prior, round(p, 4), h.impact, round(p * h.impact, 4), contribs)


def confidence_level(r: HypothesisResult) -> str:
    """분석 신뢰도(확률과 별개): 독립 출처 유형 수와 충족 지표 수 기준."""
    types = {t for c in r.contributions for t in c.source_types}
    n_ind = sum(1 for c in r.contributions if c.strength >= 0.3)
    if len(types) >= 3 and n_ind >= 3:
        return "상"
    if len(types) >= 2 or n_ind >= 2:
        return "중"
    return "하"


def assess(catalog: Catalog, evidence: list[Evidence], grading: Grading, as_of: datetime) -> AssessmentResult:
    results = [score_hypothesis(h, evidence, grading, as_of) for h in catalog.hypotheses]
    results.sort(key=lambda r: r.threat, reverse=True)
    top = results[0]
    return AssessmentResult(
        as_of=as_of, hypotheses=results, top=top, threat_score=top.threat,
        grade=catalog.grade_for(top.threat), confidence=confidence_level(top),
    )
