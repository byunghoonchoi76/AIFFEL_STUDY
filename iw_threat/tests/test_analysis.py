import io
import math
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.analysis.catalog import load_catalog, load_grading
from app.analysis.change import TargetRow, detect_changes, detect_unit_changes
from app.analysis.explain import (build_evidence_map, indicator_lines, template_summary, verify_implications,
                                  verify_summary)
from app.analysis.matching import ReportChunk, hits_to_evidence, match_imagery, quote_in_text
from app.analysis.scoring import Evidence, assess, evidence_weight, indicator_strength
from app.ingest.chunk import chunk_text
from app.ingest.parse import parse_hwpx

ROOT = Path(__file__).resolve().parents[1]
CAT = load_catalog(ROOT / "config/indicators.yaml")
GR = load_grading(ROOT / "config/source_grading.yaml")
NOW = datetime(2026, 11, 18, 12, 0)


def ev(iid, stype="IMINT", key="s1", rel="B", cred=2, days_ago=0.0):
    return Evidence(iid, stype, key, rel, cred, NOW - timedelta(days=days_ago), ref=f"r-{iid}-{key}")


def test_weight_decays_with_age():
    fresh = evidence_weight(ev("I_ARMOR_MASS"), GR, NOW, tau_days=7)
    old = evidence_weight(ev("I_ARMOR_MASS", days_ago=7), GR, NOW, tau_days=7)
    assert fresh == pytest.approx(0.85 * 0.85)
    assert old == pytest.approx(fresh * math.exp(-1))


def test_same_source_not_double_counted_but_independent_types_combine():
    assert indicator_strength([("IMINT", "s1", 0.6), ("IMINT", "s1", 0.6)]) == pytest.approx(0.6)
    assert indicator_strength([("IMINT", "s1", 0.6), ("OSINT", "s2", 0.5)]) == pytest.approx(1 - 0.4 * 0.5)


def test_no_evidence_returns_prior_and_lowest_grade():
    r = assess(CAT, [], GR, NOW)
    assert {h.hypothesis_id: h.probability for h in r.hypotheses}["H_ATTACK"] == pytest.approx(0.02)
    assert r.grade == "관심" and r.confidence == "하"


def test_multi_indicator_multi_source_raises_attack_above_exercise():
    evidence = [
        ev("I_ARMOR_MASS"), ev("I_BRIDGING"), ev("I_ARTY_POS"), ev("I_MEDICAL"),
        ev("I_LOGISTICS", stype="OSINT", key="news", rel="C", cred=3),
        ev("I_COMMAND_POST", stype="HUMINT", key="h1", rel="C", cred=2),
    ]
    r = assess(CAT, evidence, GR, NOW)
    assert r.top.hypothesis_id == "H_ATTACK"
    assert r.top.probability > 0.5
    assert r.grade in {"경계", "심각"}
    assert r.confidence == "상"
    assert r.top.contributions[0].delta_logit >= r.top.contributions[-1].delta_logit


def test_future_evidence_ignored():
    future = Evidence("I_BRIDGING", "IMINT", "s1", "A", 1, NOW + timedelta(days=1), "x")
    r = assess(CAT, [future], GR, NOW)
    assert all(not h.contributions for h in r.hypotheses)


def test_detect_changes_and_imagery_match():
    rows = [TargetRow(1, "tank", NOW - timedelta(days=20), 10),
            TargetRow(2, "tank", NOW - timedelta(days=15), 12),
            TargetRow(3, "tank", NOW - timedelta(days=2), 30),
            TargetRow(4, "truck", NOW - timedelta(days=1), 25)]
    ch = {c.equipment_type: c for c in detect_changes(rows, NOW, 7, 30)}
    assert ch["tank"].delta == pytest.approx(19) and ch["tank"].refs == [3]
    assert ch["truck"].baseline_level == 0.0
    ids = {e.indicator_id for e in match_imagery(list(ch.values()), CAT, GR)}
    assert ids == {"I_ARMOR_MASS", "I_LOGISTICS"}


def test_llm_hits_require_verbatim_quote_and_known_indicator():
    chunk = ReportChunk("d1:0", "현지 주민에 따르면 야전병원 천막 3동이 새로 설치되었다.", "HUMINT", "src9", "C", 3, NOW)
    hits = [{"indicator_id": "I_MEDICAL", "quote": "야전병원 천막 3동이 새로 설치되었다"},
            {"indicator_id": "I_MEDICAL", "quote": "야전병원 10동 설치"},          # 원문에 없음
            {"indicator_id": "I_UNKNOWN", "quote": "야전병원 천막"}]               # 목록에 없는 지표
    out = hits_to_evidence(chunk, hits, CAT)
    assert len(out) == 1 and out[0].source_type == "HUMINT" and out[0].ref == "chunk:d1:0"
    assert quote_in_text("천막  3동이\n새로", chunk.text)


def test_implications_verified_and_lines_keep_exact_facts():
    evidence = [Evidence("I_MEDICAL", "IMINT", "s", "B", 2, NOW, "r1",
                         quote="영상 판독 야전병원 기준기간 평균 0대 → 최근 2대 (+2대)"),
                Evidence("I_MEDICAL", "OSINT", "n", "C", 3, NOW, "chunk:d1:0", quote="천막 3동이 새로 설치되었다"),
                Evidence("I_BRIDGING", "IMINT", "s", "B", 2, NOW, "r2",
                         quote="영상 판독 도하장비 기준기간 평균 0대 → 최근 4대 (+4대)")]
    r = assess(CAT, evidence, GR, NOW)
    m = build_evidence_map(r, evidence)
    ok, dropped = verify_implications(
        [{"indicator_id": "I_MEDICAL", "text": "I_MEDICAL: 의무지원 준비 정황"},
         {"indicator_id": "I_BRIDGING", "text": "도하장비 10대로 증가"},          # 숫자 → 제거(사실은 정형 문장 담당)
         {"indicator_id": "I_UNKNOWN", "text": "모르는 지표"}], m)
    assert ok == {"I_MEDICAL": "의무지원 준비 정황"} and len(dropped) == 2
    names = {"I_BRIDGING": "도하·공병 장비 전방 이동"}
    ok2, dr2 = verify_implications([{"indicator_id": "I_BRIDGING", "text": "도하·공병 장비 전방 이동"}], m, names)
    assert not ok2 and dr2[0]["_reason"] == "지표 이름 되풀이"
    names3 = {"I_MEDICAL": "야전 의무시설 전개", "I_BRIDGING": "도하·공병 장비 전방 이동"}
    ok3, dr3 = verify_implications([{"indicator_id": "I_MEDICAL", "text": "야전 의무시설 전개로 부상자 처리 능력 강화"},
                                    {"indicator_id": "I_BRIDGING", "text": "도하·공병 장비 이동으로 진입 경로 확보"}], m, names3)
    assert ok3 == {"I_MEDICAL": "부상자 처리 능력 강화", "I_BRIDGING": "진입 경로 확보"} and not dr3
    meanings = {i: d.meaning for i, d in CAT.indicator_defs().items()}
    fb = {x["indicator_id"]: x for x in indicator_lines(r, m, {}, meanings)}
    assert fb["I_BRIDGING"]["text"].endswith("→ 하천 도하 공격 대비 정황") and fb["I_BRIDGING"]["generated"] == "template"
    lines = {x["indicator_id"]: x for x in indicator_lines(r, m, ok)}
    med = lines["I_MEDICAL"]
    assert "영상: 야전병원 0→2대" in med["text"] and "OSINT(C3)" in med["text"] and med["text"].endswith("→ 의무지원 준비 정황")
    assert med["generated"] == "template+llm" and med["citations"] == ["r1", "chunk:d1:0"]
    assert lines["I_BRIDGING"]["generated"] == "template" and "식별됨 — 영상: 도하장비 0→4대" in lines["I_BRIDGING"]["text"]


def test_evidence_map_only_top_hypothesis_indicators():
    evidence = [ev("I_MEDICAL"), ev("I_EX_ANNOUNCED", stype="OSINT", key="n")]
    evidence = [Evidence(e.indicator_id, e.source_type, e.source_key, e.reliability, e.credibility,
                         e.observed_at, e.ref, quote="q") for e in evidence]
    r = assess(CAT, evidence, GR, NOW)
    m = build_evidence_map(r, evidence)
    assert set(m) == {c.indicator_id for c in r.top.contributions}


def test_chunk_respects_max_len():
    text = "\n".join(["가" * 300] * 10)
    chunks = chunk_text(text, max_chars=800, overlap=100)
    assert all(len(c) <= 800 + 100 for c in chunks) and len(chunks) >= 3


def test_parse_hwpx(tmp_path):
    xml = ('<hs:sec xmlns:hs="s" xmlns:hp="p"><hp:p><hp:run><hp:t>첫 문단</hp:t></hp:run></hp:p>'
           '<hp:p><hp:run><hp:t>둘&amp;셋</hp:t></hp:run></hp:p></hs:sec>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Contents/section0.xml", xml)
    p = tmp_path / "a.hwpx"
    p.write_bytes(buf.getvalue())
    assert parse_hwpx(p) == "첫 문단\n둘&셋"


def test_summary_verification_blocks_changed_probability():
    evidence = [Evidence("I_MEDICAL", "IMINT", "s", "B", 2, NOW, "r1", quote="영상 판독 야전병원 0대 → 2대"),
                Evidence("I_BRIDGING", "IMINT", "s", "B", 2, NOW, "r2", quote="영상 판독 도하장비 0대 → 4대")]
    r = assess(CAT, evidence, GR, NOW)
    m = build_evidence_map(r, evidence)
    pct = round(r.top.probability * 100)
    ids = [m["I_MEDICAL"][0]["id"], m["I_BRIDGING"][0]["id"]]
    ok = verify_summary({"text": f"야전병원·도하장비 증가로 공격 준비 가능성 {pct}%로 판단됨", "citations": ids}, r, m, [])
    assert ok and ok["citations"] == ["r1", "r2"]
    assert verify_summary({"text": "야전병원·도하장비 증가로 공격 준비 가능성 99%로 판단됨", "citations": ids}, r, m, []) is None
    assert verify_summary({"text": "공격 준비 가능성 있음", "citations": ids}, r, m, []) is None   # 근거 내용 없음
    wrong_n = {"text": f"영상 등 6개 출처에서 야전병원·도하장비 증가로 공격 준비 가능성 {pct}%로 판단됨", "citations": ids}
    assert verify_summary(wrong_n, r, m, []) is None                                            # 출처 수 오기
    vague = {"text": f"출처 유형 1개, 핵심 징후 2개, 가설 '공격 준비' 확률 {pct}%로 판단됨", "citations": ids}
    assert verify_summary(vague, r, m, []) is None                                              # 징후를 구체적으로 안 밝힘
    t = template_summary(r)
    assert f"{r.top.probability:.0%}" in t["text"] and t["generated"] == "template"


def test_unit_baseline_reveals_surge_hidden_in_area_average():
    """A부대(평시 30대 유지)와 B부대(평시 5대 → 20대)가 섞이면 지역 평균은 왜곡되지만 부대별로는 B의 4배 증가가 드러남."""
    rows, unit_of, i = [], {}, 0
    for d in (25, 20, 15, 10):
        for uk, n in (("A", 30), ("B", 5)):
            i += 1; rows.append(TargetRow(i, "tank", NOW - timedelta(days=d), n))
            unit_of[i] = {"unit_key": uk, "name": f"제{uk}가상부대", "echelon": "여단", "dist_m": 500}
    for uk, n in (("A", 30), ("B", 20)):
        i += 1; rows.append(TargetRow(i, "tank", NOW - timedelta(days=1), n))
        unit_of[i] = {"unit_key": uk, "name": f"제{uk}가상부대", "echelon": "여단", "dist_m": 500}
    area = detect_changes(rows, NOW)[0]
    assert area.baseline_level == 17.5 and area.recent_level == 30          # 서로 다른 부대가 섞인 값
    units = {c.unit_key: c for c in detect_unit_changes(rows, unit_of, NOW)}
    assert units["B"].delta == 15 and units["B"].ratio == 4.0 and units["A"].delta == 0
    ids = {e.indicator_id for e in match_imagery([units["B"]], CAT, GR)}
    assert ids == {"I_ARMOR_MASS"}
    q = match_imagery([units["B"]], CAT, GR)[0].quote
    assert "[식별 부대: 제B가상부대(여단) 0.5km, 평시 대비 4.0배]" in q


def _unit_change(eq, base, recent, echelon, uk="U1"):
    from app.analysis.change import Change
    return Change(eq, base, recent, round(recent - base, 2), NOW, [1], unit_key=uk, unit_name=f"제{uk}가상부대",
                  echelon=echelon, dist_km=0.3, baseline_obs=4)


def test_echelon_thresholds():
    defs = CAT.indicator_defs()
    # 배율: 포병(min_delta 4) × 대대 0.2 → 1, × 여단 0.5 → 2 / 지표별 지정값 우선: 기갑 여단 6
    assert CAT.threshold(defs["I_ARTY_POS"], "대대").min_delta == 1
    assert CAT.threshold(defs["I_ARTY_POS"], "여단").min_delta == 2
    assert CAT.threshold(defs["I_ARMOR_MASS"], "여단").min_delta == 6
    assert CAT.threshold(defs["I_ARMOR_MASS"], None).min_delta == 10        # 지역 단위는 기본값
    # 여단 전차 +7: 지역 기준(10)으로는 미충족이지만 여단 기준(6)으로 충족
    hit = match_imagery([_unit_change("tank", 8, 15, "여단")], CAT, GR)
    assert [e.indicator_id for e in hit] == ["I_ARMOR_MASS"] and "(기준 여단 +6대" in hit[0].quote
    # 대대 전차 +2(기준 3 미달)지만 평시 대비 3배 → 배수 기준으로 충족 (증가량 ≥ 기준의 절반)
    assert [e.indicator_id for e in match_imagery([_unit_change("tank", 1, 3, "대대")], CAT, GR)] == ["I_ARMOR_MASS"]
    # 대대 전차 +1, 1.1배 → 미충족
    assert match_imagery([_unit_change("tank", 10, 11, "대대")], CAT, GR) == []


def test_counter_evidence_lowers_attack_and_confidence():
    base = [ev("I_ARMOR_MASS"), ev("I_BRIDGING"), ev("I_LOGISTICS", stype="OSINT", key="n", rel="C", cred=3),
            ev("I_ARTY_POS", stype="HUMINT", key="h", rel="C", cred=2)]
    before = assess(CAT, base, GR, NOW)
    counter = [ev("C_DECOY", stype="HUMINT", key="h2", rel="B", cred=2),
               ev("C_NORMAL_COMMS", stype="SIGINT", key="s2", rel="B", cred=2)]
    after = assess(CAT, base + counter, GR, NOW)
    pa = {h.hypothesis_id: h.probability for h in before.hypotheses}["H_ATTACK"]
    pb = {h.hypothesis_id: h.probability for h in after.hypotheses}["H_ATTACK"]
    assert pb < pa * 0.5                                        # 반대 증거로 공격 확률 크게 하락
    att = next(h for h in after.hypotheses if h.hypothesis_id == "H_ATTACK")
    armor = next(c for c in att.contributions if c.indicator_id == "I_ARMOR_MASS")
    assert armor.discount < 1                                    # 모형 장비 → 기갑 집결 신뢰 할인
    assert any(c.indicator_id == "C_DECOY" and c.delta_logit < 0 for c in att.contributions)
    order = {"하": 0, "중": 1, "상": 2}
    assert before.confidence == "상" and order[after.confidence] < order[before.confidence]   # 출처 간 모순 → 신뢰도 하향
    assert {c.counter_id for c in after.conflicts} == {"C_DECOY", "C_NORMAL_COMMS"}


def test_counter_hit_suppresses_overlapping_positive_hit():
    text = "협조자는 집결지에 세워진 전차 상당수가 고무와 합판으로 만든 모형으로 보인다고 전했다."
    chunk = ReportChunk("d9:0", text, "HUMINT", "src7", "B", 2, NOW)
    hits = [{"indicator_id": "I_ARMOR_MASS", "quote": "집결지에 세워진 전차 상당수가"},
            {"indicator_id": "C_DECOY", "quote": "전차 상당수가 고무와 합판으로 만든 모형으로 보인다"}]
    assert [e.indicator_id for e in hits_to_evidence(chunk, hits, CAT)] == ["C_DECOY"]
