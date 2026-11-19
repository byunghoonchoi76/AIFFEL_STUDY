"""표적 → 부대 식별 규칙 (순수 함수). DB 조회는 store.py, 여기는 반경·순위 계산만."""
from __future__ import annotations

import math

# 제대별 기본 활동 반경(m): 표적이 주둔지에서 이 거리 안에 있으면 그 부대 활동 후보
ECHELON_RADIUS_M = {"군": 30000, "군단": 20000, "사단": 10000, "여단": 5000,
                    "연대": 3000, "대대": 1500, "중대": 800, "소대": 400}
DEFAULT_RADIUS_M = 3000

# 장비 종류 ↔ 부대 병종(부대명에 포함된 단어) 친화도: 일치하면 같은 거리라도 우선
EQUIP_UNIT_HINT = {
    "tank": ("전차", "기갑", "기계화"), "ifv": ("기계화", "보병"), "apc": ("기계화", "보병"),
    "artillery": ("포병",), "mlrs": ("포병", "방사포"), "bridging": ("공병", "도하"), "engineer": ("공병",),
    "field_hospital": ("의무",), "command_vehicle": (), "truck": ("수송", "군수", "보급"),
    "fuel_truck": ("수송", "군수", "유류"), "sam": ("방공", "고사"), "aaa": ("방공", "고사"),
}


def radius_for(echelon: str | None, precision_m: int | None = None) -> int:
    """제대별 반경 + 좌표 정밀도(예: 100m 단위 좌표면 +100m)."""
    return ECHELON_RADIUS_M.get(echelon or "", DEFAULT_RADIUS_M) + int(precision_m or 0)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def affinity(equipment: str | None, unit_name: str) -> bool:
    return bool(equipment) and any(w in unit_name for w in EQUIP_UNIT_HINT.get(equipment, ()))


def rank_candidates(cands: list[dict], equipment: str | None = None) -> list[dict]:
    """cands: [{unit_key, name, echelon, dist_m, radius_m, ...}] → 점수 오름차순.
    점수 = 거리/반경 (반경 안이면 1 이하), 장비-병종 일치 시 절반으로 가중.
    같은 점수면 하위 제대(더 구체적인 부대)를 우선."""
    from .patterns import ECHELON_RANK

    out = []
    for c in cands:
        ratio = c["dist_m"] / max(c["radius_m"], 1)
        aff = affinity(equipment, c["name"])
        out.append({**c, "within": c["dist_m"] <= c["radius_m"], "affinity": aff,
                    "score": round(ratio * (0.5 if aff else 1.0), 4)})
    out.sort(key=lambda c: (c["score"], -ECHELON_RANK.get(c.get("echelon") or "", -1)))
    return out


def best_match(cands: list[dict], equipment: str | None = None) -> dict | None:
    """반경 안에 있는 후보 중 최상위. 반경 밖이면 '미식별'로 None."""
    ranked = [c for c in rank_candidates(cands, equipment) if c["within"]]
    return ranked[0] if ranked else None
