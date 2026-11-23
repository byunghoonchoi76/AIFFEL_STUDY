from app.analysis.weather import effects, interpret, load_rules

R = load_rules("config/weather_effects.yaml")


def lv(obs):
    return {e.domain: e.level for e in effects(obs, R)}


def test_cloud_blocks_eo_and_low_ceiling_blocks_air():
    x = lv({"cloud_pct": 90, "ceiling_m": 200, "visibility_km": 1.0})
    assert x["eo_imaging"] == "불가" and x["air_ops"] == "불가" and x["sar_imaging"] == "양호"


def test_frozen_ground_and_river_is_favorable_window():
    w = {"ground": "frozen", "river": "frozen"}
    it = interpret(effects(w, R), [effects(w, R)] * 3, R)
    assert [f["key"] for f in it["findings"]] == ["favorable_attack"]


def test_mud_or_flood_is_unfavorable_and_normal_river_is_neutral():
    it = interpret(effects({"ground": "mud", "river": "normal"}, R), [], R)
    assert [f["key"] for f in it["findings"]] == ["unfavorable_attack"]
    it2 = interpret(effects({"ground": "firm", "river": "normal"}, R), [], R)
    assert it2["findings"] == []


def test_collection_limit_suggests_sar():
    it = interpret(effects({"cloud_pct": 95, "precip_mm": 3}, R), [], R)
    assert it["collection_limits"] and "SAR" in it["collection_limits"][0]
