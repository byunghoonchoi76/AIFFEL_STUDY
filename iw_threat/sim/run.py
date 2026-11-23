"""시나리오 실행기: 합성 데이터 생성 → API로 적재 → 날짜별 평가 → 결과 수집.
API만 사용(블랙박스)하므로 MS-C931의 실제 서비스에 그대로 실행 가능."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

from .scenario import Scenario, generate_targets, load_scenario, orbat_rows, report_documents, weather_series
from app.analysis.readiness import observation_from_result

GRADE_ORDER = {"관심": 0, "주의": 1, "경계": 2, "심각": 3}


def _ok(r):
    if r.status_code >= 400:
        raise RuntimeError(f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:500]}")
    return r.json()


def prepare(client, sc: Scenario, d_day: datetime, out: Path, seed: int) -> dict:
    """정리 → 전투서열·표적·첩보 적재."""
    _ok(client.delete("/targets", params={"area_id": sc.area_id}))
    _ok(client.delete("/documents", params={"area_id": sc.area_id}))
    _ok(client.delete("/weather", params={"area_id": sc.area_id}))
    wx = weather_series(sc, d_day)
    if wx:
        _ok(client.post("/weather", json=wx))
        (out / "weather.json").write_text(json.dumps(wx, ensure_ascii=False, indent=1), encoding="utf-8")

    rows = orbat_rows(sc)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    (out / "orbat_records.csv").write_text(buf.getvalue(), encoding="utf-8-sig")
    orbat = _ok(client.post("/orbat/load", files={"file": ("orbat_records.csv", buf.getvalue().encode("utf-8-sig"))},
                            data={"mode": "all", "to_graph": "true"}))

    targets = generate_targets(sc, d_day, seed)
    (out / "targets.json").write_text(json.dumps(targets, ensure_ascii=False, indent=1), encoding="utf-8")
    ins = _ok(client.post("/targets", json=[{k: v for k, v in t.items() if k not in ("truth_unit", "false_positive", "decoy_truth", "day")}
                                            for t in targets]))

    docs = report_documents(sc, d_day)
    (out / "reports").mkdir(exist_ok=True)
    for d in docs:
        (out / "reports" / d["filename"]).write_text(d["text"], encoding="utf-8")
        _ok(client.post("/ingest", files={"file": (d["filename"], d["text"].encode("utf-8"))}, data={
            "title": d["title"], "source_name": d["source_name"], "source_type": d["source_type"],
            "reliability": d["reliability"], "credibility": str(d["credibility"]), "area_id": sc.area_id,
            "reported_at": d["reported_at"], "extract_graph": "false"}))
    (out / "reports.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"orbat": orbat, "targets": ins.get("inserted"), "reports": len(docs), "weather_days": len(wx),
            "false_reports": sum(1 for d in docs if not d["truth"])}


def assess_series(client, sc: Scenario, d_day: datetime, step: int, log=print) -> tuple[list[dict], dict]:
    """평가 기간 동안 step일 간격으로 평가 (마지막 날만 해설 생성)."""
    days = list(range(sc.assess[0], sc.assess[1] + 1, step))
    if days[-1] != sc.assess[1]:
        days.append(sc.assess[1])
    series, final = [], {}
    for day in days:
        as_of = (d_day + timedelta(days=day)).replace(hour=18, minute=0, second=0, microsecond=0)
        r = _ok(client.post("/assess", json={"area_id": sc.area_id, "as_of": as_of.isoformat(),
                                             "explain": day == days[-1]}))
        probs = {h["name"]: h["probability"] for h in r["hypotheses"]}
        series.append({"day": day, "as_of": as_of.isoformat(), "grade": r["grade"], "confidence": r["confidence"],
                       "top_threat": r["top_hypothesis"], "probabilities": probs,
                       "threats": {h["hypothesis_id"]: h["threat"] for h in r["hypotheses"]},
                       "indicators": sorted({c["indicator_id"] for h in r["hypotheses"] for c in h["contributions"]}),
                       "decisive_indicators": sorted(observation_from_result(day, r).indicators),   # 태세 결정적 징후용(강도 기준 통과분)
                       "n_indicators": len(r["hypotheses"][0]["contributions"]), "elapsed_s": r.get("elapsed_s")})
        log(f"  D{day:+d}  등급 {r['grade']:2s}  " + "  ".join(f"{k} {v:.0%}" for k, v in probs.items()))
        final = r
    return series, final


def identification_accuracy(client, sc: Scenario, out: Path, window_days: int = 7) -> dict:
    """평가 마지막 주의 실제 표적(오탐 제외)을 부대 식별 API로 조회해 정답 부대와 비교."""
    from app.orbat.patterns import unit_key

    targets = json.loads((out / "targets.json").read_text(encoding="utf-8"))
    in_orbat = {u.name for u in sc.units if u.in_orbat}
    sample = [t for t in targets if not t["false_positive"] and not t.get("decoy_truth") and t["truth_unit"] in in_orbat
              and sc.assess[1] - window_days < t["day"] <= sc.assess[1]]
    ok = miss = wrong = 0
    errors = []
    for t in sample:
        r = _ok(client.get("/orbat/identify", params={"lat": t["lat"], "lon": t["lon"], "equipment": t["equipment_type"]}))
        best = r.get("best")
        if not best:
            miss += 1
        elif best["unit_key"] == unit_key(t["truth_unit"]):
            ok += 1
        else:
            wrong += 1
            errors.append(f"{t['equipment_type']}: 정답 {t['truth_unit']} → {best['name']}")
    n = max(len(sample), 1)
    return {"events": len(sample), "correct": ok, "wrong": wrong, "unidentified": miss,
            "accuracy": round(ok / n, 3), "errors": sorted(set(errors))[:10]}


def readiness_eval(sc: Scenario, series: list[dict], readiness_path: str = "config/readiness.yaml") -> dict:
    """태세 건의를 시간순으로 계산하고 시나리오 정답 태세 일정과 비교 (트랙별)."""
    from app.analysis.readiness import Observation, load_readiness, recommend_all

    tracks, basis = load_readiness(readiness_path)
    obs = [Observation(x["day"], x["threats"], x["confidence"], set(x.get("decisive_indicators", x["indicators"])), f"D{x['day']:+d}") for x in series]
    recs = recommend_all(tracks, obs)
    truth_cfg = (sc.truth.get("readiness") or {})
    out = {"basis": basis, "tracks": {}}
    for t in tracks:
        sched = sorted(truth_cfg.get(t.id, [[sc.assess[0], t.baseline]]))
        truth_at = lambda day: next((lv for d0, lv in reversed(sched) if day >= d0), t.baseline)  # noqa: E731
        rows = [{"day": r["day"], "rec": r["level"], "truth": truth_at(r["day"]), "action": r["action"],
                 "name": r["level_name"], "reasons": r["reasons"], "note": r["note"], "defcon": r["defcon_review"]}
                for r in recs[t.id]]
        delays = []
        for (d0, lv), prev in zip(sched[1:], sched[:-1]):
            if lv < prev[1]:                                          # 정답상 격상 시점
                first_ok = next((r["day"] for r in rows if r["rec"] <= lv), None)
                delays.append({"to": t.name_of(lv), "truth_day": d0, "rec_day": first_ok,
                               "delay_days": (first_ok - d0) if first_ok is not None else None})
        changes = sum(1 for a, b in zip(rows, rows[1:]) if a["rec"] != b["rec"])
        out["tracks"][t.id] = {
            "name": t.name, "final": rows[-1]["name"], "final_truth": t.name_of(rows[-1]["truth"]),
            "match_rate": round(sum(r["rec"] == r["truth"] for r in rows) / len(rows), 3),
            "over_days": sum(r["rec"] < r["truth"] for r in rows), "under_days": sum(r["rec"] > r["truth"] for r in rows),
            "level_changes": changes, "escalation_delays": delays,
            "defcon_review_days": [r["day"] for r in rows if r["defcon"]], "series": rows}
    return out


def evaluate(sc: Scenario, series: list[dict]) -> dict:
    """정답 대비 평가. 공격 시나리오: 경보 선행일수 / 훈련 시나리오: 오경보 일수."""
    truth_h = sc.truth["hypothesis"]
    final = series[-1]
    most_probable = max(final["probabilities"], key=final["probabilities"].get)
    warn = [s for s in series if GRADE_ORDER.get(s["grade"], 0) >= GRADE_ORDER["경계"]]
    attack_p = [s["probabilities"].get("공격 준비", 0.0) for s in series]
    if truth_h in final["probabilities"]:
        correct = most_probable == truth_h
    else:   # 정답이 '없음'(평시·기만): 어떤 가설도 50% 미만이고 등급이 경계 미만이어야 정답
        correct = max(final["probabilities"].values()) < 0.5 and GRADE_ORDER.get(final["grade"], 0) < GRADE_ORDER["경계"]
        most_probable = most_probable if max(final["probabilities"].values()) >= 0.5 else "없음(모든 가설 50% 미만)"
    ev = {"truth": truth_h, "final_most_probable": most_probable, "final_correct": correct,
          "final_grade": final["grade"], "final_confidence": final["confidence"],
          "max_attack_probability": round(max(attack_p), 4)}
    if truth_h == "공격 준비":
        first = warn[0]["day"] if warn else None
        ev.update({"first_warning_day": first, "warning_lead_days": (-first if first is not None else None),
                   "missed": first is None})
    else:
        ev.update({"false_alarm_days": [s["day"] for s in warn], "false_alarm_count": len(warn)})
    return ev


def opfor_metrics(sc: Scenario, series: list[dict], final: dict, out: Path) -> dict:
    """test_cases.yaml 지표 중 이 시스템이 담당하는 M2-echelon·M3-warning·M4-decoy 산출 + 시험 항목 대응표."""
    import yaml

    from app.orbat.echelon_estimate import estimate
    from app.orbat.echelon_estimate import TYPE_INFO

    targets = json.loads((out / "targets.json").read_text(encoding="utf-8"))
    # M3-warning: 위기 고조 시작 후 첫 경보(경계 이상)까지의 관측(재방문) 횟수
    ws = sc.truth.get("warning_start", sc.assess[0])
    first = next((x["day"] for x in series if x["day"] >= ws and GRADE_ORDER.get(x["grade"], 0) >= GRADE_ORDER["경계"]), None)
    obs_days = sorted({t["day"] for t in targets})
    revisits = len([d for d in obs_days if ws <= d <= first]) if first is not None else None
    # M4-decoy: 기만체가 실표적으로 입력된 비율 (탐지기 판별 모형 + 시스템 입력 기준)
    dec = [t for t in targets if t.get("decoy_truth")]
    dec_total = sum(t["count"] for t in dec)
    dec_real = sum(t["count"] for t in dec if not t["equipment_type"].startswith("decoy_"))
    # M2-echelon: 편제 템플릿 부대별 관측 장비(D-5~D-2 최대) → 유형 추정
    rows, ok = [], 0
    for u in (x for x in sc.units if x.template):
        cnt: dict[str, float] = {}
        for t in targets:
            if t["truth_unit"] == u.name and -5 <= t["day"] <= -2 and not t["false_positive"] and not t.get("decoy_truth"):
                cnt[t["equipment_type"]] = max(cnt.get(t["equipment_type"], 0), t["count"])
        est = estimate(cnt)
        hit = bool(est) and est["type"] == u.template
        ok += hit
        rows.append({"unit": u.name, "truth": TYPE_INFO[u.template][0], "estimate": est["type_ko"] if est else "추정 불가",
                     "similarity": est["similarity"] if est else None, "strength_ratio": est["strength_ratio"] if est else None,
                     "correct": hit})
    n_tpl = max(len(rows), 1)
    cov = []
    tc_path = Path(sc.meta.get("test_cases", ""))
    if tc_path.exists():
        tc = yaml.safe_load(tc_path.read_text(encoding="utf-8"))
        ours = {"M2-echelon": f"{ok / n_tpl:.0%}", "M3-warning": f"{revisits}회" if revisits is not None else "미탐",
                "M4-decoy": f"{dec_real / dec_total:.0%}" if dec_total else "-"}
        partial = {"M3-occupancy": "점유 라벨 활용(판별 정확도는 탐지기)", "M4-camouflage": "탐지기 재현율 가정 0.6 반영",
                   "M3-tel": "TEL 출고·분산 징후 활용(추적 정확도는 탐지기)"}
        for c in tc["test_cases"]:
            got = [f"{m}={ours[m]}" for m in c["metrics"] if m in ours]
            part = [f"{m}: {partial[m]}" for m in c["metrics"] if m in partial]
            cov.append({"id": c["id"], "scene": c["scene"], "metrics": c["metrics"],
                        "status": "평가" if got else ("일부 반영" if part else "탐지기 단계(범위 외)"),
                        "result": "; ".join(got + part)})
    return {"M2_echelon": {"accuracy": round(ok / n_tpl, 3), "target": 0.80, "units": rows},
            "M3_warning": {"warning_start": ws, "first_warning_day": first, "revisits_to_detect": revisits, "target_max": 2},
            "M4_decoy": {"decoys": dec_total, "as_real": dec_real, "decoy_as_real_rate": round(dec_real / dec_total, 3) if dec_total else None,
                         "target_max": 0.20},
            "unidentified_estimates": [{k: e[k] for k in ("type_ko", "echelon", "similarity", "strength_ratio", "lat", "lon")}
                                       for e in final.get("unit_estimates", [])[:6]],
            "test_case_coverage": cov}


def _load(path) -> Scenario:
    p = Path(path)
    if isinstance(path, Scenario):
        return path
    if p.is_dir() and (p / "scenario_events.yaml").exists():
        from .opfor import build_opfor_scenario
        return build_opfor_scenario(p)
    return load_scenario(p)


def run_scenario(client, path, out_dir: str | Path, d_day: datetime | None = None,
                 step: int = 2, seed: int = 7, log=print) -> dict:
    sc = _load(path)
    d_day = d_day or datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    out = Path(out_dir) / sc.id
    out.mkdir(parents=True, exist_ok=True)
    log(f"[{sc.name}] 데이터 적재 중…")
    prep = prepare(client, sc, d_day, out, seed)
    log(f"  전투서열 {prep['orbat'].get('unit_rows')}개 부대 · 표적 {prep['targets']}건 · 첩보 {prep['reports']}건(오정보 {prep['false_reports']})")
    log(f"[{sc.name}] 날짜별 평가 (D{sc.assess[0]:+d} ~ D{sc.assess[1]:+d}, {step}일 간격)")
    series, final = assess_series(client, sc, d_day, step, log)
    ev = evaluate(sc, series)
    ev["identification"] = identification_accuracy(client, sc, out)
    ev["readiness"] = readiness_eval(sc, series)
    if sc.meta.get("test_cases"):
        ev["opfor"] = opfor_metrics(sc, series, final, out)
    result = {"scenario": {"id": sc.id, "name": sc.name, "area_id": sc.area_id, "truth": sc.truth, "meta": sc.meta,
                           "d_day": d_day.isoformat(), "seed": seed, "step": step},
              "prepare": prep, "series": series, "final": final, "evaluation": ev}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return result
