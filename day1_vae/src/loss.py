"""
VAE 손실 함수
ELBO = 재구성 손실 + KL Divergence
"""
import torch
import torch.nn.functional as F


def reconstruction_loss(recon_x: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
    """
    재구성 손실 (Binary Cross Entropy)
    픽셀값을 베르누이 분포로 가정
    """
    x_flat = x.view(x.size(0), -1)
    return F.binary_cross_entropy(recon_x, x_flat, reduction="sum")


def kl_divergence(mu: torch.Tensor, log_var: torch.Tensor) -> torch.Tensor:
    """
    KL Divergence: q(z|x) || p(z) = N(0, I)
    Closed-form: -0.5 * Σ(1 + log σ² - μ² - σ²)
    """
    return -0.5 * torch.sum(1 + log_var - mu.pow(2) - log_var.exp())


def vae_loss(
    recon_x: torch.Tensor,
    x: torch.Tensor,
    mu: torch.Tensor,
    log_var: torch.Tensor,
    beta: float = 1.0,
) -> dict:
    """
    VAE 총 손실 = 재구성 손실 + β * KL Divergence

    Args:
        recon_x: 재구성된 데이터
        x: 원본 데이터
        mu: 잠재 분포 평균
        log_var: 잠재 분포 로그 분산
        beta: KL 가중치 (β-VAE에서 > 1로 설정)

    Returns:
        loss_dict: total, recon, kl 손실값 딕셔너리
    """
    recon = reconstruction_loss(recon_x, x)
    kl = kl_divergence(mu, log_var)
    total = recon + beta * kl

    batch_size = x.size(0)
    return {
        "total": total / batch_size,
        "recon": recon / batch_size,
        "kl": kl / batch_size,
    }
