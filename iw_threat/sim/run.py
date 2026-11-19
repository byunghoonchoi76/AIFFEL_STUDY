"""시나리오 실행기: 합성 데이터 생성 → API로 적재 → 날짜별 평가 → 결과 수집.
API만 사용(블랙박스)하므로 MS-C931의 실제 서비스에 그대로 실행 가능."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

from .scenario import Scenario, generate_targets, load_scenario, orbat_rows, report_documents

GRADE_ORDER = {"관심": 0, "주의": 1, "경계": 2, "심각": 3}


def _ok(r):
    if r.status_code >= 400:
        raise RuntimeError(f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:500]}")
    return r.json()


def prepare(client, sc: Scenario, d_day: datetime, out: Path, seed: int) -> dict:
    """정리 → 전투서열·표적·첩보 적재."""
    _ok(client.delete("/targets", params={"area_id": sc.area_id}))
    _ok(client.delete("/documents", params={"area_id": sc.area_id}))

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
    ins = _ok(client.post("/targets", json=[{k: v for k, v in t.items() if k not in ("truth_unit", "false_positive", "day")}
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
    return {"orbat": orbat, "targets": ins.get("inserted"), "reports": len(docs),
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
                       "n_indicators": len(r["hypotheses"][0]["contributions"]), "elapsed_s": r.get("elapsed_s")})
        log(f"  D{day:+d}  등급 {r['grade']:2s}  " + "  ".join(f"{k} {v:.0%}" for k, v in probs.items()))
        final = r
    return series, final


def identification_accuracy(client, sc: Scenario, out: Path, window_days: int = 7) -> dict:
    """평가 마지막 주의 실제 표적(오탐 제외)을 부대 식별 API로 조회해 정답 부대와 비교."""
    from app.orbat.patterns import unit_key

    targets = json.loads((out / "targets.json").read_text(encoding="utf-8"))
    sample = [t for t in targets if not t["false_positive"] and sc.assess[1] - window_days < t["day"] <= sc.assess[1]]
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


def evaluate(sc: Scenario, series: list[dict]) -> dict:
    """정답 대비 평가. 공격 시나리오: 경보 선행일수 / 훈련 시나리오: 오경보 일수."""
    truth_h = sc.truth["hypothesis"]
    final = series[-1]
    most_probable = max(final["probabilities"], key=final["probabilities"].get)
    warn = [s for s in series if GRADE_ORDER.get(s["grade"], 0) >= GRADE_ORDER["경계"]]
    attack_p = [s["probabilities"].get("공격 준비", 0.0) for s in series]
    ev = {"truth": truth_h, "final_most_probable": most_probable, "final_correct": most_probable == truth_h,
          "final_grade": final["grade"], "final_confidence": final["confidence"],
          "max_attack_probability": round(max(attack_p), 4)}
    if truth_h == "공격 준비":
        first = warn[0]["day"] if warn else None
        ev.update({"first_warning_day": first, "warning_lead_days": (-first if first is not None else None),
                   "missed": first is None})
    else:
        ev.update({"false_alarm_days": [s["day"] for s in warn], "false_alarm_count": len(warn)})
    return ev


def run_scenario(client, path: str | Path, out_dir: str | Path, d_day: datetime | None = None,
                 step: int = 2, seed: int = 7, log=print) -> dict:
    sc = load_scenario(path)
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
    result = {"scenario": {"id": sc.id, "name": sc.name, "area_id": sc.area_id, "truth": sc.truth,
                           "d_day": d_day.isoformat(), "seed": seed, "step": step},
              "prepare": prep, "series": series, "final": final, "evaluation": ev}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return result
