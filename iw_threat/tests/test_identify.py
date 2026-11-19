from app.orbat.identify import affinity, best_match, haversine_m, radius_for, rank_candidates


def test_radius_by_echelon_and_precision():
    assert radius_for("사단") == 10000 and radius_for("대대", 100) == 1600 and radius_for(None) == 3000


def test_affinity_prefers_matching_branch_at_similar_distance():
    cands = [{"unit_key": "a", "name": "제1가상보병연대", "echelon": "연대", "dist_m": 1000, "radius_m": 3000},
             {"unit_key": "b", "name": "제2가상전차여단", "echelon": "여단", "dist_m": 1400, "radius_m": 5000}]
    assert best_match(cands, "tank")["unit_key"] == "b"          # 전차 ↔ 전차여단
    assert best_match(cands, "artillery")["unit_key"] == "a"     # 친화 없음 → 더 가까운 부대
    assert affinity("artillery", "제3가상포병연대") and not affinity(None, "제3가상포병연대")


def test_outside_radius_is_unidentified():
    cands = [{"unit_key": "a", "name": "제1가상공병대대", "echelon": "대대", "dist_m": 4000, "radius_m": 1500}]
    assert best_match(cands, "bridging") is None
    assert rank_candidates(cands, "bridging")[0]["within"] is False


def test_haversine():
    assert abs(haversine_m(38.0, 127.0, 38.01, 127.0) - 1112) < 5


def test_large_radius_parent_does_not_steal_nearby_subunit():
    """사단(반경 10km) 사령부가 0.87km, 의무대대(반경 1.5km)가 0.4km — 가까운 하급부대로 식별."""
    cands = [{"unit_key": "div", "name": "제1가상보병사단", "echelon": "사단", "dist_m": 870, "radius_m": 10000},
             {"unit_key": "med", "name": "제6가상의무대대", "echelon": "대대", "dist_m": 400, "radius_m": 1500}]
    assert best_match(cands, "field_hospital")["unit_key"] == "med"
    cands2 = [{"unit_key": "div", "name": "제1가상보병사단", "echelon": "사단", "dist_m": 4930, "radius_m": 10000},
              {"unit_key": "bde", "name": "제2가상전차여단", "echelon": "여단", "dist_m": 2560, "radius_m": 5000}]
    assert best_match(cands2, "apc")["unit_key"] == "bde"
