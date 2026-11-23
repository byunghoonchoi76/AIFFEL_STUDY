"""DB의 평가 이력으로 현재 태세 판단 조건 산출 (API용, 상황 판단 참고)."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..config import get_settings, to_aware
from ..stores import pg
from .readiness import load_readiness, observation_from_result, recommend_all


def current_readiness(area_id: str, as_of: datetime) -> dict:
    s = get_settings()
    as_of = to_aware(as_of)
    tracks, basis = load_readiness(s.readiness_path)
    hist = pg.fetch_assessment_history(area_id, as_of - timedelta(days=s.readiness_history_days), as_of)
    obs = []
    for h in hist:
        r = dict(h["result"])
        r["confidence"] = h["confidence"]
        days = (h["as_of"] - as_of).total_seconds() / 86400
        obs.append(observation_from_result(days, r, h["as_of"].isoformat()))
    recs = recommend_all(tracks, obs)
    now = {tid: (series[-1] if series else None) for tid, series in recs.items()}
    # 합참·연합사 발령 태세와 정보 상황 조건 비교 → 정보 판단 자료 (태세 건의 아님)
    try:
        issued = pg.fetch_posture(area_id, as_of)
    except Exception:  # noqa: BLE001
        issued = {}
    names = {t.id: t for t in tracks}
    for tid, cur in now.items():
        if cur is None:
            continue
        p = issued.get(tid)
        if p:
            cur["issued_level"], cur["issued_name"], cur["issuer"] = p["level"], names[tid].name_of(p["level"]), p["issuer"]
            cur["vs_issued"] = ("정보 상황이 발령 태세보다 높은 조건" if cur["level"] < p["level"] else
                                "정보 상황이 발령 태세보다 낮은 조건" if cur["level"] > p["level"] else "발령 태세와 일치")
        else:
            cur["issued_level"], cur["issued_name"], cur["issuer"], cur["vs_issued"] = None, None, None, "발령 태세 미입력"
    return {"basis": basis, "history_points": len(obs), "current": now,
            "changes": {tid: [x for x in series if x["action"] != "유지"] for tid, series in recs.items()}}
