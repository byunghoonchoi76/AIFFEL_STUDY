"""대항군 군단 연속 시나리오 변환기:
scenarios/opfor_corps/{orbat, layout_rules, scenario_events, test_cases}.yaml → 시뮬레이터 Scenario.

- 가상 시험지역(layout_rules.test_area, 60×100 km, 가상 군사분계선)을 위경도에 대응 (실제 지형과 무관한 가상 좌표)
- 부대: orbat.units (가상 부대명 부여), 편제 템플릿 수량 × 관측 가시율 = 평시 영상 관측량
- 이벤트: scenario_events의 국면·이벤트를 '관측 가능한 변화(장비 라벨 수량·위치)'로 변환 (EFFECTS)
  ※ 변환표는 시나리오 해석이며 위협징후목록(지표·우도비)을 참조하지 않음 — 평가 순환 방지
- 시험 설계 가정값은 ASSUMPTIONS에 모아 보고서에 명시"""
from __future__ import annotations

import math
import random
from pathlib import Path

import yaml

from .scenario import KM_PER_DEG_LAT, Activity, Scenario, Unit

ORIGIN = (37.70, 126.70)          # 가상 시험지역 남서쪽 모서리 (가상 좌표 기준점)
MDL_Y_KM = 20                     # 가상 군사분계선: 남쪽 끝에서 20 km
ASSUMPTIONS = {
    "visible_ratio": 0.5,          # 평시 주둔지에서 영상으로 보이는 장비 비율
    "division_trucks": 200,        # 사단 트럭 (편제표 미기재)
    "mech_ifv": 279,               # 기계화사단 보병전투차·장갑차 (편제표 미기재)
    "tank_split": {"tank_t62_chonma": 40, "tank_pokpung": 30, "tank_songun": 15, "tank_new_gen": 8},
    "harts_sites": 14,
}

# orbat 부대 id → 가상 부대명 (실제 부대와 무관)
UNIT_NAMES = {
    "corps_hq": "제98가상군단", "inf_div_1": "제9801가상보병사단", "inf_div_2": "제9802가상보병사단",
    "mech_div_1": "제9803가상기계화보병사단", "tank_bde_1": "제9804가상전차여단", "corps_arty_grp": "제9805가상포병여단",
    "missile_bn_1": "제9806가상미사일대대", "ad_units": "제9807가상고사포연대", "light_inf_bde_1": "제9808가상경보병여단",
    "recon_bn_1": "제9809가상정찰대대", "engr_rgt_bridge": "제9810가상도하공병연대", "ew_co_1": "제9811가상전자전중대",
    "nbc_bn_1": "제9812가상화학방어대대", "deception_unit_1": "제9813가상기만대대",
}
TEMPLATE_TYPE = {"inf_div_1": "infantry_division", "inf_div_2": "infantry_division", "mech_div_1": "mech_division",
                 "tank_bde_1": "tank_brigade", "corps_arty_grp": "corps_artillery_group"}
ITEM_CLASS = {
    "tank_t55_t62": "tank_t55", "light_tank": "light_tank", "ifv_apc": "ifv_apc", "recon_brdm2": "recon_brdm2",
    "howitzer_152mm": "towed_howitzer", "howitzer_122mm": "towed_howitzer", "sp_howitzer_152mm": "sp_howitzer_122_152",
    "sp_howitzer_122mm": "sp_howitzer_122_152", "sp_gun_170": "sp_gun_170", "mrl_107mm": "mrl", "mrl_240mm": "mrl",
    "mortar_120mm": "mortar_120", "aaa_zpu4": "aaa_zpu4", "aaa_14_5mm": "aaa_zpu4", "aaa_s60_57mm": "aaa_s60_37mm",
    "aaa_37mm": "aaa_s60_37mm", "aaa_57mm": "aaa_s60_37mm", "aaa_57mm_type80_zsu57": "aaa_s60_37mm",
    "aaa_vehicle_m1983_btr152": "aaa_zu23_2", "radar_firecan": "radar", "truck_2_5t": "truck",
}
# 배치 지역: 군사분계선에서 북쪽으로의 거리(km) 범위 (layout_rules.defense 기준 해석)
ZONES = {"security_zone": (2, 10), "forward_assembly": (9, 14), "defense_zone_1": (12, 25), "defense_zone_1_rear": (22, 27),
         "defense_zone_2": (33, 46), "rear_area": (55, 72), "dispersed": (8, 40)}


def _xy_to_latlon(x_km: float, y_km: float) -> tuple[float, float]:
    lat = ORIGIN[0] + y_km / KM_PER_DEG_LAT
    lon = ORIGIN[1] + x_km / (KM_PER_DEG_LAT * math.cos(math.radians(ORIGIN[0])))
    return round(lat, 6), round(lon, 6)


def _place(zone: str, sector: str | None, rnd: random.Random) -> tuple[float, float]:
    lo, hi = ZONES.get(zone, (15, 30))
    x = rnd.uniform(6, 26) if sector == "west" else rnd.uniform(34, 54) if sector == "east" else rnd.uniform(15, 45)
    return x, MDL_Y_KM + rnd.uniform(lo, hi)


def _baseline(tpl: dict | None, uid: str, vis: float) -> dict[str, int]:
    eq: dict[str, float] = {}
    for item, n in ((tpl or {}).get("equipment") or {}).items():
        cls = ITEM_CLASS.get(item)
        if cls:
            eq[cls] = eq.get(cls, 0) + n
    if uid == "tank_bde_1" and "tank_t55" in eq:          # 전차여단 기종 구성 (E1-3 기종)
        eq.pop("tank_t55")
        eq.update(ASSUMPTIONS["tank_split"])
    if uid.startswith(("inf_div", "mech_div")):
        eq["truck"] = ASSUMPTIONS["division_trucks"]
    if uid == "mech_div_1":
        eq["ifv_apc"] = eq.get("ifv_apc", 0) + ASSUMPTIONS["mech_ifv"]
    return {k: max(1, round(v * vis)) for k, v in eq.items()}


def build_opfor_scenario(directory: str | Path, branch_p5: bool = True, seed: int = 11) -> Scenario:
    d = Path(directory)
    orbat = yaml.safe_load((d / "orbat.yaml").read_text(encoding="utf-8"))
    events = yaml.safe_load((d / "scenario_events.yaml").read_text(encoding="utf-8"))
    rnd = random.Random(seed)
    vis = ASSUMPTIONS["visible_ratio"]
    tpls = orbat["echelon_templates"]

    units, xy = [], {}
    for u in orbat["units"]:
        uid = u["id"]
        x, y = _place(u.get("initial_zone", "defense_zone_2"), u.get("sector"), rnd)
        xy[uid] = (x, y)
        eq = _baseline(tpls.get(u.get("template")), uid, vis)
        if uid == "corps_arty_grp":
            eq["harts@unoccupied"] = ASSUMPTIONS["harts_sites"]                 # E0-2
        if uid == "missile_bn_1":
            eq["ugf_entrance@no_activity"] = 2                                   # E0-4
        units.append(Unit(UNIT_NAMES[uid], None if uid == "corps_hq" else UNIT_NAMES["corps_hq"], _xy_to_latlon(x, y),
                          "사령부" if uid == "corps_hq" else "주둔지", eq, True, TEMPLATE_TYPE.get(uid)))
    # E1-9 장거리 발사대: 전투서열 미등재 전략군 (식별 불가 상황)
    sx, sy = 30, MDL_Y_KM + 78
    units.append(Unit("미상 전략미사일부대", None, _xy_to_latlon(sx, sy), "기지", {}, False, None))
    xy["strategic"] = (sx, sy)

    def to(uid: str, zone: str, sector=None) -> tuple[float, float]:
        """부대 위치 → 목표 지역까지의 이동 [북, 동] km."""
        tx, ty = _place(zone, sector, rnd)
        x, y = xy[uid]
        return round(ty - y, 2), round(tx - x, 2)

    U = UNIT_NAMES
    acts: list[Activity] = []

    def add(ev, uid, eq, start, end, to_count, move=(0.0, 0.0), sensors=("EO", "SAR")):
        name = U.get(uid, "미상 전략미사일부대")
        acts.append(Activity(name, eq, start, end, to_count, move, tuple(sensors), ev))

    base = {u.name: u.equipment for u in units}
    # ── P1 전쟁 준비·위기 고조 (D-14 ~ D-1) ──
    for uid in ("inf_div_1", "inf_div_2", "mech_div_1"):                                       # E1-1
        mv = (-3.0, rnd.uniform(-1, 1))
        add("E1-1", uid, "truck", -14, -6, round(base[U[uid]]["truck"] * 1.4), mv)
        add("E1-1", uid, "march_column", -14, -6, 3, mv, ("SAR", "EO"))
        add("E1-1", uid, "camouflaged", -14, -6, 12, mv, ("SAR", "EO"))
    add("E1-2", "corps_arty_grp", "harts@occupied", -10, -4, ASSUMPTIONS["harts_sites"])        # E1-2
    add("E1-2", "corps_arty_grp", "harts@unoccupied", -10, -4, 0)
    add("E1-2", "corps_arty_grp", "truck", -10, -4, 20)
    add("E1-2", "corps_arty_grp", "aaa_zpu4", -10, -4, base[U["corps_arty_grp"]].get("aaa_zpu4", 0) + 8)
    mv_tank = to("tank_bde_1", "forward_assembly")                                             # E1-3 집결지(적 9~14 km)
    for eq, n in base[U["tank_bde_1"]].items():
        add("E1-3", "tank_bde_1", eq, -12, -6, n, mv_tank)
    mv_tel = to("missile_bn_1", "dispersed")                                                   # E1-4
    add("E1-4", "missile_bn_1", "ugf_entrance@activity", -8, -6, 2)
    add("E1-4", "missile_bn_1", "ugf_entrance@no_activity", -8, -6, 0)
    add("E1-4", "missile_bn_1", "tel_srbm", -7, -4, 4, mv_tel)
    add("E1-4", "missile_bn_1", "missile_support_vehicle", -7, -4, 6, mv_tel)
    add("E1-4", "missile_bn_1", "decoy_tel_srbm", -7, -4, 6, (mv_tel[0] + 3, mv_tel[1] - 4))
    add("E1-5", "deception_unit_1", "decoy_tank_t55", -10, -3, 24)                             # E1-5
    add("E1-5", "deception_unit_1", "decoy_towed_howitzer", -10, -3, 12)
    mv_sof = to("light_inf_bde_1", "forward_assembly")                                         # E1-6
    add("E1-6", "light_inf_bde_1", "aircraft_an2", -9, -4, 12, mv_sof, ("EO",))
    add("E1-6", "light_inf_bde_1", "helicopter_md500", -9, -4, 8, mv_sof, ("EO",))
    for eq, n in {"aaa_zpu4": 20, "aaa_zu23_2": 10, "spaag_zsu234": 6, "sam_launcher": 6, "radar": 4}.items():  # E1-7
        add("E1-7", "ad_units", eq, -10, -5, n)
    mv_br = to("engr_rgt_bridge", "security_zone")                                             # E1-8
    add("E1-8", "engr_rgt_bridge", "bridge_pmp", -5, -2, 4, mv_br, ("EO",))
    add("E1-8", "engr_rgt_bridge", "ferry_gsp_k61", -6, -2, 8, mv_br)
    add("E1-9", "strategic", "tel_strategic", -6, -4, 2)                                       # E1-9
    add("ECS-1", "light_inf_bde_1", "semi_submersible", -12, -6, 3, (-8.0, 25.0), ("SAR",))    # PCS 해상 침투
    # ── P2 개전·돌파 (H ~ H+24) ──
    add("E2-1", "corps_arty_grp", "artillery_position", -1, 0, 8)
    add("E2-2", "inf_div_1", "formation_wedge_v_line", 0, 1, 6, (-6.0, 0), ("SAR",))
    add("E2-2", "inf_div_2", "formation_wedge_v_line", 0, 1, 6, (-6.0, 0), ("SAR",))
    add("E2-3", "light_inf_bde_1", "tunnel_exit", -1, 0, 2, (-14.0, 0), ("EO",))
    add("E2-4", "light_inf_bde_1", "helicopter_md500", 0, 1, 18, (0, 0), ("EO",))
    add("E2-6", "ew_co_1", "ew_vehicle", -1, 0, 6)
    # ── P5 전술핵 위협 분기 ──
    if branch_p5:
        add("E5-1", "missile_bn_1", "tel_srbm", 1, 2, 8, (-4.0, 2.0), ("SAR",))
        add("E5-2", "missile_bn_1", "uav", 1, 2, 4, (0, 0), ("EO",))
        add("E5-3", "missile_bn_1", "decoy_tel_srbm", 1, 2, 14, (0, 0), ("SAR", "EO"))
        add("E5-4", "nbc_bn_1", "truck", 1, 3, 15)

    env = events.get("environment", {})
    return Scenario(
        id="opfor_corps", name="대항군 군단 연속 시나리오", area_id="SIM-OPFOR",
        truth={"intent": "공격(개전)" + (" + 전술핵 위협 분기" if branch_p5 else ""), "hypothesis": "공격 준비",
               "d_day": "D일 = H시 개전", "warning_start": -14,
               "readiness": {"watchcon": [[-30, 3], [-14, 2], [-3, 1]], "defcon": [[-30, 4], [-3, 3], [0, 2]], "jindogae": [[-30, 3], [-9, 2], [0, 1]],
                             "durumi": [[-30, 3], [-9, 2], [0, 1]]}},
        period=(-45, 3), assess=(-30, 3),
        noise={"p_observe": 0.9, "adverse_weather_share": env.get("adverse_weather_share", 0.3), "count_rel_sd": 0.10,
               "p_miss": 0.05, "false_positive_rate": 0.02, "position_sd_m": 200,
               "p_decoy_recognized": 0.8, "p_detect_camouflaged": 0.6,
               "fp_equipment": ["truck", "tank_t55", "towed_howitzer", "ifv_apc"]},
        units=units, activities=acts, reports=[],
        meta={"source": str(d), "assumptions": ASSUMPTIONS, "branch_p5": branch_p5,
              "phases": events.get("phases", []), "test_cases": str(d / "test_cases.yaml")},
    )
