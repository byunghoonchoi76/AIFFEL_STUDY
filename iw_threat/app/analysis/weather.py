"""전장정보분석 — 기상이 작전에 미치는 영향 (순수 함수).
관측·예보값 → 영역별 영향(양호/제한/불가)·사유 → 적 작전 여건 해석·아군 관측 제한사항."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

LEVELS = {"양호": 0, "제한": 1, "불가": 2}
DEFAULTS = {"cloud_pct": 0, "precip_mm": 0.0, "temp_c": 10.0, "visibility_km": 10.0, "ceiling_m": 3000,
            "wind_ms": 3.0, "ground": "firm", "river": "normal", "snow_cm": 0, "sea_state": 2, "wind_dir_deg": -1}   # 풍향 미상 = -1


@dataclass
class Effect:
    domain: str
    name: str
    level: str
    reason: str


def load_rules(path: str | Path = "config/weather_effects.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _eval(expr: str, env: dict) -> bool:
    return bool(eval(expr, {"__builtins__": {}}, env))  # noqa: S307 — 설정 파일(관리자 작성) 조건식만 평가


def effects(obs: dict, rules: dict) -> list[Effect]:
    """기상 1건 → 영역별 영향. 규칙은 위에서부터 처음 일치하는 것 적용, 없으면 양호."""
    env = {**DEFAULTS, **{k: v for k, v in obs.items() if v is not None}}
    out = []
    for dom, spec in rules["domains"].items():
        hit = next((r for r in spec["rules"] if _eval(r["when"], env)), None)
        out.append(Effect(dom, spec["name"], hit["level"] if hit else "양호",
                          hit["reason"].format(**env) if hit else "제한 요인 없음"))
    return out


def worst(effs_by_time: list[list[Effect]]) -> dict[str, Effect]:
    """기간 중 영역별 최악 영향."""
    out: dict[str, Effect] = {}
    for effs in effs_by_time:
        for e in effs:
            if e.domain not in out or LEVELS[e.level] > LEVELS[out[e.domain].level]:
                out[e.domain] = e
    return out


def interpret(current: list[Effect], outlook: list[list[Effect]], rules: dict) -> dict:
    """적 작전 여건 해석(근거 후보)·아군 관측 제한사항."""
    a = rules.get("assessment", {})
    days = a.get("outlook_days", 3)
    period = [current] + outlook
    w = worst(period)
    best_each = [{e.domain: e.level for e in effs} for effs in period]
    findings = []
    fav = a.get("favorable_attack")
    if fav and period and all(all(b.get(d) in lv for d, lv in fav["require"].items()) for b in best_each):
        findings.append({"key": "favorable_attack", "text": fav["text"].format(days=days)})
    unf = a.get("unfavorable_attack")
    if unf and any(w[d].level in lv for d, lv in unf["any"].items() if d in w):
        limits = ", ".join(w[d].reason for d, lv in unf["any"].items() if d in w and w[d].level in lv)
        findings.append({"key": "unfavorable_attack", "text": unf["text"].format(days=days, limits=limits)})
    cov = a.get("infiltration_cover")
    cur = {e.domain: e for e in current}
    if cov and any(d in cur and cur[d].level in lv for d, lv in cov["any"].items()):
        findings.append({"key": "infiltration_cover", "text": cov["text"]})
    collection = []
    if cur.get("eo_imaging") and cur["eo_imaging"].level != "양호":
        collection.append(f"광학 관측 {cur['eo_imaging'].level}: {cur['eo_imaging'].reason} → SAR 촬영 요청 건의")
    return {"findings": findings, "collection_limits": collection}
