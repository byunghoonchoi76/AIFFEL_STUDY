"""위협징후목록·첩보 등급표 로딩 (외부 서비스 의존 없음)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import math

import yaml


@dataclass(frozen=True)
class Indicator:
    id: str
    name: str
    lr: float
    tau_days: float
    equipment: tuple[str, ...] = ()
    min_delta: int = 0
    meaning: str = ""
    min_delta_by_echelon: tuple[tuple[str, int], ...] = ()   # 지표별 제대 임계값(있으면 배율보다 우선)
    counter: bool = False                 # 반대 징후(위협 가능성을 낮추는 증거, lr < 1)
    discounts: tuple[str, ...] = ()       # 반대 징후가 신뢰를 깎는 대상 지표 (예: 모형 장비 → 기갑 집결)
    discount_factor: float = 0.0          # 반대 징후 강도 1일 때 대상 지표 강도를 줄이는 비율


@dataclass(frozen=True)
class Hypothesis:
    id: str
    name: str
    prior: float
    impact: float
    indicators: tuple[Indicator, ...]


@dataclass(frozen=True)
class GradeBand:
    name: str
    min: float


@dataclass(frozen=True)
class Threshold:
    min_delta: int        # 이 이상 증가하면 충족
    min_ratio: float | None   # 평시 대비 이 배수 이상이면 (증가량이 min_delta의 절반 이상일 때) 충족
    basis: str            # 근거 문구용 설명


@dataclass
class Catalog:
    hypotheses: list[Hypothesis]
    grades: list[GradeBand]
    meta: dict = field(default_factory=dict)
    echelon_scale: dict[str, float] = field(default_factory=dict)   # 지역 단위 min_delta에 곱할 제대별 배율
    unit_min_ratio: float | None = None                              # 부대 단위 평시 대비 배수 기준

    def counters(self) -> dict[str, Indicator]:
        return {k: v for k, v in self.indicator_defs().items() if v.counter}

    def threshold(self, ind: Indicator, echelon: str | None) -> Threshold:
        """임계값 결정 순서: 지표별 제대 값 → 제대 배율 × 기본값(올림, 최소 1) → 기본값(지역 단위)."""
        if echelon is None:
            return Threshold(ind.min_delta, None, f"지역 +{ind.min_delta}대")
        explicit = dict(ind.min_delta_by_echelon)
        if echelon in explicit:
            md = explicit[echelon]
        elif echelon in self.echelon_scale:
            md = max(1, math.ceil(ind.min_delta * self.echelon_scale[echelon])) if ind.min_delta else 0
        else:
            md = ind.min_delta
        return Threshold(md, self.unit_min_ratio, f"{echelon} +{md}대" + (f" 또는 {self.unit_min_ratio:g}배" if self.unit_min_ratio else ""))

    def indicator_defs(self) -> dict[str, Indicator]:
        """지표 ID → 정의(가설 간 공통 속성: 이름·장비·임계값). LR은 가설별로 다름."""
        out: dict[str, Indicator] = {}
        for h in self.hypotheses:
            for ind in h.indicators:
                out.setdefault(ind.id, ind)
        return out

    def grade_for(self, score: float) -> str:
        for band in sorted(self.grades, key=lambda b: b.min, reverse=True):
            if score >= band.min:
                return band.name
        return self.grades[-1].name


@dataclass
class Grading:
    reliability: dict[str, float]
    credibility: dict[int, float]
    defaults: dict[str, dict]

    def weight(self, reliability: str, credibility: int) -> float:
        return self.reliability[reliability] * self.credibility[int(credibility)]

    def default_grade(self, source_type: str) -> tuple[str, int]:
        d = self.defaults[source_type]
        return d["reliability"], int(d["credibility"])


def load_catalog(path: str | Path) -> Catalog:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    hyps = []
    for h in raw["hypotheses"]:
        inds = tuple(
            Indicator(
                id=i["id"], name=i["name"], lr=float(i["lr"]), tau_days=float(i["tau_days"]),
                equipment=tuple(i.get("equipment", [])), min_delta=int(i.get("min_delta", 0)),
                meaning=str(i.get("meaning", "")),
                min_delta_by_echelon=tuple((str(k), int(v)) for k, v in (i.get("min_delta_by_echelon") or {}).items()),
                counter=bool(i.get("counter", False)), discounts=tuple(i.get("discounts", [])),
                discount_factor=float(i.get("discount_factor", 0.0)),
            )
            for i in h["indicators"]
        )
        for ind in inds:
            if ind.lr <= 0 or ind.tau_days <= 0:
                raise ValueError(f"{h['id']}/{ind.id}: lr, tau_days는 양수여야 함")
            if not 0 <= ind.discount_factor <= 1:
                raise ValueError(f"{h['id']}/{ind.id}: discount_factor는 0~1")
        if not 0 < h["prior"] < 1:
            raise ValueError(f"{h['id']}: prior는 0과 1 사이")
        hyps.append(Hypothesis(h["id"], h["name"], float(h["prior"]), float(h["impact"]), inds))
    grades = [GradeBand(g["name"], float(g["min"])) for g in raw["grades"]]
    th = raw.get("thresholds") or {}
    scale = {str(k): float(v) for k, v in (th.get("echelon_scale") or {}).items()}
    ratio = float(th["unit_min_ratio"]) if th.get("unit_min_ratio") else None
    return Catalog(hyps, grades, raw.get("meta", {}), scale, ratio)


def load_grading(path: str | Path) -> Grading:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Grading(
        reliability={str(k): float(v) for k, v in raw["reliability"].items()},
        credibility={int(k): float(v) for k, v in raw["credibility"].items()},
        defaults=raw["defaults"],
    )
