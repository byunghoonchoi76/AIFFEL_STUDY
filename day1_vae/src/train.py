"""
VAE 학습 스크립트
"""
import torch
import torch.optim as optim
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
from tqdm import tqdm

from model import VAE
from loss import vae_loss


def get_mnist_dataloader(batch_size: int = 128) -> tuple[DataLoader, DataLoader]:
    transform = transforms.Compose([
        transforms.ToTensor(),
    ])
    train_ds = datasets.MNIST("./data", train=True, download=True, transform=transform)
    test_ds = datasets.MNIST("./data", train=False, download=True, transform=transform)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)
    return train_loader, test_loader


def train_epoch(model, loader, optimizer, device):
    model.train()
    total_loss = 0
    for x, _ in tqdm(loader, desc="Training", leave=False):
        x = x.to(device)
        optimizer.zero_grad()
        recon, mu, log_var = model(x)
        losses = vae_loss(recon, x, mu, log_var)
        losses["total"].backward()
        optimizer.step()
        total_loss += losses["total"].item()
    return total_loss / len(loader)


def main():
    # 하이퍼파라미터
    LATENT_DIM = 2
    HIDDEN_DIM = 256
    BATCH_SIZE = 128
    EPOCHS = 20
    LR = 1e-3
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"Device: {DEVICE}")

    # 데이터 로드
    train_loader, _ = get_mnist_dataloader(BATCH_SIZE)

    # 모델 초기화
    model = VAE(input_dim=784, hidden_dim=HIDDEN_DIM, latent_dim=LATENT_DIM).to(DEVICE)
    optimizer = optim.Adam(model.parameters(), lr=LR)

    # 학습
    for epoch in range(1, EPOCHS + 1):
        loss = train_epoch(model, train_loader, optimizer, DEVICE)
        print(f"Epoch {epoch:02d} | Loss: {loss:.4f}")

    # 모델 저장
    torch.save(model.state_dict(), "vae_mnist.pt")
    print("✅ 모델 저장 완료: vae_mnist.pt")


if __name__ == "__main__":
    main()
