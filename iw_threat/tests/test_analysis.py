import io
import math
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.analysis.catalog import load_catalog, load_grading
from app.analysis.change import TargetRow, detect_changes
from app.analysis.explain import (build_evidence_map, label_doctrine, template_sentences, template_summary,
                                  verify_sentences, verify_summary)
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


def test_verify_sentences_requires_indicator_matched_citations():
    ev_map = {"I_MEDICAL": [{"id": "E1", "ref": "targets:field_hospital:1", "text": "영상 판독 야전병원 0대 → 2대"}],
              "I_ARMOR_MASS": [{"id": "E2", "ref": "targets:tank:2,3", "text": "영상 판독 전차 13대 → 40대"}]}
    doctrine = label_doctrine([{"ref": "chunk:d1:0", "text": "공격 전 기갑부대는 집결지를 점령한다"}])
    names = {"I_MEDICAL": "야전 의무시설 전개", "I_ARMOR_MASS": "기갑·기계화 장비 전방 집결"}
    kept, dropped = verify_sentences(
        [{"indicator_id": "I_MEDICAL", "text": "I_MEDICAL: 야전병원 2개소 새로 확인됨", "citations": ["[E1]"]},   # 접두어·괄호 정리
         {"indicator_id": "I_MEDICAL", "text": "전차 40대 근거를 끌어다 씀", "citations": ["E2"]},              # 다른 지표 근거
         {"indicator_id": "I_ARMOR_MASS", "text": "교리상 집결지 점령 단계로 판단됨", "citations": ["D1"]},
         {"indicator_id": "I_ARMOR_MASS", "text": "기갑·기계화 장비 전방 집결이 상당히 충족됨", "citations": ["E2"]},  # 상투 문장
         {"indicator_id": "I_ARMOR_MASS", "text": "근거 없음", "citations": []},
         {"indicator_id": "I_UNKNOWN", "text": "모르는 지표", "citations": ["D1"]}],
        ev_map, doctrine, names,
    )
    assert [s["text"] for s in kept] == ["야전병원 2개소 새로 확인됨", "교리상 집결지 점령 단계로 판단됨"]
    assert len(dropped) == 4
    assert kept[0]["citations"] == ["targets:field_hospital:1"] and kept[1]["citations"] == ["chunk:d1:0"]


def test_template_fills_uncovered_indicators():
    evidence = [Evidence("I_MEDICAL", "IMINT", "s", "B", 2, NOW, "r1", quote="야전병원 0 → 2"),
                Evidence("I_BRIDGING", "IMINT", "s", "B", 2, NOW, "r2", quote="도하 0 → 4")]
    r = assess(CAT, evidence, GR, NOW)
    m = build_evidence_map(r, evidence)
    filled = template_sentences(r, m, covered={"I_MEDICAL"})
    assert [f["indicator_id"] for f in filled] == ["I_BRIDGING"] and filled[0]["citations"] == ["r2"]


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
    vague = {"text": f"출처 유형 1개, 핵심 징후 2개, 가설 '공격 준비' 확률 {pct}%로 판단됨", "citations": ids}
    assert verify_summary(vague, r, m, []) is None                                              # 징후를 구체적으로 안 밝힘
    t = template_summary(r)
    assert f"{r.top.probability:.0%}" in t["text"] and t["generated"] == "template"
