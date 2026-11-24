"""경보·감시태세 상황 판단 조건 엔진 (워치콘·데프콘·진돗개·두루미 등 트랙별) — 순수 함수.
날짜순 평가 이력을 보고 트랙마다 현재 정보 상황이 부합하는 '조건 단계'를 산출.
태세 판단·건의는 합참·연합사, 결정은 국방부장관·통수권자 재가 사항이므로 정보사는 건의하지 않으며, 결과는 합참·연합사 판단 지원 정보임."""
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
    low_confidence_action: str = "추가 첩보·관측 확인 필요"
    posture_evidence: dict = field(default_factory=dict)

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
            t.get("decisive", []), t.get("defcon_review", {}), raw.get("low_confidence_action", "추가 첩보·관측 확인 필요"),
            t.get("posture_evidence", {})))
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
        if set(rule["indicators"]) <= ob.indicators and rule["min_level"] < level \
                and threat >= rule.get("min_threat", 0.0):          # 조합 + 위협 수준을 함께 요구하는 규칙(선택)
            level = rule["min_level"]
            reasons.append(f"결정적 징후: {rule['note']}")
    return level, reasons, held, threat


def recommend_series(track: Track, obs: list[Observation]) -> list[dict]:
    """상향 조건은 즉시, 하향 조건은 downgrade_hold_days 동안 낮은 조건 지속 시 한 단계씩 반영."""
    out, current, lower_since = [], track.baseline, None
    for ob in sorted(obs, key=lambda o: o.day):
        cand, reasons, held, threat = _candidate(track, ob)
        action, note = "유지", ""
        if cand < current:
            action, current, lower_since = "상향 조건", cand, None
        elif cand > current:
            lower_since = ob.day if lower_since is None else lower_since
            if ob.day - lower_since >= track.downgrade_hold_days:
                action, current, lower_since = "하향 조건", current + 1, (ob.day if current + 1 < cand else None)
                reasons = [f"하향 조건 {track.downgrade_hold_days}일 이상 지속"]
            else:
                note = f"하향 조건 {ob.day - lower_since:g}/{track.downgrade_hold_days}일 — 유지"
        else:
            lower_since = None
        if held:   # 더 높은 단계 점수는 넘었지만 신뢰도 부족 — 분석 결과로 "추가 확인 필요" 표시 (수집 지시는 합참 수집 부서 소관)
            note = (note + " · " if note else "") + f"상위 단계 기준 초과이나 분석 신뢰도 부족 → {track.low_confidence_action}"
        dr = track.defcon_review   # 구버전 설정 호환 (현재는 데프콘을 별도 조건 트랙으로 산출)
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
