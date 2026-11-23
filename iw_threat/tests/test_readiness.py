from pathlib import Path

from app.analysis.readiness import Observation, load_readiness, recommend_all

ROOT = Path(__file__).resolve().parents[1]
TRACKS, BASIS = load_readiness(ROOT / "config/readiness.yaml")
WC = next(t for t in TRACKS if t.id == "watchcon")


def ob(day, attack=0.0, conf="중", inds=(), infil=0.0, air=0.0):
    return Observation(day, {"H_ATTACK": attack, "H_EXERCISE": 0.02, "H_INFILTRATION": infil, "H_AIR": air}, conf, set(inds))


def levels(series):
    return [x["level"] for x in series]


def test_escalate_immediately_downgrade_after_hold():
    s = recommend_all(TRACKS, [ob(0, 0.1), ob(2, 0.62), ob(4, 0.41), ob(6, 0.80), ob(8, 0.2), ob(10, 0.2), ob(12, 0.2),
                               ob(14, 0.2), ob(16, 0.2)])["watchcon"]
    # 0.62→2 상향 조건, 0.41(일시 하락)에도 유지, 0.80→1 상향 조건, 이후 낮음: 4일 지속마다 한 단계씩 하향
    assert levels(s) == [3, 2, 2, 1, 1, 1, 2, 2, 3]
    assert s[1]["action"] == "상향 조건" and s[2]["action"] == "유지" and "하향 조건" in s[2]["note"]
    assert s[6]["action"] == "하향 조건"


def test_low_confidence_holds_escalation_and_requests_collection():
    s = recommend_all(TRACKS, [ob(0, 0.9, conf="하")])["watchcon"]
    assert s[0]["level"] == 2 and s[0]["collect_more"] and "정보 수집 강화" in s[0]["note"]   # 1단계는 신뢰도 '중' 필요


def test_decisive_indicators_and_defcon_condition_track():
    s = recommend_all(TRACKS, [ob(0, 0.2, inds=("I_BRIDGING", "I_MEDICAL"))])["watchcon"]
    assert s[0]["level"] == 2 and "결정적 징후" in s[0]["reasons"][0]
    s2 = recommend_all(TRACKS, [ob(0, 0.9, conf="상")])["watchcon"]
    assert s2[0]["level"] == 1
    r = recommend_all(TRACKS, [ob(0, 0.9, conf="상"), ob(2, 0.98, conf="상"), ob(4, 0.5, conf="중", inds=("I_ASSAULT",))])["defcon"]
    assert levels(r) == [3, 2, 2]          # 평시 4 → 긴장 고조 조건 3 → 전쟁 임박 조건 2 (공격대형 결정적 징후로 유지)
    assert recommend_all(TRACKS, [ob(0, 0.8, conf="중")])["defcon"][0]["level"] == 4   # 신뢰도 '상' 미만이면 평시 조건


def test_tracks_follow_their_own_hypotheses():
    r = recommend_all(TRACKS, [ob(0, attack=0.9, conf="상", infil=0.05, air=0.3)])
    assert r["watchcon"][0]["level_name"] == "워치콘 1"
    assert r["jindogae"][0]["level_name"] == "진돗개 셋"            # 침투 위협 낮음
    assert r["durumi"][0]["level"] == 2                              # 항공 위협 0.3 ≥ 0.2
    r2 = recommend_all(TRACKS, [ob(0, inds=("I_INFIL_SIGN",))])
    assert r2["jindogae"][0]["level_name"] == "진돗개 하나"          # 침투 흔적 = 결정적 징후
