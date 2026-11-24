"""전장정보분석 — 도심지 진출 속도와 아군 중요시설 취약성 (순수 함수).

- 도심지: "도시로 들어가는 것은 늪으로 들어가는 것"이라는 교시에 따라 북한군은 도심 직접 진입을 회피·우회하고,
  진입 시 진출 속도가 급감한다고 가정 → 시설별 지상 위협 도달 예상 시간(직접·우회)
- 중요시설: 국가중요시설·군사중요시설이 적 화력(장사정포·방사포·갱도포병) 사거리 안이고 풍하이면
  무력화 목적 화학탄 사용 위협이 큼 → 취약도(상/중/하)와 화학 위협 근거 생성
"""
from __future__ import annotations

import math
from pathlib import Path

import yaml

from ..taxonomy import match_keys

MANEUVER = {"tank", "apc", "ifv", "group:main_battle_tank", "group:light_armor", "march_column", "formation_wedge_v_line"}
FIRES = {"artillery": "artillery", "mlrs": "mlrs", "group:artillery": "artillery", "harts": "harts"}


def load_terrain(path: str | Path = "config/terrain_facilities.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def urban_of(lat: float, lon: float, cfg: dict) -> str | None:
    for u in cfg.get("urban_areas", []):
        if _km(lat, lon, u["lat"], u["lon"]) <= u["radius_km"]:
            return u["name"]
    return None


def advance_days(src: tuple[float, float], dst: tuple[float, float], cfg: dict, samples: int = 40) -> dict:
    """직선 경로 중 도심 구간 비율로 직접 진입 소요일, 도심 우회 소요일 추정."""
    rate = cfg["advance_rate_km_per_day"]
    d = _km(*src, *dst)
    urban = sum(1 for i in range(samples) if urban_of(src[0] + (dst[0] - src[0]) * (i + 0.5) / samples,
                                                      src[1] + (dst[1] - src[1]) * (i + 0.5) / samples, cfg)) / samples
    direct = d * (1 - urban) / rate["open"] + d * urban / rate["urban"]
    bypass = d * (cfg.get("urban_bypass_factor", 1.3) if urban > 0 else 1.0) / rate["open"]
    return {"km": round(d, 1), "urban_share": round(urban, 2), "direct_days": round(direct, 1), "bypass_days": round(bypass, 1)}


def positions(points: list[dict]) -> tuple[list[dict], list[dict]]:
    """최근 표적 → 기동부대 위치, 화력(투발 수단) 위치."""
    man, fires = [], []
    for p in points:
        if p.get("lat") is None:
            continue
        keys = match_keys(p["equipment_type"])
        if keys & MANEUVER:
            man.append(p)
        ft = next((FIRES[k] for k in keys if k in FIRES), None)
        if ft:
            fires.append({**p, "fire_type": ft})
    return man, fires


def facility_vulnerability(points: list[dict], cfg: dict, wx: dict | None = None, chem_favorable: bool = False) -> dict:
    man, fires = positions(points)
    rng = cfg.get("delivery_range_km", {})
    wind_from = (wx or {}).get("wind_dir_deg")
    wind_from = None if wind_from is None or wind_from < 0 else wind_from
    wind_to = (wind_from + 180) % 360 if wind_from is not None else None
    out = []
    for f in cfg.get("facilities", []):
        loc = (f["lat"], f["lon"])
        in_range = [x for x in fires if _km(x["lat"], x["lon"], *loc) <= rng.get(x["fire_type"], 0)]
        downwind = bool(wind_to is not None and any(
            abs((_bearing(x["lat"], x["lon"], *loc) - wind_to + 180) % 360 - 180) <= cfg.get("chem_downwind_deg", 30)
            for x in in_range))
        near = min(man, key=lambda p: _km(p["lat"], p["lon"], *loc), default=None)
        adv = advance_days((near["lat"], near["lon"]), loc, cfg) if near else None
        urban = urban_of(*loc, cfg)
        fastest = min(adv["direct_days"], adv["bypass_days"]) if adv else None
        if in_range and (downwind and chem_favorable or (fastest is not None and fastest <= 1)):
            level = "상"
        elif in_range or (fastest is not None and fastest <= 3):
            level = "중"
        else:
            level = "하"
        notes = []
        if in_range:
            types = sorted({x["fire_type"] for x in in_range})
            notes.append(f"적 투발 수단 사거리 내({', '.join(types)} {len(in_range)}건) — 무력화 목적 화학탄 사용 가능")
        if downwind:
            notes.append("풍하 위치" + (" · 화학 운용 유리 기상" if chem_favorable else ""))
        if urban:
            notes.append(f"도심({urban}) 내 — 지상 진입 지연(우회 예상), 화력·화학·특수전 위협 우선")
        out.append({"id": f["id"], "name": f["name"], "kind": f["kind"], "grade": f.get("grade"),
                    "urban": urban, "fires_in_range": len(in_range), "downwind": downwind,
                    "advance": adv, "vulnerability": level, "notes": notes})
    return {"facilities": sorted(out, key=lambda x: "상중하".index(x["vulnerability"])),
            "advance_rates": cfg["advance_rate_km_per_day"], "basis": cfg.get("version")}
