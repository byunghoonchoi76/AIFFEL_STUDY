"""장비 계수 → 부대 유형·제대 추정 (test_cases M2-echelon, T-ORG-1/2).
편제 템플릿(scenarios/opfor_corps/orbat.yaml, ATP 7-100.2 3장)을 영상으로 보이는 장비 그룹 벡터로 바꿔
관측 그룹 비율과의 코사인 유사도로 유형을 고르고, 총량 비율로 편제 대비 규모를 추정."""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml

from ..taxonomy import group_of

# 편제표 품목명 → 영상 분류 그룹 (영상으로 식별 어려운 개인화기·소화기 등은 제외)
ITEM_GROUP = {
    "tank_t55_t62": "main_battle_tank", "light_tank": "light_armor", "ifv_apc": "light_armor", "recon_brdm2": "light_armor",
    "howitzer_152mm": "artillery", "howitzer_122mm": "artillery", "sp_howitzer_152mm": "artillery",
    "sp_howitzer_122mm": "artillery", "sp_gun_170": "artillery", "mrl_107mm": "artillery", "mrl_240mm": "artillery",
    "mortar_120mm": "artillery", "aaa_s60_57mm": "air_defense", "aaa_37mm": "air_defense", "aaa_zpu4": "air_defense",
    "aaa_14_5mm": "air_defense", "aaa_57mm": "air_defense", "aaa_57mm_type80_zsu57": "air_defense",
    "aaa_vehicle_m1983_btr152": "air_defense", "radar_firecan": "air_defense", "truck_2_5t": "logistics",
}
GROUPS = ["main_battle_tank", "light_armor", "artillery", "air_defense"]   # 유형 판별 축 (트럭은 편제표 누락이 많아 제외)
TYPE_INFO = {"infantry_division": ("보병사단", "사단"), "mech_division": ("기계화보병사단", "사단"),
             "tank_brigade": ("전차여단", "여단"), "infantry_regiment": ("보병연대", "연대"),
             "corps_artillery_group": ("군단 포병집단", "여단")}
# 시험 설계 가정: 기계화보병사단 = 보병사단 + 보병전투차·장갑차 279대(9개 대대 × 31대). 편제표에 수량이 없어 가정값 사용
MECH_IFV_ASSUMED = 279


@lru_cache
def templates(path: str = str(Path(__file__).resolve().parents[2] / "scenarios/opfor_corps/orbat.yaml")) -> dict[str, dict[str, float]]:
    """편제 템플릿. 파일이 없으면 빈 사전(제대 추정 생략) — 평가 자체는 계속 진행."""
    if not Path(path).exists():
        return {}
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["echelon_templates"]
    out = {}
    for name, t in raw.items():
        vec = {g: 0.0 for g in GROUPS}
        for item, n in (t.get("equipment") or {}).items():
            g = ITEM_GROUP.get(item)
            if g in vec:
                vec[g] += float(n)
        if sum(vec.values()) > 0:
            out[name] = vec
    if "infantry_division" in out:
        mech = dict(out["infantry_division"])
        mech["light_armor"] += MECH_IFV_ASSUMED
        out["mech_division"] = mech
    return out


def counts_to_groups(counts: dict[str, float]) -> dict[str, float]:
    vec = {g: 0.0 for g in GROUPS}
    for label, n in counts.items():
        g = group_of(label)
        if g in vec:
            vec[g] += float(n)
    return vec


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    dot = sum(a[g] * b[g] for g in GROUPS)
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def estimate(counts: dict[str, float]) -> dict | None:
    """counts: 표적 라벨 → 관측 수량. 반환: 추정 유형·제대·유사도·편제 대비 규모·후보 순위."""
    obs = counts_to_groups(counts)
    if sum(obs.values()) < 5 or not templates():
        return None
    total = sum(obs.values())
    ranked = sorted(((_cos(obs, v), k) for k, v in templates().items()), reverse=True)
    # 구성비가 거의 같은 후보(예: 보병사단·보병연대)는 총량이 편제 규모에 더 가까운 쪽 선택
    close = [(s, k) for s, k in ranked if ranked[0][0] - s <= 0.03]
    sim, best = min(close, key=lambda sk: abs(math.log(max(total, 1) / sum(templates()[sk[1]].values()))))
    scale = total / sum(templates()[best].values())
    name, echelon = TYPE_INFO.get(best, (best, None))
    return {"type": best, "type_ko": name, "echelon": echelon, "similarity": round(sim, 3),
            "strength_ratio": round(scale, 2), "margin": round(sim - max((x for x, k in ranked if k != best), default=0.0), 3),
            "candidates": [{"type": k, "similarity": round(s, 3)} for s, k in ranked[:3]]}
