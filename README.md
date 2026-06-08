# 🤖 GenAI 4일 교육 커리큘럼 코드 저장소

생성형 AI의 핵심 개념과 구현을 다루는 4일 집중 학습 코드 모음입니다.

## 📚 커리큘럼

| 일차 | 주제 | 핵심 개념 |
|------|------|-----------|
| [Day 1](./day1_vae/) | VAE (변분 오토인코더) | Latent Space, KL Divergence, ELBO, 재파라미터화 트릭 |
| [Day 2](./day2_ddpm/) | DDPM (확산 모델) | Forward/Reverse Process, U-Net, β 스케줄링 |
| [Day 3](./day3_sbm/) | Score-Based Model | Score Function, Langevin Dynamics, SDE/ODE |
| [Day 4](./day4_rag_agent/) | RAG + Agentic AI | 임베딩, VectorDB, LangGraph, AutoGen |

## 🛠️ 환경 설정

```bash
# 레포 클론
git clone https://github.com/<your-username>/genai-study.git
cd genai-study

# 가상환경 생성 (권장)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

## 📁 프로젝트 구조

```
genai-study/
├── README.md
├── requirements.txt
├── day1_vae/
│   ├── README.md
│   ├── vae_principle.ipynb
│   ├── vae_code.ipynb
│   └── src/
│       ├── model.py        # VAE 모델 클래스
│       ├── loss.py         # ELBO, KL Loss
│       └── train.py        # 학습 루프
├── day2_ddpm/
│   ├── README.md
│   ├── ddpm_principle.ipynb
│   ├── ddpm_code.ipynb
│   └── src/
│       ├── diffusion.py    # Forward/Reverse process
│       ├── unet.py         # U-Net 아키텍처
│       └── scheduler.py    # β 스케줄링
├── day3_sbm/
│   ├── README.md
│   ├── sbm_principle.ipynb
│   ├── sbm_code.ipynb
│   └── src/
│       ├── score_network.py
│       ├── langevin.py
│       └── sde_solver.py
└── day4_rag_agent/
    ├── README.md
    ├── rag_code.ipynb
    ├── agentic_langgraph.ipynb
    ├── agentic_autogen.ipynb
    └── src/
        ├── rag_pipeline.py
        ├── advanced_rag.py
        ├── agent_graph.py
        └── agent_autogen.py
```

## 🔗 관련 자료

- 📝 [노션 학습 정리](https://app.notion.com/p/379b934efb05811a91cdec957176d6c6)
- 📄 논문: [VAE (Kingma & Welling, 2013)](https://arxiv.org/abs/1312.6114)
- 📄 논문: [DDPM (Ho et al., 2020)](https://arxiv.org/abs/2006.11239)
- 📄 논문: [Score-Based (Song et al., 2021)](https://arxiv.org/abs/2011.13456)
