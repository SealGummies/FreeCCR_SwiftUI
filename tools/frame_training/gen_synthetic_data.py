"""Generate training data for the frame-segmentation model.

Synthetic-with-perfect-masks: composite REAL negative frame-content patches onto
SYNTHETIC 135 film strips (base color + bright sprocket holes + optional black
holder) at a KNOWN rectangle. This teaches the net 'the frame is the exposed rect
BETWEEN the sprocket rows' with pixel-perfect labels, across random aspect /
position / exposure / rotation / base color — including the smooth/low-contrast
cases that broke the heuristic. Color 135 only (per user: no B&W)."""
import os, sys, glob
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "."); sys.path.insert(0, "src")
import numpy as np, cv2
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv[:1])
from core.ccr_image import CCRImage

OUT = "scratch/framedata"
os.makedirs(OUT + "/img", exist_ok=True); os.makedirs(OUT + "/mask", exist_ok=True)
H, W = 256, 384
COLOR = [
    "H:/TempPhoto/20260414Scan(sakura)", "H:/TempPhoto/20260607rescan(sakuralomo)",
    "H:/TempPhoto/20260612rescan", "H:/TempPhoto/20260617RescanIT8/neutral",
    "H:/TempPhoto/20260617RescanIT8/white", "H:/TempPhoto/20260619Rescan",
]

# --- harvest real frame-content patches + base colors -----------------------
files = []
for f in COLOR:
    files += [a for a in glob.glob(f + "/*.ARW") if "_ccr" not in a]
rng = np.random.default_rng(7)
patches, bases = [], []
for p in rng.choice(files, size=min(45, len(files)), replace=False):
    try:
        neg = CCRImage(p).read_image(p, preview=True)
    except Exception:
        continue
    if neg is None:
        continue
    h, w = neg.shape[:2]
    patches.append(neg[int(h * 0.30):int(h * 0.70), int(w * 0.32):int(w * 0.68)].copy())
    bases.append(np.median(neg[:int(h * 0.05)].reshape(-1, 3), axis=0))  # base from top strip
print("harvested", len(patches), "patches /", len(bases), "base colors")


def synth(i):
    r = np.random.default_rng(10000 + i)
    base = bases[r.integers(len(bases))].astype(np.float32)
    img = base[None, None, :] + r.normal(0, max(200, base.mean() * 0.012), (H, W, 3)).astype(np.float32)
    sh = int(H * r.uniform(0.10, 0.17))                       # sprocket strip height
    hole = np.array([r.uniform(52000, 64000)] * 3, np.float32)
    pitch = int(W * r.uniform(0.085, 0.12)); hw = int(pitch * r.uniform(0.4, 0.6))
    ph = r.integers(0, pitch)
    for y0, y1 in [(0, sh), (H - sh, H)]:                     # top + bottom sprocket rows
        for hx in range(ph, W, pitch):
            img[y0 + 2:y1 - 2, hx:min(W, hx + hw)] = hole
    if r.random() < 0.35:                                     # optional black holder sides
        b = int(W * r.uniform(0.02, 0.07)); img[:, :b] = 1500; img[:, -b:] = 1500
    b0, b1 = sh + 1, H - sh - 1                               # image band between strips
    fy0 = b0 + int(r.integers(0, max(1, int((b1 - b0) * 0.12))))
    fy1 = b1 - int(r.integers(0, max(1, int((b1 - b0) * 0.12))))
    nfr = 1 if r.random() < 0.7 else 2
    mask = np.zeros((H, W), np.uint8)
    avail = list(range(int(W * 0.02), int(W * 0.98)))
    if nfr == 1:
        fw = int(W * r.uniform(0.45, 0.96)); fx0 = int(r.integers(0, max(1, W - fw)))
        spans = [(fx0, fx0 + fw)]
    else:
        gap = int(W * r.uniform(0.02, 0.06)); fw = int((W * r.uniform(0.8, 0.95) - gap) / 2)
        fx0 = int(r.integers(0, max(1, W - (2 * fw + gap))))
        spans = [(fx0, fx0 + fw), (fx0 + fw + gap, fx0 + 2 * fw + gap)]
    exposure = r.uniform(0.6, 1.25)
    for (x0, x1) in spans:
        x1 = min(W, x1)
        if x1 - x0 < 8: continue
        patch = patches[r.integers(len(patches))].astype(np.float32) * exposure
        img[fy0:fy1, x0:x1] = cv2.resize(patch, (x1 - x0, fy1 - fy0), interpolation=cv2.INTER_AREA)
        mask[fy0:fy1, x0:x1] = 255
    if r.random() < 0.5:                                      # slight film skew
        ang = r.uniform(-3.5, 3.5); M = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
        img = cv2.warpAffine(img, M, (W, H), borderValue=tuple(base.tolist()))
        mask = cv2.warpAffine(mask, M, (W, H))
    return np.clip(img, 0, 65535).astype(np.uint16), mask


N = 1200
for i in range(N):
    img, mask = synth(i)
    cv2.imwrite(f"{OUT}/img/{i:04d}.png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    cv2.imwrite(f"{OUT}/mask/{i:04d}.png", mask)
print("wrote", N, "synthetic pairs to", OUT)

# preview 12
tiles = []
for i in range(12):
    img = cv2.imread(f"{OUT}/img/{i:04d}.png"); m = cv2.imread(f"{OUT}/mask/{i:04d}.png")
    v = (np.power(np.clip(img / 255.0, 0, 1), 1 / 2.2) * 255).astype(np.uint8) if img.max() <= 255 else img
    v = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    cnt, _ = cv2.findContours((m[..., 0] > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(v, cnt, -1, (0, 0, 255), 2)
    tiles.append(v)
ch = max(t.shape[0] for t in tiles); cw = max(t.shape[1] for t in tiles)
sheet = np.full((3 * ch, 4 * cw, 3), 20, np.uint8)
for i, t in enumerate(tiles):
    rr, cc = divmod(i, 4); sheet[rr * ch:rr * ch + t.shape[0], cc * cw:cc * cw + t.shape[1]] = t
cv2.imwrite("scratch/synth_preview.png", sheet)
print("wrote scratch/synth_preview.png")
