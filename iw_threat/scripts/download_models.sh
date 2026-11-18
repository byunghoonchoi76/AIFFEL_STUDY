#!/usr/bin/env bash
# 모델 가중치를 ./models(HF 캐시 구조)에 내려받음. 이후 .env의 HF_HUB_OFFLINE=1로 오프라인 기동 가능.
# 중국계 모델·파생 모델 배제: 추가 모델은 모델 카드의 base_model까지 확인할 것.
set -euo pipefail
cd "$(dirname "$0")/.."
export HF_HOME="$PWD/models"
pip install -q -U "huggingface_hub[cli]"
huggingface-cli download openai/gpt-oss-120b
huggingface-cli download openai/gpt-oss-20b
huggingface-cli download intfloat/multilingual-e5-large-instruct
echo "완료: $HF_HOME"
