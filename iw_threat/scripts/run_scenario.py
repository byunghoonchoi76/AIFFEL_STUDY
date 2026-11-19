"""합성 시나리오 실행 → 분석보고서 생성.
사용: python scripts/run_scenario.py scenarios/attack_prep.yaml scenarios/large_exercise.yaml --api http://localhost:8080
산출: data/sim/<시나리오>/result.json, 분석보고서_<시나리오>.docx (+ 추이 그래프 png)
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx  # noqa: E402

from sim.report import build_report  # noqa: E402
from sim.run import run_scenario  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--out", default="data/sim")
    ap.add_argument("--step", type=int, default=2, help="평가 간격(일)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--d-day", default=None, help="D일 (YYYY-MM-DD, 기본: 오늘)")
    a = ap.parse_args()
    d_day = datetime.fromisoformat(a.d_day).astimezone() if a.d_day else None
    summary = []
    with httpx.Client(base_url=a.api, timeout=900) as client:
        for path in a.scenarios:
            res = run_scenario(client, path, a.out, d_day, a.step, a.seed)
            summary.append(summarize(res))
            ev = res["evaluation"]
            out = Path(a.out) / res["scenario"]["id"] / f"분석보고서_{res['scenario']['id']}.docx"
            build_report(res, out)
            print(f"  평가: 최종 최고확률 가설 {ev['final_most_probable']} ({'정답' if ev['final_correct'] else '오답'}), "
                  + (f"경보 선행 {ev['warning_lead_days']}일" if ev.get("warning_lead_days") is not None else
                     f"오경보 {ev.get('false_alarm_count', '-')}회" if "false_alarm_count" in ev else "경보 없음")
                  + f", 부대 식별 정확도 {ev['identification']['accuracy']:.0%}")
            print(f"  보고서: {out}\n")
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"종합: {Path(a.out) / 'summary.json'}")


def summarize(res: dict) -> dict:
    ev, s = res["evaluation"], res["series"]
    return {"id": res["scenario"]["id"], "name": res["scenario"]["name"], "truth": ev["truth"],
            "final_most_probable": ev["final_most_probable"], "final_correct": ev["final_correct"],
            "final_grade": ev["final_grade"], "final_confidence": ev["final_confidence"],
            "final_probabilities": s[-1]["probabilities"], "max_attack_probability": ev["max_attack_probability"],
            "warning_lead_days": ev.get("warning_lead_days"), "false_alarm_count": ev.get("false_alarm_count"),
            "identification_accuracy": ev["identification"]["accuracy"],
            "series": [{"day": x["day"], "grade": x["grade"], **x["probabilities"]} for x in s]}


if __name__ == "__main__":
    main()
