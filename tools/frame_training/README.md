# Frame-detector training

Trains the tiny U-Net that powers `src/core/frame_detect.py` — it segments the
photographic frame(s) inside a film scan and (via probability projections)
reliably excludes the sprocket strips / film base that the classical
density-projection heuristic in `ccr_processor._heuristic_detect_frames` could
not.

## Why learned, not heuristic
Threshold/flood/projection heuristics each traded one failure for another on real
scans (rebate leak, full-height sprocket inclusion, over-clamp, mis-split). A
small segmenter trained on synthetic-with-perfect-masks data generalizes to the
smooth/low-contrast frames that break density thresholds. The model is 0.48 M
params / ~1.9 MB ONNX, runs on the onnxruntime CPU session FreeCCR already ships
for dust removal, ~50 ms.

## Pipeline
```bash
# 1. Generate synthetic 135 strips (real negative content composited onto
#    synthetic base + sprocket holes + holder at KNOWN rectangles -> perfect masks).
#    Reads real COLOR negatives from H:/TempPhoto/* (no B&W). Writes scratch/framedata/.
python tools/frame_training/gen_synthetic_data.py

# 2. Train the U-Net (luma in -> frame mask out, same I/O as the dust model) and
#    export ONNX. Needs torch (CUDA recommended). Writes scratch/frame_detector.onnx.
python tools/frame_training/train_unet.py

# 3. Evaluate on REAL negatives (fresh seed — no leakage); writes a contact sheet.
python tools/frame_training/eval_real.py

# 4. Ship: copy the ONNX into the repo (bundled by Nuitka via --include-data-dir=src/models=models)
cp scratch/frame_detector.onnx src/models/frame_detector.onnx
```

## Notes / future work
- **Color 135 only** for now (per project scope; B&W excluded). Medium-format and
  reversal want their own synthetic recipe (no sprockets / inverted polarity).
- **Multi-frame split**: a 2-up strip whose inter-frame gap stays above threshold
  returns one box. Fix by adding more/wider inter-frame gaps to the synthetic data
  so the model drives the gap probability down (don't reintroduce a brittle valley
  heuristic in box extraction — it mis-splits single frames).
- **Domain gap**: training is synthetic-only; adding a few dozen real auto-labeled
  frames (bootstrapped from the model's own confident outputs) would tighten it.
