"""
Score-Based Model - Score Network & Langevin Dynamics
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from tqdm import tqdm


class ScoreNetwork(nn.Module):
    """
    Score Network: s_θ(x, σ) ≈ ∇_x log p_σ(x)

    Args:
        data_dim: 데이터 차원
        hidden_dim: 은닉층 차원
        n_sigmas: 노이즈 레벨 수

    Example:
        >>> sigmas = torch.logspace(-2, 0, 10)  # 0.01 ~ 1.0
        >>> net = ScoreNetwork(data_dim=784, hidden_dim=256, n_sigmas=10)
        >>> x = torch.randn(32, 784)
        >>> sigma_idx = torch.randint(0, 10, (32,))
        >>> score = net(x, sigmas[sigma_idx])
    """

    def __init__(self, data_dim: int, hidden_dim: int = 256, n_sigmas: int = 10):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(data_dim + 1, hidden_dim),  # +1: σ 조건
            nn.SiLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.SiLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.SiLU(),
            nn.Linear(hidden_dim, data_dim),
        )

    def forward(self, x: torch.Tensor, sigma: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: 입력 데이터 [B, D]
            sigma: 노이즈 표준편차 [B] 또는 스칼라

        Returns:
            score: 추정된 Score [B, D]
        """
        if sigma.ndim == 0:
            sigma = sigma.expand(x.size(0))

        sigma_emb = sigma.unsqueeze(-1)  # [B, 1]
        x_in = torch.cat([x, sigma_emb], dim=-1)  # [B, D+1]

        # Score = -ε_θ(x, σ) / σ (Denoising Score Matching과의 관계)
        return self.network(x_in) / sigma.unsqueeze(-1)


def dsm_loss(
    score_net: ScoreNetwork,
    x: torch.Tensor,
    sigmas: torch.Tensor,
) -> torch.Tensor:
    """
    Denoising Score Matching 손실
    L = E_{σ, x, ε} [σ² * ||s_θ(x+σε, σ) - (-ε/σ)||²]

    Args:
        score_net: Score Network
        x: 원본 데이터 [B, D]
        sigmas: 노이즈 레벨 목록

    Returns:
        scalar loss
    """
    # 랜덤 노이즈 레벨 선택
    sigma_idx = torch.randint(0, len(sigmas), (x.size(0),), device=x.device)
    sigma = sigmas[sigma_idx]  # [B]

    # 노이즈 추가
    eps = torch.randn_like(x)
    x_noisy = x + sigma.unsqueeze(-1) * eps

    # Score 예측
    score_pred = score_net(x_noisy, sigma)
    target_score = -eps / sigma.unsqueeze(-1)  # 이상적인 Score

    # 가중치: σ²이 클수록 더 중요 (노이즈 큰 경우에 집중)
    weight = sigma ** 2
    loss = weight.unsqueeze(-1) * (score_pred - target_score) ** 2
    return loss.mean()


@torch.no_grad()
def annealed_langevin_dynamics(
    score_net: ScoreNetwork,
    sigmas: torch.Tensor,
    n_samples: int,
    data_dim: int,
    n_steps: int = 100,
    step_size: float = 1e-3,
    device: str = "cpu",
) -> torch.Tensor:
    """
    Annealed Langevin Dynamics 샘플링
    높은 σ → 낮은 σ 순서로 단계적 샘플링

    Args:
        score_net: 학습된 Score Network
        sigmas: 내림차순 노이즈 레벨 목록
        n_samples: 샘플링 수
        data_dim: 데이터 차원
        n_steps: 각 레벨에서의 Langevin 스텝 수
        step_size: 스텝 크기 α

    Returns:
        생성된 샘플 [n_samples, data_dim]
    """
    score_net.eval()
    x = torch.randn(n_samples, data_dim).to(device)

    for sigma in tqdm(sigmas, desc="Annealed Langevin"):
        sigma_tensor = torch.full((n_samples,), sigma.item(), device=device)
        # 각 σ 레벨에서의 스텝 크기 조정
        alpha = step_size * (sigma / sigmas[-1]) ** 2

        for _ in range(n_steps):
            score = score_net(x, sigma_tensor)
            noise = torch.randn_like(x)
            x = x + alpha * score + (2 * alpha).sqrt() * noise

    return x
