"""vLLM(OpenAI 호환) 클라이언트 — JSON 응답 강제 + 실패 시 재시도."""
from __future__ import annotations

import json
import re

from openai import OpenAI

from .config import get_settings


class LLM:
    def __init__(self):
        s = get_settings()
        self.client = OpenAI(base_url=s.llm_base_url, api_key="not-needed", timeout=s.llm_timeout_s)
        self.model = s.llm_served_name

    def chat_json(self, system: str, user: str, max_tokens: int = 2048, retries: int = 1,
                  reasoning_effort: str = "low") -> dict:
        """reasoning_effort: gpt-oss 추론 강도(low/medium/high). 분류는 low, 해설은 medium 권장."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            kwargs = {"response_format": {"type": "json_object"}} if attempt == 0 else {}
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=messages, temperature=0.0, max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort, **kwargs,
                )
                return parse_json(resp.choices[0].message.content or "")
            except Exception as e:  # noqa: BLE001 — 형식 오류·서버 오류 모두 재시도
                last_err = e
        raise RuntimeError(f"LLM JSON 응답 실패: {last_err}")


def parse_json(text: str) -> dict:
    """응답 본문에서 첫 JSON 객체를 추출."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))
