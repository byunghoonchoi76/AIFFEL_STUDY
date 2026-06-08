"""
VAE (Variational Autoencoder) 모델
Day 1 학습 내용 기반 구현
"""
import torch
import torch.nn as nn


class Encoder(nn.Module):
    """입력 데이터를 잠재 분포 파라미터(μ, σ)로 변환"""

    def __init__(self, input_dim: int, hidden_dim: int, latent_dim: int):
        super().__init__()
        self.fc = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.fc_mu = nn.Linear(hidden_dim, latent_dim)
        self.fc_log_var = nn.Linear(hidden_dim, latent_dim)

    def forward(self, x: torch.Tensor):
        h = self.fc(x)
        mu = self.fc_mu(h)
        log_var = self.fc_log_var(h)
        return mu, log_var


class Decoder(nn.Module):
    """잠재 벡터 z를 원본 데이터 공간으로 복원"""

    def __init__(self, latent_dim: int, hidden_dim: int, output_dim: int):
        super().__init__()
        self.fc = nn.Sequential(
            nn.Linear(latent_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim),
            nn.Sigmoid(),  # 픽셀값 [0, 1] 범위로 정규화
        )

    def forward(self, z: torch.Tensor):
        return self.fc(z)


class VAE(nn.Module):
    """
    Variational Autoencoder

    Args:
        input_dim: 입력 차원 (예: MNIST = 784)
        hidden_dim: 은닉층 차원
        latent_dim: 잠재 공간 차원

    Example:
        >>> model = VAE(input_dim=784, hidden_dim=256, latent_dim=2)
        >>> x = torch.randn(32, 784)
        >>> recon, mu, log_var = model(x)
    """

    def __init__(self, input_dim: int = 784, hidden_dim: int = 256, latent_dim: int = 2):
        super().__init__()
        self.encoder = Encoder(input_dim, hidden_dim, latent_dim)
        self.decoder = Decoder(latent_dim, hidden_dim, input_dim)
        self.latent_dim = latent_dim

    def reparameterize(self, mu: torch.Tensor, log_var: torch.Tensor) -> torch.Tensor:
        """
        재파라미터화 트릭: z = μ + σ * ε, ε ~ N(0, I)
        역전파 가능하게 만드는 핵심 기법
        """
        if self.training:
            std = torch.exp(0.5 * log_var)
            eps = torch.randn_like(std)
            return mu + eps * std
        else:
            return mu  # 추론 시에는 평균값 사용

    def forward(self, x: torch.Tensor):
        # Flatten (이미지인 경우)
        x_flat = x.view(x.size(0), -1)

        # Encode
        mu, log_var = self.encoder(x_flat)

        # 잠재 벡터 샘플링
        z = self.reparameterize(mu, log_var)

        # Decode
        recon = self.decoder(z)

        return recon, mu, log_var

    @torch.no_grad()
    def sample(self, n_samples: int, device: str = "cpu") -> torch.Tensor:
        """N(0, I)에서 샘플링하여 새로운 데이터 생성"""
        z = torch.randn(n_samples, self.latent_dim).to(device)
        return self.decoder(z)

    @torch.no_grad()
    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """데이터를 잠재 벡터로 인코딩 (μ 반환)"""
        x_flat = x.view(x.size(0), -1)
        mu, _ = self.encoder(x_flat)
        return mu
