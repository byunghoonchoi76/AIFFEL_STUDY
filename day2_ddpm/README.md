# Day 2 — DDPM (확산 모델)

## 파일 구조
```
day2_ddpm/
├── ddpm_principle.ipynb
├── ddpm_code.ipynb
└── src/
    ├── diffusion.py    # Forward/Reverse process, 샘플링
    ├── unet.py         # 노이즈 예측 U-Net
    └── scheduler.py    # Linear / Cosine β 스케줄
```

## 핵심 클래스
- `Diffusion` — `.add_noise()`, `.reverse_step()`, `.sample()` 메서드
- Linear & Cosine 스케줄 지원
