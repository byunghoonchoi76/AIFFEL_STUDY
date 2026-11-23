from datetime import datetime, timezone

from app.analysis.catalog import load_catalog, load_grading
from app.analysis.change import Change
from app.analysis.matching import match_imagery
from app.analysis.patterns import case_similarity, commander_briefing, load_cases

CAT, GR = load_catalog("config/indicators.yaml"), load_grading("config/source_grading.yaml")
T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)


def h(hid, name, prob, contribs):
    return {"hypothesis_id": hid, "name": name, "probability": prob, "threat": prob, "contributions": contribs}


def c(iid, s, d=1.0):
    return {"indicator_id": iid, "name": iid, "strength": s, "delta_logit": d}


def test_case_similarity_ranks_2015_pattern_first_for_local_fire_signals():
    hyps = [h("H_LOCAL_FIRE", "국지 포격 도발 준비", 0.8, [c("I_ULTIMATUM", 0.7), c("I_HARTS_OCC", 0.6), c("I_SUB_SORTIE", 0.6)])]
    sims = case_similarity(hyps, load_cases("config/reference_cases.yaml"))
    assert sims[0]["id"] == "CASE-2015-08" and sims[0]["similarity"] > 0.3


def test_submarine_departure_detected_as_decrease():
    ch = Change("submarine", 18, 18, 0, T0, [1, 2, 3], baseline_obs=10, baseline_sd=1.0, recent_mean=6.0, recent_n=3)
    ev = [e for e in match_imagery([ch], CAT, GR) if e.indicator_id == "I_SUB_SORTIE"]
    assert ev and "기지 이탈률 67%" in ev[0].quote


def test_briefing_has_three_lines_with_computed_numbers():
    res = {"hypotheses": [h("H_ATTACK", "공격 준비", 0.83, [c("I_ARMOR_MASS", 0.7)])], "grade": "심각", "confidence": "중",
           "patterns": [{"name": "사례", "similarity": 0.5}], "commander_requirements": {"ccir_alerts": []}, "weather": {}}
    b = commander_briefing(res)
    assert len(b) == 3 and "83%" in b[0] and "유사도 50%" in b[1]
