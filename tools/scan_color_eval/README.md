# Film-scan colour-fidelity evaluation — standard procedure

A repeatable method (and CLI tool, `scan_color_eval.py`) for answering: **how
faithfully does each capture method — light source × camera profile — reproduce
colour after negative conversion?** It quantifies, per pixel or per colour-chart
patch, the **hue / saturation / value drift** of every capture against a chosen
reference, and reports it as graphics and text.

This document is the procedure we followed; the tool automates it.

---

## 1. What it measures

After converting each negative to a positive, every capture is compared to a
**reference** in CIELab. For each pixel (or chart patch):

- **hue** = `atan2(b*, a*)` (degrees)
- **saturation** = `Cab` = `hypot(a*, b*)` (chroma)
- **value** = `L*`

and the **drift** = `variant − reference` for each. The report contains the five
cross-plots the analysis is built around:

| plot | x-axis | y-axis |
|---|---|---|
| hue-vs-hue drift | reference hue | Δhue |
| value-vs-hue drift | reference L* | Δhue |
| hue-vs-saturation drift | reference hue | Δsaturation |
| value-vs-saturation drift | reference L* | Δsaturation |
| value-vs-value drift | reference L* | Δvalue (tone transfer) |

The **value-vs-hue** and **value-vs-saturation** curves are the "what happens in
the shadows" views — a hue/sat drift that depends on brightness is a tone-dependent
cast (e.g. magenta shadows).

---

## 2. Capture setup & data layout

Per **light source**, shoot:

1. **A film-lead shot** containing BOTH:
   - the **unexposed film base** (the orange C-41 mask, Dmin) — becomes the **black** point,
   - an **exposed/dense film base** patch (Dmax) — becomes the **white** point.
   The tool reads `black`/`white` rectangles from this shot. **All film-lead shots
   must share framing** so one pair of rectangles serves every light source.
2. **The test negatives** — the same scenes shot under each light source.
   (Optionally, include a **Calibrite ColorChecker** in-frame for absolute accuracy.)

Lay the files out as a ROOT folder whose **subfolders are named by light source**:

```
ROOT/
  trichrome/      DSC..R,G,B triplets — 1st triplet = lead, rest = tests   (auto RGB-merge)
  neutral/        lead.ARW, test1.ARW, test2.ARW, ...
  white/          lead.ARW, test1.ARW, test2.ARW, ...
```

- Files are **sorted by filename**; the **first** image (or first R,G,B triplet) is
  the lead, the rest are tests. Test *i* must be the same scene across folders.
- A subfolder whose name contains **`trichrome`** is loaded in **3-way RGB-merge
  mode** automatically (each consecutive R,G,B triplet → one camera-native frame).

---

## 3. Conversion & profiles

The negative is inverted with the **two-point (bwpoint)** method from FreeCCR's
`ccr_processor._twopoint_invert`:

- **black point** = median of the unexposed-base rect (clear, high scan value → black)
- **white point** = median of the exposed-base rect (dense, low scan value → white)

Each non-trichrome light source is decoded under each **profile**:

- **none** — camera-native (`output_color=raw`, `no_auto_scale`, `×65535/white_level`)
- **matrix** — libraw `output_color=Adobe` (the in-app "Camera Matrix")
- **dcp** — camera-native + `dcp_profile.apply_dcp(profile, as_shot_wb=raw.camera_whitebalance)`

Trichrome is camera-native by construction (no matrix), so it carries only `none`.

---

## 4. Reference (target) — two modes

- **Source-as-truth** (`--target <folder>`, default `trichrome`): one light source is
  the reference. Every other capture is **registered** to it (ORB + RANSAC homography,
  validated by NCC; pixels excluded if NCC < 0.6) and compared per pixel.
- **Chart-as-truth** (`--target chart`): an in-frame **ColorChecker Classic (24)** is
  the reference. Locate it once with `--chart-rect` (same location for all light
  sources); the tool samples the 6×4 patches and compares to the **baked-in X-Rite
  reference** — the *"November 2014 edition and newer"* chart, measured on an i1Pro 2
  (M0), stored as CIELab (D50) in `COLORCHECKER24_LAB_D50` and derived to sRGB
  (Bradford D50→D65) for the comparison. This gives **absolute** accuracy (ΔE +
  drifts), not just similarity to trichrome.

---

## 5. Procedure (what the tool does)

1. Enumerate light-source subfolders; split each into lead + tests.
2. From each lead, sample the black/white points (same rects for all).
3. Convert lead + tests per profile via two-point inversion → sRGB positives.
4. Build the reference (trichrome positive, or the chart's known values).
5. **Source mode:** register each variant to the reference, validate NCC.
   **Chart mode:** sample the chart patches directly (no registration needed).
6. Compute per-pixel/per-patch hue/sat/value drift; aggregate (chroma-weighted,
   circular mean for hue) into the five cross-plots, pooled over all test images.
7. Emit graphics + text + CSVs.

---

## 6. Usage

```bash
# from the repo root
python tools/scan_color_eval/scan_color_eval.py --root <ROOT> [options]
```

Interactive geometry (recommended): **omit** `--black/--white` (and `--chart-rect`)
and the tool **pops up the lead image** — drag a box for the unexposed base, then the
exposed base (then the chart). Needs a display.

Headless / scripted: pass rectangles as `x0,y0,x1,y1` **fractions** of the frame.

```bash
# source-as-truth (trichrome), all profiles, with a DCP
python tools/scan_color_eval/scan_color_eval.py \
  --root /path/ROOT --target trichrome \
  --black 0.55,0.35,0.70,0.65 --white 0.32,0.50,0.44,0.72 \
  --profiles none,matrix,dcp --dcp /path/profile.dcp

# absolute accuracy against an in-frame ColorChecker in the first test image
python tools/scan_color_eval/scan_color_eval.py \
  --root /path/ROOT --target chart --chart-rect 0.10,0.60,0.45,0.95 \
  --black ... --white ... --profiles none,matrix,dcp --dcp /path/profile.dcp
```

Key options: `--target` (folder name | `chart`), `--profiles`, `--dcp`,
`--density` (log bwpoint vs default affine), `--chart-from test:N|lead`,
`--chart-grid 6x4`, `--ref-illuminant D50|D55|D65`, `--no-gui`, `--out`.

**Reference illuminant (`--ref-illuminant`, chart mode).** The X-Rite reference is
measured under **D50**. `--ref-illuminant` Bradford-adapts it to the chosen adopted
white before comparison: **D65** (default) = the chart adapted to daylight, giving a
neutral reference — correct for a daylight (≈D65) shot that the conversion
white-balances; **D50/D55** leave the reference progressively warmer, to match a
lower-temperature adopted white or a conversion that is not fully balanced to
daylight. Note: if the conversion white-balances *perfectly* to the capture light,
the reference illuminant washes out and D65 is right — so use D50/D55 mainly as a
diagnostic (pick the one that minimises the neutral-patch ΔE) if the shot is
under-corrected.

---

## 7. Outputs (graphic + text)

Written to `<ROOT>/scan_color_eval_out/` (or `--out`):

- `drift_<light>.png` — the five drift curves per light source, profiles overlaid.
- `report.md` — text report: overall fidelity table + per-hue-band and
  per-brightness drift tables (Δhue/Δsat/Δvalue), with the graphics embedded.
- `overall.csv`, `drift_per_hue_band.csv`, `drift_vs_value.csv` — machine-readable.
- `registration_overlays.jpg` (source mode) — checkerboard of reference vs each
  aligned variant; **validate that the scene is seamless across squares**.
- `chart_*` outputs in chart mode (per-patch + summary, ΔE).

---

## 8. Findings from the reference run (Kodak C-41, this dataset)

Run on `20260626ScanTestv2` (trichrome = truth; neutral & white light; none/matrix/dcp;
3 test scenes, registration NCC 0.99). These are dataset-specific but illustrative:

- **Overall similarity to the no-matrix trichrome truth:** `none` is closest
  (neutral 6.1° / white 8.9° mean |Δhue|), then `dcp`, then `matrix`. Note this is
  partly **by construction** — trichrome-truth has no colour matrix, so the no-matrix
  `none` decode shares its colour science. "Closest to trichrome" ≠ "most accurate";
  use **chart mode** to judge absolute accuracy.
- **DCP is a better-behaved profile than the libraw Camera Matrix** — smaller drift
  in both lights, and far gentler on blue/cyan (B band: dcp ≈ −3…−13° vs matrix
  ≈ −9…−21°).
- **The matrix/DCP correct warm hues** (R/O off by +20…+40° in `none`, pulled toward
  0) **but distort cool hues** — the inherent trade of a camera matrix on a negative.
- **Light source governs the shadow (tone-dependent) drift:** under **neutral**
  light all profiles are tone-stable (±~7° across L*); under **white** light matrix
  and dcp develop a **strong shadow→highlight hue crossover** (+12…+25° in shadows →
  −8…−10° in highlights). `none` stays flat. This is consistent with a **spectral
  mismatch** between the white light and the profile's calibration illuminant — so
  shoot under the light the profile was made for, or use no profile.

---

## 9. Assumptions & caveats

- All film-lead shots (and the chart, in chart mode) share framing — one set of
  rectangles is reused for every light source.
- The orange unexposed base reads as R,G high / B ≈ half — the tool/operator should
  confirm that signature when placing the black-point box.
- Chart mode assumes a **ColorChecker Classic 24** (6 wide × 4 tall, dark-skin patch
  top-left, upright). The baked reference is the **X-Rite post-Nov-2014** measured
  CIELab (`COLORCHECKER24_LAB_D50`); replace it (and `--chart-grid`) for a different
  chart/edition, ideally with your own chart's measured values.
- Hue is unstable at low chroma; hue stats are chroma-weighted and thresholded.
  Saturation/value drifts use all pixels. The magenta/cyan bands (few pixels, hue
  wrap) are the noisiest — trust R/O/Y/G/B and the brightness curves most.
- "none-is-closest" in source mode is **similarity**, not absolute accuracy.

## Dependencies

Runs against the FreeCCR repo (`src/core` for decode/merge/invert/DCP) plus
`rawpy`, `opencv-python` (ORB + HighGUI for the picker), `numpy`, `matplotlib`.
Read-only — it never writes to the RAWs or the application.
