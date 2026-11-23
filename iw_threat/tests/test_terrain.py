from app.analysis.terrain import advance_days, facility_vulnerability, load_terrain

CFG = {"advance_rate_km_per_day": {"open": 30, "mountain": 10, "urban": 3}, "urban_bypass_factor": 1.3,
       "delivery_range_km": {"artillery": 30, "mlrs": 60}, "chem_downwind_deg": 30,
       "urban_areas": [{"name": "도시", "lat": 38.0, "lon": 127.0, "radius_km": 5}],
       "facilities": [{"id": "F1", "name": "시설", "kind": "국가중요시설", "grade": "가", "lat": 38.0, "lon": 127.0}]}


def test_urban_slows_direct_advance_and_bypass_is_faster():
    a = advance_days((38.3, 127.0), (38.0, 127.0), CFG)
    assert a["urban_share"] > 0 and a["direct_days"] > a["bypass_days"]


def test_fires_in_range_downwind_gives_high_vulnerability():
    pts = [{"lat": 38.2, "lon": 127.0, "equipment_type": "mlrs", "count": 6},
           {"lat": 38.3, "lon": 127.0, "equipment_type": "tank", "count": 20}]
    r = facility_vulnerability(pts, CFG, {"wind_dir_deg": 0}, chem_favorable=True)["facilities"][0]
    assert r["fires_in_range"] == 1 and r["downwind"] and r["vulnerability"] == "상" and r["urban"] == "도시"
    r2 = facility_vulnerability(pts, CFG, {"wind_dir_deg": 180}, chem_favorable=True)["facilities"][0]
    assert not r2["downwind"] and r2["vulnerability"] == "중"


def test_config_loads():
    assert load_terrain("config/terrain_facilities.yaml")["facilities"]
