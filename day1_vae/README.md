# Day 1 — VAE (변분 오토인코더)

## 파일 구조
```
day1_vae/
├── vae_principle.ipynb   # 원리 노트북 (노션 Day 1과 동일 내용)
├── vae_code.ipynb         # 실습 노트북
└── src/
    ├── model.py           # VAE 클래스 (Encoder, Decoder, 재파라미터화)
    ├── loss.py            # ELBO = 재구성 손실 + KL Divergence
    └── train.py           # MNIST 학습 스크립트
```

## 빠른 시작
```bash
cd src
python train.py
```

## 핵심 클래스
- `VAE` — 전체 모델, `.sample()`, `.encode()` 메서드 포함
- `vae_loss()` — β-VAE 지원 손실 함수
