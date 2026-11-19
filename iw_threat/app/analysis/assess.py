"""평가 오케스트레이터: 변화 분석 → 지표 매칭(영상 규칙 + 텍스트 LLM) → 위협도 산정 → 근거 해설."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta

from ..config import get_settings, to_aware
from ..embed import embed_query
from ..stores import pg
from .catalog import load_catalog, load_grading
from .change import TargetRow, detect_changes, detect_unit_changes
from .explain import (build_evidence_map, explanation_prompt, indicator_lines, label_doctrine, template_summary,
                      verify_implications, verify_summary)
from .matching import ReportChunk, classification_prompt, hits_to_evidence, match_imagery
from .scoring import assess


def estimate_unidentified(points: list[dict], link_km: float = 4.0) -> list[dict]:
    """전투서열로 식별되지 않은 최근 표적을 거리 기준으로 묶어 장비 구성으로 부대 유형·제대 추정 (M2-echelon).
    예) 주둔지를 떠나 집결지에 나타난 전차·장갑차 군집 → '전차여단(여단) 추정'."""
    from ..orbat.echelon_estimate import estimate
    from ..orbat.identify import haversine_m

    clusters: list[dict] = []
    for p in sorted(points, key=lambda x: -x["count"]):
        if p.get("lat") is None:
            continue
        for c in clusters:
            if haversine_m(p["lat"], p["lon"], c["lat"], c["lon"]) <= link_km * 1000:
                break
        else:
            c = {"lat": p["lat"], "lon": p["lon"], "max": {}, "ids": []}
            clusters.append(c)
        c["max"][p["equipment_type"]] = max(c["max"].get(p["equipment_type"], 0), p["count"])
        c["ids"].append(p["id"])
    out = []
    for c in clusters:
        est = estimate(c["max"])
        if est:
            out.append({"lat": round(c["lat"], 5), "lon": round(c["lon"], 5), "equipment": c["max"],
                        "event_ids": c["ids"][:50], **est})
    return sorted(out, key=lambda e: -sum(e["equipment"].values()))


def _report_chunks(vector, area_id: str, as_of: datetime, lookback_days: int, k: int) -> list[ReportChunk]:
    q = embed_query("적 부대 이동, 장비 집결, 보급 증가, 지휘소·의무시설 전개, 훈련 공표 관련 첩보")
    hits = vector.search(q, k, source_types=["HUMINT", "SIGINT", "OSINT"], area_id=area_id,
                         since=as_of - timedelta(days=lookback_days), until=as_of)
    return [ReportChunk(
        chunk_id=h["chunk_id"], text=h["text"], source_type=h["source_type"], source_key=h["source_key"],
        reliability=h["reliability"], credibility=int(h["credibility"]),
        reported_at=to_aware(datetime.fromisoformat(h["reported_at"])),
    ) for h in hits if h.get("reported_at")]


def run_assessment(area_id: str, as_of: datetime, *, vector=None, llm=None, explain: bool = True) -> dict:
    t0 = time.perf_counter()
    s = get_settings()
    as_of = to_aware(as_of)
    catalog, grading = load_catalog(s.indicators_path), load_grading(s.grading_path)

    # 1) 영상 판독 표적 변화 → 규칙 매칭
    #    전투서열 DB가 있으면 표적을 부대별로 나눠 '부대별 평시 기준선'과 비교, 없으면 지역 단위 비교
    from ..orbat import store as orbat_store
    since = as_of - timedelta(days=s.window_days + s.baseline_days)
    attribution: list[dict] = []
    unit_estimates: list[dict] = []
    baseline_mode = "area"
    if orbat_store.has_data():
        pts = pg.fetch_target_points(area_id, since, as_of)
        unit_of = orbat_store.identify_events(pts)
        rows = [TargetRow(p["id"], p["equipment_type"], p["observed_at"], p["count"]) for p in pts]
        changes = detect_unit_changes(rows, unit_of, as_of, s.window_days, s.baseline_days)
        recent = [p for p in pts if p["observed_at"] > as_of - timedelta(days=s.window_days)]
        attribution = orbat_store.attribute_targets(recent)
        unit_estimates = estimate_unidentified([p for p in recent if not unit_of.get(p["id"])])
        baseline_mode = "unit"
    else:
        rows = pg.fetch_target_rows(area_id, since, as_of)
        changes = detect_changes(rows, as_of, s.window_days, s.baseline_days)
    evidence = match_imagery(changes, catalog, grading)

    # 2) 텍스트 첩보 → LLM 분류(인용문 원문 대조 검증)
    rejected_hits = 0
    n_chunks = 0
    if vector is not None and llm is not None:
        chunks = _report_chunks(vector, area_id, as_of, s.report_lookback_days, s.report_top_k)
        n_chunks = len(chunks)

        def classify(ch):
            system, user = classification_prompt(ch, catalog)
            try:
                return ch, llm.chat_json(system, user, max_tokens=2048, reasoning_effort=s.classify_reasoning_effort).get("hits", [])
            except Exception:  # noqa: BLE001
                return ch, []

        # vLLM 연속 배칭 활용: 동시 요청으로 처리량 확보
        with ThreadPoolExecutor(max_workers=s.llm_concurrency) as pool:
            for ch, hits in pool.map(classify, chunks):
                ev = hits_to_evidence(ch, hits, catalog)
                rejected_hits += len(hits) - len(ev)
                evidence.extend(ev)

    # 3) 위협도 산정 (결정적 계산)
    result = assess(catalog, evidence, grading, as_of)

    # 4) 근거 기반 해설 (교리 검색 + 근거 검증)
    narrative = {"summary": template_summary(result) if result.top.contributions else None, "sentences": [], "dropped": 0}
    if explain and vector is not None and llm is not None and result.top.contributions:
        q = embed_query(" ".join(c.name for c in result.top.contributions))
        doctrine = label_doctrine([{"ref": f"chunk:{d['chunk_id']}", "text": d["text"]}
                                   for d in vector.search(q, s.doctrine_top_k, source_types=["DOCTRINE"])])
        ev_map = build_evidence_map(result, evidence)
        system, user = explanation_prompt(result, ev_map, doctrine)
        implications, dropped = {}, []
        try:
            out = llm.chat_json(system, user, max_tokens=2048, reasoning_effort=s.explain_reasoning_effort)
            names = {c.indicator_id: c.name for c in result.top.contributions}
            implications, dropped = verify_implications(out.get("implications"), ev_map, names)
            narrative["summary"] = verify_summary(out.get("summary"), result, ev_map, doctrine) or narrative["summary"]
        except Exception as e:  # noqa: BLE001
            narrative["error"] = str(e)
        lines = indicator_lines(result, ev_map, implications, {i: d.meaning for i, d in catalog.indicator_defs().items()})
        narrative.update({"sentences": lines, "dropped": len(dropped),
                          "template_filled": sum(1 for x in lines if x["generated"] == "template"),
                          "dropped_samples": dropped[:3]})

    payload = {
        "baseline_mode": baseline_mode,
        "changes": [{**asdict(c), "ratio": c.ratio} for c in changes],
        "unit_attribution": attribution,
        "unit_estimates": unit_estimates,
        "evidence": [asdict(e) for e in evidence],
        "hypotheses": [asdict(h) for h in result.hypotheses],
        "conflicts": [asdict(c) for c in result.conflicts],
        "rejected_llm_hits": rejected_hits,
        "catalog_version": catalog.meta.get("version"),
        "report_chunks_checked": n_chunks,
        "elapsed_s": None,
    }
    payload["elapsed_s"] = round(time.perf_counter() - t0, 2)
    aid = pg.insert_assessment(
        area_id, as_of, result.top.name, result.threat_score, result.grade, result.confidence,
        _jsonable(payload), narrative, {"llm": s.llm_served_name, "embed": s.embed_model},
    )
    pg.audit("system", "assess", {"assessment_id": aid, "area_id": area_id, "as_of": as_of.isoformat()})
    return {"assessment_id": aid, "area_id": area_id, "as_of": as_of.isoformat(),
            "top_hypothesis": result.top.name, "probability": result.top.probability,
            "threat_score": result.threat_score, "grade": result.grade, "confidence": result.confidence,
            "narrative": narrative, **_jsonable(payload)}


def _jsonable(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o
