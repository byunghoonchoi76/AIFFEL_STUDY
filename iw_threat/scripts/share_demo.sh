#!/usr/bin/env bash
# 읽기 전용 공유 화면을 띄우고 Cloudflare 임시 링크를 만든다 (외부 시연용).
# - 합성 시나리오 결과(demo/*/result.json)만 표시, 업로드·API 호출·피드백 저장 없음
# - 화면은 127.0.0.1에만 열리고, 외부 접속은 cloudflared 터널로만 가능
# - Ctrl+C 한 번으로 화면과 터널을 함께 종료
# 사용: bash scripts/share_demo.sh            (기본 포트 8502 — 8501은 docker compose ui가 사용)
#       PORT=8600 bash scripts/share_demo.sh
#       NO_TUNNEL=1 bash scripts/share_demo.sh  (터널 없이 이 장비에서만 확인)
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8502}"

if ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
  echo "포트 ${PORT}을 이미 다른 프로그램이 쓰고 있습니다. PORT=다른번호 bash scripts/share_demo.sh 로 실행하세요." >&2
  exit 1
fi

SHARE_MODE=1 DEMO_DIR="${DEMO_DIR:-demo}" streamlit run ui/streamlit_app.py \
  --server.port "$PORT" --server.address 127.0.0.1 --server.headless true \
  --client.toolbarMode viewer --server.fileWatcherType none --browser.gatherUsageStats false &
ST_PID=$!
trap 'kill "$ST_PID" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 30); do
  ss -ltn 2>/dev/null | grep -q ":${PORT} " && break
  sleep 1
done
echo "화면: http://localhost:${PORT} (읽기 전용 공유 모드)"

if [ -n "${NO_TUNNEL:-}" ]; then
  wait "$ST_PID"
else
  command -v cloudflared >/dev/null || { echo "cloudflared가 없습니다. 설치 후 다시 실행하세요." >&2; exit 1; }
  echo "아래에 나오는 https://...trycloudflare.com 주소가 공유 링크입니다. 링크를 아는 사람은 누구나 열 수 있으니 시연 후 Ctrl+C로 종료하세요."
  cloudflared tunnel --no-autoupdate --url "http://localhost:${PORT}"
fi
