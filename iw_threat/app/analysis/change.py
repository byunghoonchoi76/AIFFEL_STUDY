"""영상 판독 표적 이력 기반 변화 분석 (순수 함수)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median


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
    unit_key: str | None = None        # 부대별 기준선일 때 식별 부대 (None = 미식별·지역 단위)
    unit_name: str | None = None
    echelon: str | None = None
    dist_km: float | None = None
    baseline_obs: int = 0              # 기준기간 관측 횟수 (0이면 평시 자료 없음)
    baseline_sd: float = 0.0           # 기준기간 수량 표준편차 (판독 오차·평시 변동폭)
    recent_mean: float = 0.0           # 최근 창 관측 수량 평균
    recent_n: int = 0                  # 최근 창 관측 횟수

    @property
    def zscore(self) -> float | None:
        """평시 변동폭 대비 증가 크기 (관측 3회 미만이면 None)."""
        if self.baseline_obs < 3:
            return None
        return round(self.delta / max(self.baseline_sd, 0.5), 2)

    @property
    def persist_z(self) -> float | None:
        """지속성 z: 최근 관측 평균의 평시 대비 증가 ÷ 표준오차. 여러 번 반복 관측된 작은 증가는
        단발 잡음과 구분되므로, 관측 기회가 적은 상황에서 약한 신호를 놓치지 않도록 사용."""
        if self.baseline_obs < 3 or self.recent_n < 2:
            return None
        se = max(self.baseline_sd, 0.5) * (1 / self.recent_n + 1 / self.baseline_obs) ** 0.5
        return round((self.recent_mean - self.baseline_level) / se, 2)

    @property
    def ratio(self) -> float | None:
        """평시 대비 배수 (평시 평균이 0이면 None)."""
        return round(self.recent_level / self.baseline_level, 1) if self.baseline_level > 0 else None


def noise_sd(counts: list[int]) -> float:
    """판독 잡음 표준편차 추정: 연속 관측 차이의 중앙절대편차(MAD) × 1.4826 ÷ √2.
    일정한 증가 추세(차이가 고르게 양수)는 잡음으로 잡히지 않음."""
    if len(counts) < 3:
        return 0.0
    diffs = [b - a for a, b in zip(counts, counts[1:])]
    m = median(diffs)
    mad = median(abs(d - m) for d in diffs)
    return round(1.4826 * mad / 2 ** 0.5, 2)


def detect_changes(rows: list[TargetRow], as_of: datetime, window_days: int = 7, baseline_days: int = 30) -> list[Change]:
    """최근 창(as_of-window, as_of]의 최대 수량과 그 이전 기준기간 중앙값(평시 수준)을 비교."""
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
        base_rows = sorted((r for r in rs if b_start < r.observed_at <= w_start), key=lambda r: r.observed_at)
        base = [r.count for r in base_rows]
        # 평시 수준 = 중앙값, 변동폭 = 연속 관측 간 차이의 MAD 기반 추정 → 기준기간에 증가 추세가 섞여도 왜곡이 작음
        baseline = median(base) if base else 0.0
        peak = max(recent, key=lambda r: r.count)
        out.append(Change(
            equipment_type=eq, baseline_level=round(baseline, 2), recent_level=peak.count,
            delta=round(peak.count - baseline, 2),
            latest_at=max(r.observed_at for r in recent),
            refs=[r.id for r in recent], baseline_obs=len(base),
            recent_mean=round(sum(r.count for r in recent) / len(recent), 2), recent_n=len(recent),
            baseline_sd=noise_sd(base),
        ))
    out.sort(key=lambda c: c.delta, reverse=True)
    return out


def detect_unit_changes(rows: list[TargetRow], unit_of: dict[int, dict | None], as_of: datetime,
                        window_days: int = 7, baseline_days: int = 30) -> list[Change]:
    """부대별 평시 기준선: 표적 이벤트를 식별 부대별로 나눠, 부대마다 최근 vs 평시를 비교.
    unit_of: 이벤트 id → 식별 결과({unit_key, name, echelon, dist_m}) 또는 None(미식별)."""
    groups: dict[str | None, list[TargetRow]] = {}
    for r in rows:
        u = unit_of.get(r.id)
        groups.setdefault(u["unit_key"] if u else None, []).append(r)
    out: list[Change] = []
    for key, rs in groups.items():
        info = next((unit_of[r.id] for r in rs if unit_of.get(r.id)), None)
        for ch in detect_changes(rs, as_of, window_days, baseline_days):
            if info:
                ch.unit_key, ch.unit_name, ch.echelon = key, info["name"], info.get("echelon")
                dists = [unit_of[i]["dist_m"] for i in ch.refs if unit_of.get(i)]
                ch.dist_km = round(min(dists) / 1000, 2) if dists else None
            out.append(ch)
    out.sort(key=lambda c: c.delta, reverse=True)
    return out
