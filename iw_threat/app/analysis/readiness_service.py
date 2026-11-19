"""DB의 평가 이력으로 현재 태세 건의 계산 (API용)."""
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
    return {"basis": basis, "history_points": len(obs), "current": now,
            "changes": {tid: [x for x in series if x["action"] != "유지"] for tid, series in recs.items()}}
