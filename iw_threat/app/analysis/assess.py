"""평가 오케스트레이터: 변화 분석 → 지표 매칭(영상 규칙 + 텍스트 LLM) → 위협도 산정 → 근거 해설."""
from __future__ import annotations

from dataclasses import replace as dc_replace

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
from .matching import ReportChunk, classification_prompt, hits_to_evidence, match_imagery, sync_evidence
from .commander import KeyTarget, target_activity
from .scoring import Evidence, assess


def lr_matrix(catalog, result) -> dict:
    """식별된 지표 × 가설 우도비 (경쟁가설 비교표용). 1보다 크면 그 가설을 지지, 작으면 반박."""
    ids = {c.indicator_id for h in result.hypotheses for c in h.contributions}
    names = {i: d.name for i, d in catalog.indicator_defs().items()}
    out = {}
    for iid in sorted(ids):
        out[iid] = {"name": names.get(iid, iid),
                    "lr": {h.name: next((x.lr for x in h.indicators if x.id == iid), 1.0) for h in catalog.hypotheses}}
    return out


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


WX_EVIDENCE = {"favorable_attack": "I_WX_WINDOW", "unfavorable_attack": "C_WX_NOGO", "infiltration_cover": "I_WX_COVER"}


def weather_assessment(area_id: str, as_of: datetime, catalog, grading) -> dict:
    """기상 자료가 있으면 영향 평가·근거 생성, 없으면 빈 결과(기존 평가에 영향 없음)."""
    from .scoring import Evidence
    from .weather import effects, interpret, load_rules
    rules = load_rules(get_settings().weather_rules_path)
    days = rules.get("assessment", {}).get("outlook_days", 3)
    rows = pg.fetch_weather(area_id, as_of - timedelta(days=1), as_of + timedelta(days=days))
    if not rows:
        return {"available": False, "evidence": []}
    past = [r for r in rows if r["valid_at"] <= as_of]
    cur = past[-1] if past else rows[0]
    fut = [r for r in rows if r["valid_at"] > as_of]
    cur_e = effects(cur["data"], rules)
    out_e = [effects(r["data"], rules) for r in fut]
    it = interpret(cur_e, out_e, rules)
    rel, cred = grading.default_grade("METOC")
    valid = catalog.indicator_defs()
    ev = [Evidence(indicator_id=WX_EVIDENCE[f["key"]], source_type="METOC", source_key="METOC:wx",
                   reliability=rel, credibility=cred, observed_at=cur["valid_at"], ref=f"weather:{area_id}:{cur['valid_at'].date()}",
                   quote=f["text"]) for f in it["findings"] if WX_EVIDENCE.get(f["key"]) in valid]
    return {"available": True, "valid_at": cur["valid_at"].isoformat(), "kind": cur["kind"], "data": cur["data"],
            "effects": [vars(e) for e in cur_e],
            "outlook": [{"valid_at": r["valid_at"].isoformat(), "effects": [vars(e) for e in es]} for r, es in zip(fut, out_e)],
            "findings": it["findings"], "collection_limits": it["collection_limits"], "evidence": ev}


LOGI_EQ = {"truck", "fuel_truck", "supply_point"}
FRONT_IND = {"I_ARMOR_MASS", "I_ARTY_POS", "I_HARTS_OCC"}


def deception_checks(changes, evidence, pts, catalog, grading) -> list:
    """기만·단순 전시 판별 (자동 반대 징후):
    ① SAR 금속 반사파 불일치 표적 비율 ≥ 50% → 모형·기만체 의심(C_DECOY)
    ② 전방 기갑·포병 증강에도 후방 보급·유류 차량 증가가 없음 → 지속지원 미연동(C_NO_LOGISTICS, 약하게)"""
    from .scoring import Evidence
    valid = catalog.indicator_defs()
    rel, cred = grading.default_grade("IMINT")
    out = []
    sar = {p["id"]: p.get("sar_verified") for p in pts}
    front = [e for e in evidence if e.indicator_id in FRONT_IND and e.scale >= 1.0]
    if "C_DECOY" in valid:
        for e in front:
            ids = [int(x) for x in e.ref.split(":")[-1].split(",") if x.isdigit()]
            flags = [sar.get(i) for i in ids if sar.get(i) is not None]
            if flags and sum(1 for f in flags if f is False) / len(flags) >= 0.5:
                out.append(Evidence(indicator_id="C_DECOY", source_type="IMINT", source_key="IMINT:sar_check",
                                    reliability=rel, credibility=cred, observed_at=e.observed_at, ref="sar:" + e.ref,
                                    quote=f"SAR 금속 반사파 불일치 {sum(1 for f in flags if f is False)}/{len(flags)}건 — 모형·기만체 의심 ({e.quote[:40]})"))
    fires = [e for e in front if e.indicator_id in ("I_ARTY_POS", "I_HARTS_OCC")]
    # 기동부대 존재: 기갑 집결(약한 신호 포함)·주둔지 이탈·공격대형·동시 은밀 전개 중 하나라도 있으면 해당
    armor = [e for e in evidence if (e.indicator_id == "I_ARMOR_MASS" and e.scale >= 1.0)
             or e.indicator_id in ("I_DISPERSAL", "I_ASSAULT", "I_SYNC_CONCEAL")]
    if fires and not armor and "C_NO_MANEUVER" in valid:     # 화력만 증강, 기동부대 집결 없음 → 국지 화력 도발 쪽
        out.append(Evidence(indicator_id="C_NO_MANEUVER", source_type="IMINT", source_key="IMINT:maneuver_check",
                            reliability=rel, credibility=cred, observed_at=max(e.observed_at for e in fires),
                            ref="maneuver:none", scale=0.7,
                            quote="포병·갱도포병 증강이 식별되나 기갑·기계화 기동부대 집결은 미식별 — 전면 공격보다 국지 화력 도발 가능성"))
    logi = [c for c in changes if c.equipment_type in LOGI_EQ and c.baseline_obs >= 3]
    has_logi_ev = any(e.indicator_id == "I_LOGISTICS" for e in evidence)
    if front and logi and not has_logi_ev and all(c.delta <= 0 for c in logi) and "C_NO_LOGISTICS" in valid:
        out.append(Evidence(indicator_id="C_NO_LOGISTICS", source_type="IMINT", source_key="IMINT:logistics_check",
                            reliability=rel, credibility=cred, observed_at=max(e.observed_at for e in front),
                            ref="logistics:" + ",".join(c.equipment_type for c in logi), scale=0.5,
                            quote="전방 기갑·포병 증강에도 후방 보급·유류 차량 증가 없음 — 지속지원 미연동(단순 전시·훈련 가능성)"))
    return out


def terrain_assessment(area_id, as_of, weather, catalog, grading, s, pts, since) -> dict:
    from .scoring import Evidence
    from .terrain import facility_vulnerability, load_terrain
    try:
        cfg = load_terrain(s.terrain_path)
    except FileNotFoundError:
        return {"available": False, "evidence": []}
    if not cfg.get("facilities"):
        return {"available": False, "evidence": []}
    pts = pts if pts is not None else pg.fetch_target_points(area_id, since, as_of)
    recent = [p for p in pts if p["observed_at"] > as_of - timedelta(days=s.window_days)]
    wx = weather.get("data") if weather.get("available") else None
    chem_ok = any(e["domain"] == "chemical_ops" and e["level"] == "양호" for e in weather.get("effects", []))
    res = facility_vulnerability(recent, cfg, wx, chem_ok)
    rel, cred = grading.default_grade("IMINT")
    valid = catalog.indicator_defs()
    ev = []
    hi = [f for f in res["facilities"] if f["fires_in_range"] and f.get("grade") == "가"]
    if hi and "I_CHEM_DELIVERY" in valid:
        ev.append(Evidence(indicator_id="I_CHEM_DELIVERY", source_type="IMINT", source_key="IMINT:terrain",
                           reliability=rel, credibility=cred, observed_at=max(p["observed_at"] for p in recent),
                           ref="terrain:" + ",".join(f["id"] for f in hi),
                           quote="가급 중요시설 " + ", ".join(f["name"] for f in hi) + " — 적 화력(투발 수단) 사거리 내"))
    dw = [f for f in res["facilities"] if f["downwind"]]
    if chem_ok and "I_WX_CHEM" in valid:        # 북풍(북→남) 약풍·강수 없음 = 군 판단상 화학탄 사용 가능성 높은 기상
        r2, c2 = grading.default_grade("METOC")
        ev.append(Evidence(indicator_id="I_WX_CHEM", source_type="METOC", source_key="METOC:chem",
                           reliability=r2, credibility=c2, observed_at=to_aware(datetime.fromisoformat(weather["valid_at"])),
                           ref="weather:chem", quote=f"북풍({wx.get('wind_dir_deg')}°) {wx.get('wind_ms')}m/s·강수 없음 — 화학탄 사용 가능성 높은 기상"
                           + (", 풍하 시설: " + ", ".join(f["name"] for f in dw) if dw else "")))
    return {"available": True, **res, "evidence": ev}


def issued_posture_evidence(area_id: str, as_of: datetime, catalog, grading, s) -> tuple[dict, list]:
    """합참·연합사 발령 태세(평시 기준보다 높은 단계)를 해당 가설의 참고 근거로 변환."""
    from .readiness import load_readiness
    from .scoring import Evidence
    try:
        posture = pg.fetch_posture(area_id, as_of)
    except Exception:  # noqa: BLE001
        posture = {}
    if not posture:
        return {}, []
    tracks, _ = load_readiness(s.readiness_path)
    rel, cred = grading.default_grade("COMMAND")
    valid = catalog.indicator_defs()
    issued, ev = {}, []
    for t in tracks:
        p = posture.get(t.id)
        if not p:
            continue
        issued[t.id] = {"level": p["level"], "level_name": t.name_of(p["level"]), "issuer": p["issuer"],
                        "valid_from": p["valid_from"].isoformat(), "note": p["note"]}
        pe = t.posture_evidence or {}
        scale = (pe.get("scale_by_level") or {}).get(p["level"])
        if p["level"] < t.baseline and scale and pe.get("indicator") in valid:
            ev.append(Evidence(indicator_id=pe["indicator"], source_type="COMMAND", source_key=f"COMMAND:{t.id}",
                               reliability=rel, credibility=cred, observed_at=p["valid_from"],
                               ref=f"posture:{t.id}:{p['level']}", scale=float(scale),
                               quote=f"{p['issuer']} {t.name_of(p['level'])} 판단·발령({p['valid_from'].date()}) — 상급 판단을 평가 참고 조건으로 반영"))
    return issued, ev


def commander_requirements(result, kt_acts: list[dict], s) -> dict:
    """합참의장·연합사령관 PIR 답변 상태와 CCIR 보고 조건."""
    from .commander import ccir_alerts, load_requirements, pir_answers
    req = load_requirements(s.pir_ccir_path)
    hyps = [asdict(h) for h in result.hypotheses]
    pirs = pir_answers(req, hyps, kt_acts)
    return {"pirs": pirs, "ccir_alerts": ccir_alerts(req, pirs, kt_acts),
            "key_target_activity": [{k: v for k, v in a.items() if k != "hits"} | {
                "hits": [{**h, "latest_at": h["latest_at"].isoformat(), "refs": h["refs"][:20]} for h in a["hits"]]}
                for a in kt_acts], "basis": req.get("version")}


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
    evidence += deception_checks(changes, evidence, pts if baseline_mode == "unit" else [], catalog, grading)
    # 관측 공백: 지역의 마지막 영상 관측 이후 기간 → 영상 근거의 시의성 감쇠를 완화 (기상·운용 제약으로 재관측 불가)
    last_img = max((r.observed_at for r in rows), default=None)
    if last_img is not None:
        gap = max(0.0, (as_of - last_img).total_seconds() / 86400)
        evidence = [dc_replace(e, gap_days=gap) for e in evidence]
    evidence += sync_evidence(evidence, catalog, grading, {c.unit_key: c.unit_name for c in changes if c.unit_key})

    # 1-1) 합참·연합사 핵심표적·고가치표적 활동 (지휘관 우선 표적 → 위협 평가 기준)
    key_targets, kt_acts = [], []
    try:
        key_targets = [KeyTarget(**{k: r[k] for k in KeyTarget.__dataclass_fields__ if k in r})
                       for r in pg.fetch_keytargets(area_id)]
    except Exception:  # noqa: BLE001 — 표적목록 미적재 시 생략
        key_targets = []
    if key_targets:
        kt_pts = pts if baseline_mode == "unit" else pg.fetch_target_points(area_id, since, as_of)
        kt_acts = target_activity(key_targets, changes, kt_pts, as_of, s.window_days, s.baseline_days)
        rel, cred = grading.default_grade("IMINT")
        valid = catalog.indicator_defs()
        for a in kt_acts:
            if a["indicator"] not in valid:
                continue
            h0 = a["hits"][0]
            evidence.append(Evidence(
                indicator_id=a["indicator"], source_type="IMINT", source_key=f"IMINT:keytarget:{a['target_id']}",
                reliability=rel, credibility=cred, observed_at=a["latest_at"],
                ref=f"keytarget:{a['target_id']}:" + ",".join(map(str, h0["refs"][:20])),
                quote=f"{a['owner']} {a['category']} '{a['name']}' 활동 — " + "; ".join(
                    f"{h['equipment']} {h['baseline']:g}→{h['recent']}대({h['basis']} 매칭)" for h in a["hits"])))

    # 1-2) 기상이 작전에 미치는 영향 (전장정보분석) — 현재 + 전망 기간 기상 → 영역별 영향, 적 작전 여건 근거, 아군 관측 제한
    weather = weather_assessment(area_id, as_of, catalog, grading)
    evidence += weather.pop("evidence")

    # 1-2b) 지형(도심지 진출 속도)·아군 중요시설 취약성 — 화학탄 투발 수단 사거리·풍하 → 화학 위협 근거
    terrain = terrain_assessment(area_id, as_of, weather, catalog, grading, s,
                                 pts if baseline_mode == "unit" else None, since)
    evidence += terrain.pop("evidence")

    # 1-3) 합참·연합사가 판단·발령한 현 태세 → 평가 참고 조건 (상급 판단 반영, 분석 신뢰도 독립 출처에는 미산입)
    issued, posture_ev = issued_posture_evidence(area_id, as_of, catalog, grading, s)
    evidence += posture_ev

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
        "changes": [{**asdict(c), "ratio": c.ratio, "zscore": c.zscore} for c in changes],
        "unit_attribution": attribution,
        "unit_estimates": unit_estimates,
        "evidence": [asdict(e) for e in evidence],
        "hypotheses": [asdict(h) for h in result.hypotheses],
        "conflicts": [asdict(c) for c in result.conflicts],
        "lr_matrix": lr_matrix(catalog, result),
        "weather": weather,
        "issued_posture": issued,
        "terrain": terrain,
        "commander_requirements": commander_requirements(result, kt_acts, s),
        "rejected_llm_hits": rejected_hits,
        "catalog_version": catalog.meta.get("version"),
        "report_chunks_checked": n_chunks,
        "elapsed_s": None,
    }
    from .patterns import case_similarity, commander_briefing, load_cases
    payload["patterns"] = case_similarity(payload["hypotheses"], load_cases(s.cases_path))
    payload["briefing"] = commander_briefing({**payload, "grade": result.grade, "confidence": result.confidence})
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
