"""영상 판독 표적 이력 기반 변화 분석 (순수 함수)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean


@dataclass(frozen=True)
class TargetRow:
    id: int
    equipment_type: str
    observed_at: datetime
    count: int
    source_id: int | None = None


@dataclass
class Change:
    equipment_type: str
    baseline_level: float
    recent_level: int
    delta: float
    latest_at: datetime
    refs: list[int]


def detect_changes(rows: list[TargetRow], as_of: datetime, window_days: int = 7, baseline_days: int = 30) -> list[Change]:
    """최근 창(as_of-window, as_of]의 최대 수량과 그 이전 기준기간 평균 수량을 비교."""
    w_start = as_of - timedelta(days=window_days)
    b_start = w_start - timedelta(days=baseline_days)
    by_eq: dict[str, list[TargetRow]] = {}
    for r in rows:
        by_eq.setdefault(r.equipment_type, []).append(r)

    out: list[Change] = []
    for eq, rs in by_eq.items():
        recent = [r for r in rs if w_start < r.observed_at <= as_of]
        if not recent:
            continue
        base = [r.count for r in rs if b_start < r.observed_at <= w_start]
        baseline = mean(base) if base else 0.0
        peak = max(recent, key=lambda r: r.count)
        out.append(Change(
            equipment_type=eq, baseline_level=round(baseline, 2), recent_level=peak.count,
            delta=round(peak.count - baseline, 2),
            latest_at=max(r.observed_at for r in recent),
            refs=[r.id for r in recent],
        ))
    out.sort(key=lambda c: c.delta, reverse=True)
    return out
