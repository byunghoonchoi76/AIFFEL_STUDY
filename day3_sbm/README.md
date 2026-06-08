# Day 3 — Score-Based Model

## 파일 구조
```
day3_sbm/
├── sbm_principle.ipynb
├── sbm_code.ipynb
└── src/
    ├── score_network.py   # ScoreNetwork 클래스 + DSM 손실 + Annealed Langevin
    └── sde_solver.py      # SDE/ODE 솔버 (Euler-Maruyama, Probability Flow ODE)
```

## 핵심 함수
- `dsm_loss()` — Denoising Score Matching 손실
- `annealed_langevin_dynamics()` — 고품질 샘플링
