"""Run the trained ONNX frame segmenter on REAL color negatives (the true test of
synthetic->real transfer) and contact-sheet the results. Mirrors dust_detect.py's
inference: short-side 512, multiple of 16, luma [-1,1], sigmoid -> prob -> boxes."""
import onnxruntime as ort   # MUST be first (Windows DLL-order conflict with cv2/rawpy under torch)
import os, sys, glob
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "."); sys.path.insert(0, "src")
import numpy as np, cv2
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv[:1])
from core.ccr_image import CCRImage

SESS = ort.InferenceSession("scratch/frame_detector.onnx", providers=["CPUExecutionProvider"])
INAME = SESS.get_inputs()[0].name


def detect(neg, thr=0.5):
    h, w = neg.shape[:2]
    short = 512; scale = min(1.0, short / min(h, w))
    dh = max(16, int(round(h * scale / 16)) * 16); dw = max(16, int(round(w * scale / 16)) * 16)
    small = cv2.resize(neg, (dw, dh), interpolation=cv2.INTER_AREA).astype(np.float32) / 65535.0
    luma = 0.2126 * small[..., 0] + 0.7152 * small[..., 1] + 0.0722 * small[..., 2]
    inp = (luma * 2 - 1)[None, None].astype(np.float32)
    out = np.asarray(SESS.run(None, {INAME: inp})[0]).squeeze()
    prob = 1.0 / (1.0 + np.exp(-out))

    def runs(on, minlen):
        o = []; s = None
        for i, v in enumerate(on):
            if v and s is None: s = i
            elif (not v) and s is not None:
                if i - s >= minlen: o.append((s, i))
                s = None
        if s is not None and len(on) - s >= minlen: o.append((s, len(on)))
        return o

    # Box from prob PROJECTIONS (robust to stray edge pixels): row-mean prob gives
    # the frame band (sprocket strips fall below thr), column-mean inside it splits
    # side-by-side frames.
    rb = runs(prob.mean(1) > thr, int(0.12 * dh))
    if not rb:
        return [], prob
    y0, y1 = max(rb, key=lambda r: r[1] - r[0])
    cb = runs(prob[y0:y1].mean(0) > thr, int(0.08 * dw))
    boxes = [(x0 / dw, y0 / dh, x1 / dw, y1 / dh) for (x0, x1) in cb]
    return boxes, prob


def tile(p, label):
    neg = CCRImage(p).read_image(p, preview=True)
    if neg is None: return None
    boxes, prob = detect(neg)
    H, W = neg.shape[:2]; sc = 300 / max(H, W)
    v = cv2.resize(neg, (int(W * sc), int(H * sc)), interpolation=cv2.INTER_AREA)
    v = cv2.cvtColor((np.power(np.clip(v / 65535, 0, 1), 1 / 2.2) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    th, tw = v.shape[:2]
    pm = cv2.resize((prob * 255).astype(np.uint8), (tw, th))
    v[..., 1] = np.maximum(v[..., 1], (pm * 0.35).astype(np.uint8))   # green = prob
    for (x1, y1, x2, y2) in boxes:
        cv2.rectangle(v, (int(x1 * tw), int(y1 * th)), (int(x2 * tw), int(y2 * th)), (0, 0, 255), 2)
    cv2.putText(v, f"{label}[{len(boxes)}]", (3, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    return v


SOURCES = [
    ("sakura", "H:/TempPhoto/20260414Scan(sakura)"), ("lomo", "H:/TempPhoto/20260607rescan(sakuralomo)"),
    ("marina", "H:/TempPhoto/20260612rescan"), ("neutral", "H:/TempPhoto/20260617RescanIT8/neutral"),
    ("white", "H:/TempPhoto/20260617RescanIT8/white"), ("rescan19", "H:/TempPhoto/20260619Rescan"),
]
rng = np.random.default_rng(99)        # DIFFERENT seed -> frames not seen in patch harvest
picks = []
for name, folder in SOURCES:
    arws = sorted(a for a in glob.glob(folder + "/*.ARW") if "_ccr" not in a)
    for i in rng.choice(len(arws), size=min(6, len(arws)), replace=False):
        picks.append((name, arws[int(i)]))
tiles = []
for name, p in picks:
    try:
        t = tile(p, f"{name}:{os.path.basename(p)[3:8]}")
        if t is not None: tiles.append(t)
    except Exception as e:
        print("FAIL", name, e)
cols = 6; rows = (len(tiles) + cols - 1) // cols
cw = max(t.shape[1] for t in tiles); ch = max(t.shape[0] for t in tiles)
sheet = np.full((rows * ch, cols * cw, 3), 20, np.uint8)
for i, t in enumerate(tiles):
    r, c = divmod(i, cols); sheet[r * ch:r * ch + t.shape[0], c * cw:c * cw + t.shape[1]] = t
cv2.imwrite("scratch/onnx_real_contact.png", sheet)
print("wrote scratch/onnx_real_contact.png with", len(tiles), "real frames (green=prob, red=box)")
