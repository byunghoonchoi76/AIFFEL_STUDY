#!/usr/bin/env bash
# MS-C931에서 언어모델(gpt-oss-20b) 적용 상태로 시나리오 11종을 다시 돌리고, 간이 방식 결과와 비교한다.
# 전제: docker compose up -d 로 llm·api·DB가 떠 있어야 함 (API: http://localhost:8080)
# 사용: bash scripts/remeasure_llm.sh            (결과: data/sim_llm/)
set -euo pipefail
cd "$(dirname "$0")/.."
API="${API:-http://localhost:8080}"
OUT="${OUT:-data/sim_llm}"
D_DAY="2026-12-20"          # 기준 결과와 같은 D일·seed로 맞춤

echo "[1/4] API 상태 확인: $API"
for i in $(seq 1 36); do                     # API 재기동 직후를 고려해 최대 3분 대기
  curl -sf "$API/health" >/dev/null && break
  [ "$i" = 36 ] && { echo "API가 3분 동안 응답하지 않습니다. docker compose logs --tail 50 api 로 오류를 확인하세요." >&2; exit 1; }
  sleep 5
done
echo "      API 응답 확인"

echo "[2/4] 언어모델 응답 확인"
docker compose ps llm 2>/dev/null | tail -n +2 || true

mkdir -p "$OUT"
START=$(date +%s)
echo "[3/4] 시나리오 10종 (2일 간격 평가)"
python scripts/run_scenario.py \
  scenarios/attack_prep.yaml scenarios/large_exercise.yaml scenarios/peacetime.yaml scenarios/deception.yaml \
  scenarios/info_gap.yaml scenarios/infiltration.yaml scenarios/air_provocation.yaml scenarios/local_fire_2015.yaml \
  scenarios/rus_analog_nk.yaml scenarios/rus_analog_nk_ps.yaml \
  --api "$API" --out "$OUT" --d-day "$D_DAY" --step 2 --seed 7 2>&1 | tee "$OUT/run.log"
echo "      대항군 군단 (1일 간격 평가)"
python scripts/run_scenario.py scenarios/opfor_corps --api "$API" --out "$OUT" --d-day "$D_DAY" --step 1 --seed 7 2>&1 | tee -a "$OUT/run.log"
END=$(date +%s)
echo "총 소요 시간: $(( (END-START)/60 ))분 $(( (END-START)%60 ))초" | tee -a "$OUT/run.log"

echo "[4/4] 간이 방식 결과와 비교"
python scripts/compare_results.py --new "$OUT" --base sim/baseline/keyword_2026-11-23.json --out "$OUT/compare_llm_vs_keyword.json" | tee "$OUT/compare.txt"
echo "완료 — 결과 폴더: $OUT (run.log, compare.txt, 시나리오별 result.json·분석보고서)"
