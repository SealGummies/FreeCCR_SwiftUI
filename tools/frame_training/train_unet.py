"""Train a tiny U-Net frame segmenter on the synthetic data and export ONNX.

Input/output mirror the existing dust model (src/core/dust_detect.py): 1-channel
luma normalized to [-1,1], shape [1,1,H,W] (H,W multiple of 16), output a logit
map -> sigmoid -> frame probability. Exported with dynamic spatial axes so
inference can run at short-side 512 like the dust detector."""
import os, glob, numpy as np, cv2, torch, torch.nn as nn
from torch.utils.data import Dataset, DataLoader

DATA = "scratch/framedata"
DEV = "cuda" if torch.cuda.is_available() else "cpu"
H, W = 256, 384


def load_luma(i):
    img = cv2.imread(f"{DATA}/img/{i:04d}.png", cv2.IMREAD_UNCHANGED)   # BGR uint16
    if img.dtype != np.uint16:
        img = img.astype(np.uint16)
    b, g, r = img[..., 0], img[..., 1], img[..., 2]
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b).astype(np.float32) / 65535.0
    return luma


class FrameDS(Dataset):
    def __init__(self, idxs, aug=False):
        self.idxs = idxs; self.aug = aug
    def __len__(self): return len(self.idxs)
    def __getitem__(self, k):
        i = self.idxs[k]
        luma = load_luma(i)
        mask = (cv2.imread(f"{DATA}/mask/{i:04d}.png", cv2.IMREAD_GRAYSCALE) > 127).astype(np.float32)
        if self.aug:
            if np.random.rand() < 0.5:
                luma = luma[:, ::-1].copy(); mask = mask[:, ::-1].copy()
            luma = np.clip(luma * np.random.uniform(0.8, 1.2) + np.random.uniform(-0.05, 0.05), 0, 1)
            if np.random.rand() < 0.3:
                luma = np.clip(luma ** np.random.uniform(0.7, 1.4), 0, 1)
        x = torch.from_numpy(luma * 2 - 1)[None]          # [1,H,W] in [-1,1]
        y = torch.from_numpy(mask)[None]
        return x, y


class DoubleConv(nn.Module):
    def __init__(s, i, o):
        super().__init__()
        s.c = nn.Sequential(nn.Conv2d(i, o, 3, 1, 1), nn.BatchNorm2d(o), nn.ReLU(True),
                            nn.Conv2d(o, o, 3, 1, 1), nn.BatchNorm2d(o), nn.ReLU(True))
    def forward(s, x): return s.c(x)


class UNet(nn.Module):
    def __init__(s, ch=16):
        super().__init__()
        s.d1, s.d2, s.d3, s.d4 = DoubleConv(1, ch), DoubleConv(ch, ch * 2), DoubleConv(ch * 2, ch * 4), DoubleConv(ch * 4, ch * 8)
        s.pool = nn.MaxPool2d(2)
        s.u3 = nn.ConvTranspose2d(ch * 8, ch * 4, 2, 2); s.c3 = DoubleConv(ch * 8, ch * 4)
        s.u2 = nn.ConvTranspose2d(ch * 4, ch * 2, 2, 2); s.c2 = DoubleConv(ch * 4, ch * 2)
        s.u1 = nn.ConvTranspose2d(ch * 2, ch, 2, 2); s.c1 = DoubleConv(ch * 2, ch)
        s.out = nn.Conv2d(ch, 1, 1)
    def forward(s, x):
        x1 = s.d1(x); x2 = s.d2(s.pool(x1)); x3 = s.d3(s.pool(x2)); x4 = s.d4(s.pool(x3))
        y = s.c3(torch.cat([s.u3(x4), x3], 1))
        y = s.c2(torch.cat([s.u2(y), x2], 1))
        y = s.c1(torch.cat([s.u1(y), x1], 1))
        return s.out(y)


def dice_iou(logit, y):
    p = (torch.sigmoid(logit) > 0.5).float()
    inter = (p * y).sum((1, 2, 3)); union = (p + y - p * y).sum((1, 2, 3))
    return ((inter + 1) / (union + 1)).mean().item()


def main():
    n = len(glob.glob(f"{DATA}/img/*.png"))
    idx = np.arange(n); np.random.default_rng(0).shuffle(idx)
    val = idx[:max(1, n // 10)]; tr = idx[max(1, n // 10):]
    print(f"{n} samples | train {len(tr)} val {len(val)} | device {DEV}")
    dl = DataLoader(FrameDS(tr, aug=True), batch_size=16, shuffle=True, num_workers=0)
    vdl = DataLoader(FrameDS(val), batch_size=16, num_workers=0)
    net = UNet(16).to(DEV)
    print("params:", sum(p.numel() for p in net.parameters()) / 1e6, "M")
    opt = torch.optim.Adam(net.parameters(), 1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, 40)
    bce = nn.BCEWithLogitsLoss()
    def dice_loss(lg, y):
        p = torch.sigmoid(lg); inter = (p * y).sum((1, 2, 3))
        return (1 - (2 * inter + 1) / (p.sum((1, 2, 3)) + y.sum((1, 2, 3)) + 1)).mean()
    best = 0.0
    for ep in range(40):
        net.train()
        for x, y in dl:
            x, y = x.to(DEV), y.to(DEV)
            lg = net(x); loss = bce(lg, y) + dice_loss(lg, y)
            opt.zero_grad(); loss.backward(); opt.step()
        sched.step()
        net.eval(); ious = []
        with torch.no_grad():
            for x, y in vdl:
                ious.append(dice_iou(net(x.to(DEV)), y.to(DEV)))
        iou = float(np.mean(ious))
        if ep % 4 == 0 or ep == 39:
            print(f"ep{ep:02d} val_IoU={iou:.4f}")
        if iou > best:
            best = iou; torch.save(net.state_dict(), "scratch/frame_unet_best.pt")
    print("best val IoU:", round(best, 4))

    # export ONNX (dynamic spatial axes)
    net.load_state_dict(torch.load("scratch/frame_unet_best.pt"))
    net.eval()
    dummy = torch.randn(1, 1, 256, 384, device=DEV)
    torch.onnx.export(net, dummy, "scratch/frame_detector.onnx",
                      input_names=["input"], output_names=["logit"],
                      dynamic_axes={"input": {2: "h", 3: "w"}, "logit": {2: "h", 3: "w"}},
                      opset_version=13)
    print("exported scratch/frame_detector.onnx")


if __name__ == "__main__":
    main()
