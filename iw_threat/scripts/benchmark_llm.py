"""LLM 실측: 첫 토큰 시간(TTFT)과 토큰 생성 속도.
사용: python scripts/benchmark_llm.py --base-url http://localhost:8000/v1 --model gpt-oss --runs 3
"""
import argparse
import statistics
import time

from openai import OpenAI

PROMPT = ("다음 첩보를 읽고 적의 공격 준비 징후에 해당하는 내용을 근거와 함께 5문장으로 요약하라.\n"
          "첩보: ○○지역에서 전차 30여 대와 도하장비 4대가 전방으로 이동했고, 야전병원 천막 3동이 새로 설치되었다. "
          "보급차량 통행이 평소의 2배로 증가했다는 현지 보고가 있다.")


def run(client: OpenAI, model: str, max_tokens: int) -> tuple[float, float, int]:
    t0 = time.perf_counter()
    first = None
    n_chunks = 0
    usage_tokens = None
    stream = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": PROMPT}], max_tokens=max_tokens,
        temperature=0.0, stream=True, stream_options={"include_usage": True},
    )
    for ev in stream:
        if ev.usage:
            usage_tokens = ev.usage.completion_tokens
        if ev.choices and ev.choices[0].delta and ev.choices[0].delta.content:
            if first is None:
                first = time.perf_counter()
            n_chunks += 1
    t1 = time.perf_counter()
    tokens = usage_tokens or n_chunks
    ttft = (first or t1) - t0
    tps = tokens / max(t1 - (first or t0), 1e-6)
    return ttft, tps, tokens


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="gpt-oss")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=512)
    a = ap.parse_args()
    client = OpenAI(base_url=a.base_url, api_key="not-needed")
    run(client, a.model, 32)  # 워밍업
    res = [run(client, a.model, a.max_tokens) for _ in range(a.runs)]
    for i, (ttft, tps, n) in enumerate(res, 1):
        print(f"run{i}: TTFT {ttft:.2f}s, {tps:.1f} tok/s, {n} tokens")
    print(f"median: TTFT {statistics.median(r[0] for r in res):.2f}s, "
          f"{statistics.median(r[1] for r in res):.1f} tok/s")


if __name__ == "__main__":
    main()
