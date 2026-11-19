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


def run(client: OpenAI, model: str, max_tokens: int, effort: str = "medium") -> dict:
    """gpt-oss는 추론(reasoning) 토큰을 먼저 생성하고 답변(content)을 생성함.
    - ttft: 첫 토큰(추론 포함)까지 시간
    - answer_latency: 첫 답변 문자가 화면에 보이기까지 시간
    - tps: 전체 생성 토큰(추론+답변) / 첫 토큰 이후 생성 시간"""
    t0 = time.perf_counter()
    first_any = first_content = None
    n_chunks = 0
    usage_tokens = None
    stream = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": PROMPT}], max_tokens=max_tokens,
        temperature=0.0, stream=True, stream_options={"include_usage": True}, reasoning_effort=effort,
    )
    for ev in stream:
        if ev.usage:
            usage_tokens = ev.usage.completion_tokens
        if not ev.choices or not ev.choices[0].delta:
            continue
        d = ev.choices[0].delta
        reasoning = getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None)
        if reasoning or d.content:
            now = time.perf_counter()
            first_any = first_any or now
            n_chunks += 1
            if d.content and first_content is None:
                first_content = now
    t1 = time.perf_counter()
    tokens = usage_tokens or n_chunks
    start = first_any or t0
    return {
        "ttft": start - t0,
        "answer_latency": (first_content or t1) - t0,
        "tps": tokens / max(t1 - start, 1e-6),
        "total": t1 - t0,
        "tokens": tokens,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="gpt-oss")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--reasoning-effort", default="medium", choices=["low", "medium", "high"])
    a = ap.parse_args()
    client = OpenAI(base_url=a.base_url, api_key="not-needed")
    run(client, a.model, 32, a.reasoning_effort)  # 워밍업
    res = [run(client, a.model, a.max_tokens, a.reasoning_effort) for _ in range(a.runs)]
    for i, r in enumerate(res, 1):
        print(f"run{i}: TTFT {r['ttft']:.2f}s, 첫 답변 {r['answer_latency']:.2f}s, "
              f"{r['tps']:.1f} tok/s, 전체 {r['total']:.2f}s, {r['tokens']} tokens")
    med = {k: statistics.median(r[k] for r in res) for k in res[0]}
    print(f"median: TTFT {med['ttft']:.2f}s, 첫 답변 {med['answer_latency']:.2f}s, "
          f"{med['tps']:.1f} tok/s, 전체 {med['total']:.2f}s")


if __name__ == "__main__":
    main()
