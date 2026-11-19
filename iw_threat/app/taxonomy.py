"""탐지 대상 분류 체계 (config/classes.yaml).
표적 라벨 형식:  <class_id>            예) tank_pokpung
               <class_id>@<state>    예) harts@occupied, ugf_entrance@activity
               decoy_<class_id>      예) decoy_tel_srbm (탐지기가 기만체로 판별한 표적)
기존 단순 코드(tank, apc, truck …)도 그대로 지원."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

LEGACY_KO = {
    "tank": "전차", "ifv": "보병전투차", "apc": "장갑차", "truck": "트럭", "fuel_truck": "유류차",
    "artillery": "야포", "mlrs": "방사포", "bridging": "도하장비", "engineer": "공병장비",
    "field_hospital": "야전병원", "command_vehicle": "지휘차량", "sam": "지대공미사일", "aaa": "대공포",
    "sof_vehicle": "특수작전부대 차량", "submarine": "잠수함정", "hovercraft": "공기부양정", "fighter": "전투기",
}
LEGACY_GROUP = {"tank": "main_battle_tank", "ifv": "light_armor", "apc": "light_armor", "truck": "logistics",
                "fuel_truck": "logistics", "artillery": "artillery", "mlrs": "artillery", "bridging": "engineer",
                "engineer": "engineer", "sam": "air_defense", "aaa": "air_defense", "fighter": "aviation"}
STATE_KO = {"occupied": "점유", "unoccupied": "비점유", "activity": "활동", "no_activity": "활동 없음"}
SHORT_KO = {"tank_t55": "T-55 계열 전차", "tank_t62_chonma": "천마 전차", "tank_pokpung": "폭풍 전차",
            "tank_songun": "선군 전차", "tank_new_gen": "신형 전차", "truck": "트럭"}


@lru_cache
def classes(path: str = str(Path(__file__).resolve().parents[1] / "config/classes.yaml")) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    return {c["id"]: c for c in yaml.safe_load(p.read_text(encoding="utf-8"))["classes"]}


def parse_label(label: str) -> tuple[str, str | None, bool]:
    """→ (기본 class id, 상태, 기만체 여부)"""
    decoy = label.startswith("decoy_")
    base = label[len("decoy_"):] if decoy else label
    state = None
    if "@" in base:
        base, state = base.split("@", 1)
    return base, state, decoy


def group_of(label: str) -> str | None:
    base, _, decoy = parse_label(label)
    if decoy:
        return "deception"
    c = classes().get(base)
    return (c.get("group") or c.get("tier")) if c else LEGACY_GROUP.get(base)


def name_ko(label: str) -> str:
    base, state, decoy = parse_label(label)
    c = classes().get(base)
    name = SHORT_KO.get(base) or (c["name_ko"].split(" (")[0] if c else LEGACY_KO.get(base, base))
    if state:
        name += f"({STATE_KO.get(state, state)})"
    return f"기만체[{name}]" if decoy else name


def match_keys(label: str) -> set[str]:
    """지표의 equipment 목록과 대조할 키: 라벨 자체, 기본 id, 'id@상태', 'group:그룹'."""
    base, state, decoy = parse_label(label)
    if decoy:
        return {label, "group:deception"}
    keys = {label, base}
    if state:
        keys.add(f"{base}@{state}")
    g = group_of(label)
    if g:
        keys.add(f"group:{g}")
    return keys
