from datetime import datetime, timedelta, timezone

from app.analysis.change import Change
from app.analysis.commander import KeyTarget, ccir_alerts, load_requirements, pir_answers, target_activity

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
REQ = load_requirements("config/pir_ccir.yaml")


def ch(unit, eq, base, recent):
    return Change(eq, base, recent, recent - base, T0, [1, 2], unit_key=unit, unit_name=unit, baseline_obs=10, baseline_sd=1.0)


def test_unit_and_location_matching():
    kts = [KeyTarget("K1", "포병 장사정포", "핵심표적", "합참", unit_name="포병연대", equipment=["artillery"], pir_ids=["PIR-J2"]),
           KeyTarget("K2", "TEL 기지", "핵심표적", "공동", lat=39.0, lon=126.0, radius_m=1500, equipment=["tel_srbm"],
                     hypotheses=["H_NUCLEAR"])]
    pts = [{"id": i, "lat": 39.001, "lon": 126.001, "equipment_type": "tel_srbm", "count": c,
            "observed_at": T0 - timedelta(days=d)} for i, (d, c) in enumerate([(20, 2), (15, 2), (10, 2), (2, 5)])]
    acts = target_activity(kts, [ch("포병연대", "artillery", 6, 16), ch("전차여단", "tank", 10, 30)], pts, T0)
    assert {a["target_id"]: a["indicator"] for a in acts} == {"K1": "I_KEY_TARGET", "K2": "I_KEY_TARGET_WMD"}


def test_pir_status_and_ccir():
    hyps = [{"hypothesis_id": "H_ATTACK", "probability": 0.8, "contributions": [
        {"indicator_id": "I_ARMOR_MASS", "name": "기갑 집결", "strength": 0.7, "delta_logit": 1.2}]},
        {"hypothesis_id": "H_NUCLEAR", "probability": 0.01, "contributions": []}]
    acts = [{"target_id": "K1", "name": "포병 장사정포", "category": "핵심표적", "indicator": "I_KEY_TARGET", "pir_ids": ["PIR-J2"]}]
    pirs = {p["id"]: p["status"] for p in pir_answers(REQ, hyps, acts)}
    assert pirs["PIR-J1"] == "확인" and pirs["PIR-J2"] == "징후 있음" and pirs["PIR-J3"] == "미식별"
    alerts = {a["id"] for a in ccir_alerts(REQ, pir_answers(REQ, hyps, acts), acts)}
    assert {"CCIR-J1", "CCIR-J2"} <= alerts and "CCIR-J3" not in alerts
