"""
DDPM - Forward & Reverse Diffusion Process
"""
import torch
import torch.nn as nn
from typing import Optional


class Diffusion:
    """
    DDPM Forward/Reverse Diffusion Process

    Args:
        T: 총 타임스텝 수
        beta_start: β 시작값
        beta_end: β 끝값
        schedule: 'linear' 또는 'cosine'

    Example:
        >>> diffusion = Diffusion(T=1000)
        >>> x0 = torch.randn(4, 3, 32, 32)
        >>> t = torch.randint(0, 1000, (4,))
        >>> x_t, noise = diffusion.add_noise(x0, t)
    """

    def __init__(
        self,
        T: int = 1000,
        beta_start: float = 1e-4,
        beta_end: float = 0.02,
        schedule: str = "linear",
    ):
        self.T = T
        self.betas = self._get_schedule(T, beta_start, beta_end, schedule)
        self.alphas = 1.0 - self.betas
        self.alpha_bars = torch.cumprod(self.alphas, dim=0)
        self.sqrt_alpha_bars = self.alpha_bars.sqrt()
        self.sqrt_one_minus_alpha_bars = (1 - self.alpha_bars).sqrt()

    def _get_schedule(self, T, beta_start, beta_end, schedule):
        if schedule == "linear":
            return torch.linspace(beta_start, beta_end, T)
        elif schedule == "cosine":
            # Nichol & Dhariwal (2021) Cosine Schedule
            s = 0.008
            steps = torch.arange(T + 1, dtype=torch.float64)
            f = torch.cos((steps / T + s) / (1 + s) * torch.pi / 2) ** 2
            alphas_bar = f / f[0]
            betas = 1 - (alphas_bar[1:] / alphas_bar[:-1])
            return betas.clamp(0, 0.999).float()
        else:
            raise ValueError(f"Unknown schedule: {schedule}")

    def add_noise(
        self, x0: torch.Tensor, t: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Forward process: q(x_t | x_0)
        x_t = √ᾱ_t * x_0 + √(1-ᾱ_t) * ε,  ε ~ N(0, I)

        Args:
            x0: 원본 데이터 [B, ...]
            t: 타임스텝 인덱스 [B]

        Returns:
            x_t: 노이즈가 추가된 데이터
            noise: 추가된 노이즈
        """
        device = x0.device
        sqrt_ab = self.sqrt_alpha_bars[t].to(device)
        sqrt_1_ab = self.sqrt_one_minus_alpha_bars[t].to(device)

        # 브로드캐스팅을 위한 차원 맞춤
        for _ in range(x0.ndim - 1):
            sqrt_ab = sqrt_ab.unsqueeze(-1)
            sqrt_1_ab = sqrt_1_ab.unsqueeze(-1)

        noise = torch.randn_like(x0)
        x_t = sqrt_ab * x0 + sqrt_1_ab * noise
        return x_t, noise

    def reverse_step(
        self,
        x_t: torch.Tensor,
        t: int,
        predicted_noise: torch.Tensor,
    ) -> torch.Tensor:
        """
        Reverse process: p_θ(x_{t-1} | x_t)
        DDPM 논문 Algorithm 2

        Args:
            x_t: 현재 타임스텝의 노이즈 데이터
            t: 현재 타임스텝 (int)
            predicted_noise: U-Net이 예측한 노이즈

        Returns:
            x_{t-1}: 노이즈가 제거된 데이터
        """
        device = x_t.device
        beta_t = self.betas[t].to(device)
        alpha_t = self.alphas[t].to(device)
        alpha_bar_t = self.alpha_bars[t].to(device)

        # μ_θ(x_t, t) 계산
        coef = beta_t / (1 - alpha_bar_t).sqrt()
        mu = (1 / alpha_t.sqrt()) * (x_t - coef * predicted_noise)

        if t == 0:
            return mu

        # 분산: σ_t² = β_t (단순화된 버전)
        sigma_t = beta_t.sqrt()
        z = torch.randn_like(x_t)
        return mu + sigma_t * z

    @torch.no_grad()
    def sample(self, model: nn.Module, n_samples: int, img_shape: tuple, device: str = "cpu"):
        """
        완전한 역방향 샘플링 (생성)

        Args:
            model: 노이즈 예측 U-Net
            n_samples: 생성할 샘플 수
            img_shape: (C, H, W)
            device: 디바이스

        Returns:
            생성된 이미지 [n_samples, C, H, W]
        """
        model.eval()
        x = torch.randn(n_samples, *img_shape).to(device)  # x_T ~ N(0, I)

        for t in reversed(range(self.T)):
            t_tensor = torch.full((n_samples,), t, dtype=torch.long, device=device)
            predicted_noise = model(x, t_tensor)
            x = self.reverse_step(x, t, predicted_noise)

        return x.clamp(-1, 1)
