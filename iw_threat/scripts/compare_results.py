"""재측정 결과(언어모델 적용)를 기준값(간이 방식)과 비교해 표로 출력하고 JSON으로 저장.
사용: python scripts/compare_results.py --new data/sim --base sim/baseline/keyword_2026-11-23.json
"""
import argparse
import json
from pathlib import Path


def summarize(r: dict) -> dict:
    ev, f = r["evaluation"], r["final"]
    top = max(f["hypotheses"], key=lambda h: h["probability"])
    rd = {k: round(v["match_rate"], 3) for k, v in (ev.get("readiness") or {}).get("tracks", {}).items() if v.get("match_rate") is not None}
    return {"id": r["scenario"]["id"], "name": r["scenario"]["name"], "truth": r["scenario"]["truth"].get("intent"),
            "final_correct": ev.get("final_correct"), "final_top": top["name"], "final_top_p": round(top["probability"], 4),
            "max_attack_p": round(ev.get("max_attack_probability") or 0, 4), "first_warning_day": ev.get("first_warning_day"),
            "false_alarms": ev.get("false_alarm_count"), "readiness_match": rd}


def warn(s):
    if s.get("first_warning_day") is not None:
        return f"D{s['first_warning_day']:+d}"
    return f"오경보 {s.get('false_alarms') or 0}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", default="data/sim")
    ap.add_argument("--base", default="sim/baseline/keyword_2026-11-23.json")
    ap.add_argument("--out", default="data/sim/compare_llm_vs_keyword.json")
    a = ap.parse_args()
    base = json.loads(Path(a.base).read_text(encoding="utf-8"))["scenarios"]
    new = {}
    for f in sorted(Path(a.new).glob("*/result.json")):
        s = summarize(json.loads(f.read_text(encoding="utf-8")))
        new[s["id"]] = s
    rows, same = [], 0
    print(f"{'시나리오':24s} {'정답(간이→LLM)':14s} {'최종 판단(LLM)':30s} {'경보(간이→LLM)':18s} 공격최대(간이→LLM)")
    for sid, b in base.items():
        n = new.get(sid)
        if not n:
            print(f"{b['name'][:22]:24s} 재측정 결과 없음"); continue
        ok = (b["final_correct"] == n["final_correct"]) and (warn(b) == warn(n))
        same += ok
        print(f"{b['name'][:22]:24s} {str(b['final_correct'])+'→'+str(n['final_correct']):14s} "
              f"{(n['final_top'][:16] + ' ' + format(n['final_top_p'], '.0%')):30s} {warn(b)+' → '+warn(n):18s} "
              f"{b['max_attack_p']:.0%} → {n['max_attack_p']:.0%}" + ("" if ok else "   ◀ 차이"))
        rows.append({"id": sid, "keyword": b, "llm": n, "same": ok})
    correct = sum(1 for r in rows if r["llm"]["final_correct"])
    print(f"\n언어모델 적용 결과: 최종 판단 정답 {correct}/{len(rows)}, 간이 방식과 판단·경보가 같은 시나리오 {same}/{len(rows)}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"rows": rows, "llm_correct": correct, "same": same}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {a.out}")


if __name__ == "__main__":
    main()
