"""경보·감시태세 건의 엔진 (워치콘·진돗개·두루미 등 트랙별) — 순수 함수.
날짜순 평가 이력을 보고 트랙마다 격상·유지·하향을 건의. 발령은 지휘관 권한이므로 결과는 항상 '건의'."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONF_ORDER = {"하": 0, "중": 1, "상": 2}


@dataclass
class Track:
    id: str
    name: str
    scope: str
    hypotheses: list[str] | str        # 'all' 또는 가설 ID 목록
    baseline: int
    level_names: dict[int, str]
    levels: list[dict]
    downgrade_hold_days: int
    decisive: list[dict] = field(default_factory=list)
    defcon_review: dict = field(default_factory=dict)
    low_confidence_action: str = "정보 수집 강화"

    def name_of(self, level: int) -> str:
        return self.level_names.get(level, f"{self.name} {level}")

    def label(self, level: int) -> str:
        return next((l["label"] for l in self.levels if l["level"] == level), "")


def load_readiness(path: str | Path) -> tuple[list[Track], str]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    tracks = []
    for t in raw["tracks"]:
        tracks.append(Track(
            t["id"], t["name"], t.get("scope", ""), t.get("hypotheses", "all"), int(t["baseline"]),
            {int(k): v for k, v in (t.get("level_names") or {}).items()},
            sorted(t["levels"], key=lambda l: l["level"]), int(t.get("downgrade_hold_days", 0)),
            t.get("decisive", []), t.get("defcon_review", {}), raw.get("low_confidence_action", "정보 수집 강화")))
    return tracks, raw.get("basis", "")


@dataclass
class Observation:
    """하루치 평가 요약."""
    day: float                       # 일 단위 시점 (차이만 사용)
    threats: dict[str, float]        # 가설 ID → 위협 점수(확률 × 영향도)
    confidence: str
    indicators: set[str]
    label: str = ""


def _track_threat(track: Track, ob: Observation) -> float:
    if track.hypotheses == "all":
        return max(ob.threats.values(), default=0.0)
    return max((ob.threats.get(h, 0.0) for h in track.hypotheses), default=0.0)


def _candidate(track: Track, ob: Observation) -> tuple[int, list[str], bool, float]:
    threat = _track_threat(track, ob)
    reasons, held, level = [], False, track.baseline
    for lv in track.levels:                                   # 높은 단계부터
        if threat >= lv["min_threat"]:
            if CONF_ORDER.get(ob.confidence, 0) >= CONF_ORDER.get(lv.get("min_confidence", "하"), 0):
                level = lv["level"]
                if level < track.baseline:
                    reasons.append(f"위협 점수 {threat:.0%} ≥ {lv['min_threat']:.0%} (분석 신뢰도 {ob.confidence})")
                break
            held = True
    for rule in track.decisive:
        if set(rule["indicators"]) <= ob.indicators and rule["min_level"] < level:
            level = rule["min_level"]
            reasons.append(f"결정적 징후: {rule['note']}")
    return level, reasons, held, threat


def recommend_series(track: Track, obs: list[Observation]) -> list[dict]:
    """격상은 즉시, 하향은 downgrade_hold_days 동안 낮은 조건 지속 시 한 단계씩."""
    out, current, lower_since = [], track.baseline, None
    for ob in sorted(obs, key=lambda o: o.day):
        cand, reasons, held, threat = _candidate(track, ob)
        action, note = "유지", ""
        if cand < current:
            action, current, lower_since = "격상", cand, None
        elif cand > current:
            lower_since = ob.day if lower_since is None else lower_since
            if ob.day - lower_since >= track.downgrade_hold_days:
                action, current, lower_since = "하향", current + 1, (ob.day if current + 1 < cand else None)
                reasons = [f"하향 조건 {track.downgrade_hold_days}일 이상 지속"]
            else:
                note = f"하향 조건 {ob.day - lower_since:g}/{track.downgrade_hold_days}일 — 유지"
        else:
            lower_since = None
        if held:   # 더 높은 단계 점수는 넘었지만 신뢰도 부족 — 그 단계 대신 정보 수집 강화 건의
            note = (note + " · " if note else "") + f"상위 단계 기준 초과이나 분석 신뢰도 부족 → {track.low_confidence_action} 건의"
        dr = track.defcon_review
        defcon = bool(dr) and current <= dr.get("at_level", 0) and \
            CONF_ORDER.get(ob.confidence, 0) >= CONF_ORDER.get(dr.get("min_confidence", "상"), 2)
        out.append({"track": track.id, "day": ob.day, "label": ob.label, "level": current,
                    "level_name": track.name_of(current), "level_label": track.label(current),
                    "action": action, "reasons": reasons, "note": note, "collect_more": held,
                    "defcon_review": defcon, "threat": round(threat, 4), "confidence": ob.confidence})
    return out


def recommend_all(tracks: list[Track], obs: list[Observation]) -> dict[str, list[dict]]:
    return {t.id: recommend_series(t, obs) for t in tracks}


DECISIVE_MIN_STRENGTH = 0.45   # 영상 B2 근거 1건(약 0.7)은 통과, 약한 신호(×0.5 ≈ 0.36)는 단독으로 불충분


def observation_from_result(day: float, result: dict, label: str = "") -> Observation:
    """평가 결과(API 응답 또는 assessments.result+컬럼) → Observation."""
    hyps = result.get("hypotheses", [])
    threats = {h["hypothesis_id"]: float(h.get("threat", 0.0)) for h in hyps}
    # 결정적 징후 조합은 충분히 강하게 식별된 지표만 인정 (약한 신호·단일 저신뢰 출처만으로 태세 격상 방지)
    inds = {c["indicator_id"] for h in hyps for c in h.get("contributions", [])
            if c.get("strength", 1.0) >= DECISIVE_MIN_STRENGTH and c.get("delta_logit", 1.0) > 0}
    return Observation(day, threats, result.get("confidence", "하"), inds, label)
