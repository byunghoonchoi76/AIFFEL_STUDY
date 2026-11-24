"""시나리오 결과(result.json)의 태세 조건 트랙별 일치율과 단계 변화 시점을 출력.
사용: python scripts/show_readiness.py data/sim_llm_infocon/rus_analog_nk/result.json [--track infocon]
"""
import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("--track", default=None, help="특정 트랙만 (watchcon/defcon/jindogae/durumi/infocon)")
    a = ap.parse_args()
    for f in a.results:
        r = json.loads(Path(f).read_text(encoding="utf-8"))
        ev = r["evaluation"]
        print(f"\n■ {r['scenario']['name']} — 정답 {ev.get('final_correct')}, "
              + (f"경보 D{ev['first_warning_day']:+d}" if ev.get("first_warning_day") is not None else f"오경보 {ev.get('false_alarm_count')}"))
        for tid, tr in ev["readiness"]["tracks"].items():
            if a.track and tid != a.track:
                continue
            mr = tr.get("match_rate")
            head = f"  {tr['name']:4s} 일치율 {mr:.0%} (과잉 {tr['over_days']}일, 미달 {tr['under_days']}일)" if mr is not None else f"  {tr['name']:4s} (정답 일정 없음 — 채점 제외)"
            print(head)
            prev = None
            for x in tr["series"]:
                if x["rec"] != prev:
                    why = "; ".join(x.get("reasons") or [])[:70]
                    print(f"      D{x['day']:+d}  {x['name']}" + (f"  ← {why}" if why else ""))
                    prev = x["rec"]
            for dly in tr.get("escalation_delays", []):
                rd = f"D{dly['rec_day']:+d}" if dly["rec_day"] is not None else "미부합"
                print(f"      정답 {dly['to']} D{dly['truth_day']:+d} / 조건 부합 {rd}")


if __name__ == "__main__":
    main()
