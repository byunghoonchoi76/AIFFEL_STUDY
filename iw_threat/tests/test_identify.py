from app.orbat.identify import affinity, best_match, haversine_m, radius_for, rank_candidates


def test_radius_by_echelon_and_precision():
    assert radius_for("사단") == 10000 and radius_for("대대", 100) == 1600 and radius_for(None) == 3000


def test_affinity_prefers_matching_branch_at_similar_distance():
    cands = [{"unit_key": "a", "name": "제1가상보병연대", "echelon": "연대", "dist_m": 1000, "radius_m": 3000},
             {"unit_key": "b", "name": "제2가상전차여단", "echelon": "여단", "dist_m": 1400, "radius_m": 5000}]
    assert best_match(cands, "tank")["unit_key"] == "b"          # 전차 ↔ 전차여단
    assert best_match(cands, "artillery")["unit_key"] == "b"     # 친화 없음 → 거리/반경 비율 낮은 쪽(0.28 < 0.33)
    assert affinity("artillery", "제3가상포병연대") and not affinity(None, "제3가상포병연대")


def test_outside_radius_is_unidentified():
    cands = [{"unit_key": "a", "name": "제1가상공병대대", "echelon": "대대", "dist_m": 4000, "radius_m": 1500}]
    assert best_match(cands, "bridging") is None
    assert rank_candidates(cands, "bridging")[0]["within"] is False


def test_haversine():
    assert abs(haversine_m(38.0, 127.0, 38.01, 127.0) - 1112) < 5
