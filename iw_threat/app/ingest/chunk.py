"""문단 단위 청크 분할 (문단 경계 우선, 최대 길이 초과 시 문장 단위로 분할)."""
from __future__ import annotations

import re


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    units: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            units.append(p)
        else:
            units.extend(s.strip() for s in re.split(r"(?<=[.!?。다])\s+", p) if s.strip())

    chunks: list[str] = []
    cur = ""
    for u in units:
        while len(u) > max_chars:  # 문장 하나가 너무 긴 경우 강제 분할
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(u[:max_chars])
            u = u[max_chars - overlap:]
        if cur and len(cur) + 1 + len(u) > max_chars:
            chunks.append(cur)
            cur = cur[-overlap:] + " " + u if overlap else u
        else:
            cur = f"{cur}\n{u}" if cur else u
    if cur:
        chunks.append(cur)
    return chunks
