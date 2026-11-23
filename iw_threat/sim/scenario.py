"""합성 시나리오 정의 로딩 + 데이터 생성 (영상 표적·첩보 문서·전투서열).
생성기는 위협징후목록(지표·우도비·임계값)을 참조하지 않는다 — 평가 순환 방지."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

KM_PER_DEG_LAT = 111.32


@dataclass
class Unit:
    name: str
    parent: str | None
    garrison: tuple[float, float]
    location_type: str
    equipment: dict[str, int]
    in_orbat: bool = True          # False: 전투서열 미등재(미상 부대) — 식별 불가 상황 재현
    template: str | None = None    # 편제 템플릿 (제대 추정 평가용)


@dataclass
class Activity:
    unit: str
    equipment: str
    start: int
    end: int
    to_count: int
    move_km: tuple[float, float] = (0.0, 0.0)
    sensors: tuple[str, ...] = ("EO", "SAR")   # 구름·야간에는 SAR 가능 활동만 관측
    event: str | None = None                   # 시나리오 이벤트 ID (scenario_events.yaml)


@dataclass
class Report:
    day: int
    source: str
    name: str
    reliability: str
    credibility: int
    truth: bool
    text: str


@dataclass
class Scenario:
    id: str
    name: str
    area_id: str
    truth: dict
    period: tuple[int, int]
    assess: tuple[int, int]
    noise: dict
    units: list[Unit]
    activities: list[Activity]
    reports: list[Report] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def load_scenario(path: str | Path) -> Scenario:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Scenario(
        id=raw["id"], name=raw["name"], area_id=raw["area_id"], truth=raw["truth"],
        period=(raw["period"]["start"], raw["period"]["end"]),
        assess=(raw["assess"]["start"], raw["assess"]["end"]),
        noise=raw.get("noise", {}),
        units=[Unit(u["name"], u.get("parent"), tuple(u["garrison"]), u.get("location_type", "주둔지"),
                    dict(u.get("equipment") or {})) for u in raw["units"]],
        activities=[Activity(a["unit"], a["equipment"], a["from"], a["to"], a["to_count"],
                             tuple(a.get("move_km", (0, 0)))) for a in raw.get("activities", [])],
        reports=[Report(r["day"], r["source"], r["name"], r["reliability"], int(r["credibility"]),
                        bool(r.get("truth", True)), r["text"]) for r in raw.get("reports", [])],
        meta={"weather": raw.get("weather", []), "imagery": raw.get("imagery", []), "key_targets": raw.get("key_targets", []), "issued_posture": raw.get("issued_posture", [])},
    )


# ── 기상 (전장정보분석: 기상이 작전에 미치는 영향) ──
def weather_on(sc: Scenario, day: int) -> dict | None:
    """구간 정의(from~to, 나중 구간 우선)에서 해당 일 기상값."""
    hit = None
    for w in sc.meta.get("weather", []):
        if w["from"] <= day <= w["to"]:
            hit = {k: v for k, v in w.items() if k not in ("from", "to", "note")}
    return hit


def weather_series(sc: Scenario, d_day: datetime) -> list[dict]:
    out = []
    for day in range(sc.period[0], sc.period[1] + 1):
        w = weather_on(sc, day)
        if w is not None:
            t = (d_day + timedelta(days=day)).replace(hour=9, minute=0, second=0, microsecond=0)
            out.append({"area_id": sc.area_id, "valid_at": t.isoformat(), "kind": "obs", "data": w})
    return out


def eo_blocked_days(sc: Scenario) -> set[int]:
    """광학 관측 불가일 (운량 70% 이상 또는 강수) — SAR 관측 가능 활동만 관측됨."""
    out = set()
    for day in range(sc.period[0], sc.period[1] + 1):
        w = weather_on(sc, day)
        if w and (w.get("cloud_pct", 0) >= 70 or w.get("precip_mm", 0) >= 1):
            out.add(day)
    return out


# ── 활동 → 날짜별 '실제' 상태 ──
def _progress(day: int, a: Activity) -> float:
    if day <= a.start:
        return 0.0
    if day >= a.end:
        return 1.0
    return (day - a.start) / max(a.end - a.start, 1)


def true_state(sc: Scenario, day: int) -> list[dict]:
    """해당 일의 부대별 장비 실제 수량·위치 (관측 잡음 전). 같은 부대·장비의 활동은 시간순으로 이어서 적용."""
    out = []
    for u in sc.units:
        eqs = set(u.equipment) | {a.equipment for a in sc.activities if a.unit == u.name}
        for eq in sorted(eqs):
            count, dn, de = float(u.equipment.get(eq, 0)), 0.0, 0.0
            sensors: tuple[str, ...] = ("EO", "SAR")
            for a in sorted((x for x in sc.activities if x.unit == u.name and x.equipment == eq), key=lambda x: x.start):
                p = _progress(day, a)
                if p <= 0:
                    break
                count = count + (a.to_count - count) * p
                dn, de = dn + a.move_km[0] * p, de + a.move_km[1] * p
                sensors = a.sensors
            if count <= 0.5:
                continue
            lat = u.garrison[0] + dn / KM_PER_DEG_LAT
            lon = u.garrison[1] + de / (KM_PER_DEG_LAT * math.cos(math.radians(u.garrison[0])))
            out.append({"unit": u.name, "equipment": eq, "count": count, "lat": lat, "lon": lon, "sensors": sensors})
    return out


# ── 관측 모델(잡음) ──
def _jitter(lat: float, lon: float, sd_m: float, rnd: random.Random) -> tuple[float, float]:
    dn, de = rnd.gauss(0, sd_m) / 1000, rnd.gauss(0, sd_m) / 1000
    return (lat + dn / KM_PER_DEG_LAT, lon + de / (KM_PER_DEG_LAT * math.cos(math.radians(lat))))


FP_EQUIPMENT = ["truck", "tank", "artillery", "apc"]


def generate_targets(sc: Scenario, d_day: datetime, seed: int = 7) -> list[dict]:
    """영상 판독 표적 이벤트 (관측 누락·구름·수량 오차·좌표 오차·오탐 포함)."""
    rnd = random.Random(seed)
    nz = sc.noise
    out = []
    cloud = set(nz.get("cloud_days", [])) | eo_blocked_days(sc)
    adverse = nz.get("adverse_weather_share", 0.0)
    for day in range(sc.period[0], sc.period[1] + 1):
        if rnd.random() > nz.get("p_observe", 1.0):
            continue
        cloudy = day in cloud or rnd.random() < adverse          # 악천후: SAR 가능 표적만 관측
        p_sar = nz.get("p_sar_on_cloudy", 1.0)
        if cloudy and p_sar < 1.0 and rnd.random() > p_sar:   # 광학 불가일에 SAR 촬영이 없으면 관측 공백
            continue
        t = (d_day + timedelta(days=day)).replace(hour=10, minute=0, second=0, microsecond=0)
        for s in true_state(sc, day):
            if cloudy and "SAR" not in s["sensors"]:
                continue
            if rnd.random() < nz.get("p_miss", 0.0):
                continue
            n = max(0, round(s["count"] * (1 + rnd.gauss(0, nz.get("count_rel_sd", 0.0)))))
            if s["equipment"] == "camouflaged":                # 위장 표적은 일부만 탐지
                n = sum(rnd.random() < nz.get("p_detect_camouflaged", 0.6) for _ in range(n))
            if n == 0:
                continue
            lat, lon = _jitter(s["lat"], s["lon"], nz.get("position_sd_m", 0), rnd)
            if s["equipment"].startswith("decoy_"):            # 탐지기 기만체 판별 모형: 일부는 실표적으로 오인
                real = sum(rnd.random() > nz.get("p_decoy_recognized", 0.8) for _ in range(n))
                if n - real:
                    out.append(_event(sc, t, s["equipment"], n - real, lat, lon, day, s["unit"], decoy=True))
                if real:
                    out.append(_event(sc, t, s["equipment"][len("decoy_"):], real, lat, lon, day, s["unit"], decoy=True))
                continue
            out.append(_event(sc, t, s["equipment"], n, lat, lon, day, s["unit"]))
        for u in sc.units:
            if rnd.random() < nz.get("false_positive_rate", 0.0):
                lat, lon = _jitter(*u.garrison, 400, rnd)
                out.append(_event(sc, t, rnd.choice(nz.get("fp_equipment", FP_EQUIPMENT)), rnd.randint(1, 3), lat, lon, day, u.name, fp=True))
    sc_ = nz.get("sar_check")                 # SAR 금속 반사파 확인 (별도 난수 — 기존 표적 생성 결과 불변)
    if sc_:
        r2 = random.Random(seed + 99)
        dec_units, dec_eq = set(sc_.get("decoy_units", [])), set(sc_.get("decoy_equipment", []))
        for e in out:
            if r2.random() < sc_.get("p_sar", 0.5):
                is_dummy = e["decoy_truth"] or (e["truth_unit"] in dec_units and e["equipment_type"] in dec_eq
                                                and r2.random() < sc_.get("dummy_share", 0.6))
                e["sar_verified"] = not is_dummy
    return out


def _event(sc: Scenario, t: datetime, eq: str, n: int, lat: float, lon: float, day: int,
           unit: str, fp: bool = False, decoy: bool = False) -> dict:
    """truth_unit·false_positive·decoy_truth는 평가용 정답 (API는 무시)."""
    return {"area_id": sc.area_id, "observed_at": t.isoformat(), "equipment_type": eq, "count": n,
            "lat": round(lat, 6), "lon": round(lon, 6), "image_ref": f"SIM-{sc.id}-D{day:+d}",
            "truth_unit": unit, "false_positive": fp, "decoy_truth": decoy, "day": day}


def report_documents(sc: Scenario, d_day: datetime) -> list[dict]:
    out = []
    for i, r in enumerate(sorted(sc.reports, key=lambda x: x.day), 1):
        t = (d_day + timedelta(days=r.day)).replace(hour=15, minute=0, second=0, microsecond=0)
        out.append({"filename": f"{sc.id}_report{i:02d}_D{r.day:+d}.txt", "text": r.text, "title": f"{r.name} 보고 (D{r.day:+d})",
                    "source_name": r.name, "source_type": r.source, "reliability": r.reliability,
                    "credibility": r.credibility, "reported_at": t.isoformat(), "truth": r.truth, "day": r.day})
    return out


def orbat_rows(sc: Scenario) -> list[dict]:
    """전투서열 records.csv 형식 행 (파서 산출물과 동일 구조)."""
    import mgrs as _mgrs

    from app.orbat.patterns import echelon_of, unit_key

    conv = _mgrs.MGRS()
    rows = []
    for u in sc.units:
        if not u.in_orbat:
            continue
        m = conv.toMGRS(u.garrison[0], u.garrison[1], MGRSPrecision=5)
        rows.append({"doc": f"sim_{sc.id}", "page": "1", "source": "table", "unit_raw": u.name, "unit_key": unit_key(u.name),
                     "echelon": echelon_of(u.name) or "", "echelon_from": "name",
                     "parent_raw": u.parent or "", "parent_key": unit_key(u.parent) if u.parent else "",
                     "mgrs": m, "lat": u.garrison[0], "lon": u.garrison[1], "mgrs_precision_m": "1",
                     "location_type": u.location_type, "equipment": "", "count": "", "as_of": "", "remark": "",
                     "quote": "합성 시나리오", "bbox": "", "images": "", "issues": ""})
    return rows
