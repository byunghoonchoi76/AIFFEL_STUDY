# Day 4 — RAG + Agentic AI

## 파일 구조
```
day4_rag_agent/
├── rag_code.ipynb
├── agentic_langgraph.ipynb
├── agentic_autogen.ipynb
└── src/
    ├── rag_pipeline.py    # BasicRAG + AdvancedRAG (HyDE, Multi-Query, Hybrid)
    └── agent_graph.py     # LangGraph ReAct 에이전트
```

## 환경 변수 설정
```bash
export OPENAI_API_KEY="your-key-here"
```

## 핵심 클래스
- `BasicRAG` — 기본 RAG 파이프라인
- `AdvancedRAG` — HyDE, Multi-Query, Hybrid Search
- `build_react_agent()` — LangGraph ReAct 에이전트
