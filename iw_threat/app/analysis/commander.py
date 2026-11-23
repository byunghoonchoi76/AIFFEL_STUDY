"""지휘관 정보요구(PIR·CCIR)와 합참·연합사 핵심표적·고가치표적 활동 — 순수 함수.

1) 표적목록(핵심표적·고가치표적)의 활동 탐지 → 위협도 근거(I_KEY_TARGET 등)
2) 평가 결과로 합참의장·연합사령관 PIR 답변 상태 산출
3) CCIR 보고 조건 충족 여부 (즉시 보고 대상)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

import yaml

from ..taxonomy import match_keys


@dataclass
class KeyTarget:
    target_id: str
    name: str
    category: str                      # 핵심표적 / 고가치표적
    owner: str                         # 합참 / 연합사 / 공동
    unit_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_m: float = 2000.0
    equipment: list[str] = field(default_factory=list)
    pir_ids: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=lambda: ["H_ATTACK"])
    note: str | None = None

    def indicator(self) -> str:
        if "H_NUCLEAR" in self.hypotheses:
            return "I_KEY_TARGET_WMD"
        return "I_KEY_TARGET" if self.category == "핵심표적" else "I_HV_TARGET"

    def wants(self, equipment_type: str) -> bool:
        return not self.equipment or bool(match_keys(equipment_type) & set(self.equipment))


def load_requirements(path: str | Path = "config/pir_ccir.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def target_activity(targets: list[KeyTarget], changes: list, points: list[dict], as_of: datetime,
                    window_days: int = 7, baseline_days: int = 30) -> list[dict]:
    """표적별 활동: ① 부대 매칭 — 해당 부대의 평시 대비 증가(변화 분석 결과) ② 위치 매칭 — 반경 내 표적 수량 증가."""
    out = []
    w0, b0 = as_of - timedelta(days=window_days), as_of - timedelta(days=window_days + baseline_days)
    for t in targets:
        hits = []
        if t.unit_name:
            for c in changes:
                if c.unit_name == t.unit_name and c.delta > 0 and t.wants(c.equipment_type) and \
                        (c.ratio is None or c.ratio >= 1.3 or (c.zscore or 0) >= 2):
                    hits.append({"equipment": c.equipment_type, "baseline": c.baseline_level, "recent": c.recent_level,
                                 "latest_at": c.latest_at, "refs": c.refs, "basis": "부대"})
        if t.lat is not None and t.lon is not None:
            near = [p for p in points if p.get("lat") is not None and t.wants(p["equipment_type"])
                    and _dist_m(t.lat, t.lon, p["lat"], p["lon"]) <= t.radius_m]
            by_eq: dict[str, list[dict]] = {}
            for p in near:
                by_eq.setdefault(p["equipment_type"], []).append(p)
            for eq, ps in by_eq.items():
                if any(h["equipment"] == eq for h in hits):
                    continue
                recent = [p for p in ps if w0 < p["observed_at"] <= as_of]
                base = [p["count"] for p in ps if b0 < p["observed_at"] <= w0]
                if not recent:
                    continue
                peak = max(recent, key=lambda p: p["count"])
                bl = median(base) if base else 0.0
                if peak["count"] - bl >= 1 and (bl == 0 or peak["count"] / bl >= 1.3):
                    hits.append({"equipment": eq, "baseline": bl, "recent": peak["count"],
                                 "latest_at": max(p["observed_at"] for p in recent), "refs": [p["id"] for p in recent],
                                 "basis": "위치"})
        if hits:
            out.append({"target_id": t.target_id, "name": t.name, "category": t.category, "owner": t.owner,
                        "indicator": t.indicator(), "pir_ids": t.pir_ids, "hits": hits,
                        "latest_at": max(h["latest_at"] for h in hits)})
    return out


def status_of(strength: float, levels: list[dict]) -> str:
    for lv in levels:
        if strength >= lv["min"] and (lv["min"] > 0 or strength == 0):
            return lv["name"]
    return levels[-1]["name"]


def pir_answers(req: dict, hypotheses: list[dict], activities: list[dict]) -> list[dict]:
    """PIR별 답변 상태: 연결 가설의 연결 징후(확률을 높인 것) 강도 최댓값 + 연결 표적 활동."""
    levels = sorted(req["status_levels"], key=lambda x: -x["min"])
    hyp = {h["hypothesis_id"]: h for h in hypotheses}
    out = []
    for cmd in req["commanders"]:
        for p in cmd["pir"]:
            contribs = [c for hid in p["hypotheses"] for c in (hyp.get(hid) or {}).get("contributions", [])
                        if c["indicator_id"] in p["indicators"] and c["delta_logit"] > 0]
            acts = [a for a in activities if p["id"] in a["pir_ids"] or a["indicator"] in p["indicators"]]
            strength = max([c["strength"] for c in contribs] + [0.0])
            if acts:
                strength = max(strength, 0.3)        # 지휘관 지정 표적 활동은 최소 '징후 있음'
            prob = max([(hyp.get(h) or {}).get("probability", 0.0) for h in p["hypotheses"]] + [0.0])
            out.append({"commander": cmd["name"], "id": p["id"], "question": p["question"], "status": status_of(strength, levels),
                        "strength": round(strength, 2), "probability": round(prob, 4),
                        "evidence": sorted({c["name"] for c in contribs}),
                        "targets": [a["name"] for a in acts]})
    return out


def ccir_alerts(req: dict, pirs: list[dict], activities: list[dict]) -> list[dict]:
    order = {lv["name"]: i for i, lv in enumerate(sorted(req["status_levels"], key=lambda x: x["min"]))}
    pir = {p["id"]: p for p in pirs}
    out = []
    for cmd in req["commanders"]:
        for c in cmd.get("ccir", []):
            hit, why = False, ""
            if c["type"] == "key_target_activity":
                acts = [a for a in activities if a["category"] == c.get("category", a["category"])]
                hit, why = bool(acts), ", ".join(a["name"] for a in acts)
            elif c["type"] == "pir_status":
                p = pir.get(c["pir"])
                hit = bool(p) and order[p["status"]] >= order[c["min_status"]]
                why = f"{c['pir']} {p['status']}" if p else ""
            elif c["type"] == "multi_target":
                hit, why = len(activities) >= c["min_count"], f"{len(activities)}개 표적 활동"
            if hit:
                out.append({"commander": cmd["name"], "id": c["id"], "text": c["text"], "basis": why})
    return out
