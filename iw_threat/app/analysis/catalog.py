"""위협징후목록·첩보 등급표 로딩 (외부 서비스 의존 없음)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class Catalog:
    hypotheses: list[Hypothesis]
    grades: list[GradeBand]
    meta: dict = field(default_factory=dict)

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
            )
            for i in h["indicators"]
        )
        for ind in inds:
            if ind.lr <= 0 or ind.tau_days <= 0:
                raise ValueError(f"{h['id']}/{ind.id}: lr, tau_days는 양수여야 함")
        if not 0 < h["prior"] < 1:
            raise ValueError(f"{h['id']}: prior는 0과 1 사이")
        hyps.append(Hypothesis(h["id"], h["name"], float(h["prior"]), float(h["impact"]), inds))
    grades = [GradeBand(g["name"], float(g["min"])) for g in raw["grades"]]
    return Catalog(hyps, grades, raw.get("meta", {}))


def load_grading(path: str | Path) -> Grading:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Grading(
        reliability={str(k): float(v) for k, v in raw["reliability"].items()},
        credibility={int(k): float(v) for k, v in raw["credibility"].items()},
        defaults=raw["defaults"],
    )
