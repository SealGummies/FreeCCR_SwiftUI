# color_eval — general image colour-fidelity evaluator

A standalone tool that measures how faithfully one or more **finished images**
reproduce a colour reference, and writes a graphic + text report (Markdown **and
PDF**) of four CIELab drift curves. **No application coupling** — depends on
`numpy`, `opencv-python`, and `matplotlib`, plus `markdown-pdf` (+ `linkify-it-py`)
for the PDF export. Read-only.

It replaces the FreeCCR-specific `scan_color_eval` for the general case: it takes
already-converted images (JPG/PNG/TIFF/…), not RAW negatives, and does no
decoding/inversion of its own.

## What it measures

Every image is compared to a reference in CIELab. Per pixel (gold mode) or per
patch (chart mode) it computes **hue** (`atan2(b*,a*)`), **saturation** (`Cab`),
and **value** (`L*`), and the **drift** (image − reference) of each. The report
contains four cross-plots, split by which data is meaningful for each:

| plot | x | y | data used |
|---|---|---|---|
| hue-vs-hue | reference hue | Δhue | **colour patches** (grey has no hue) |
| hue-vs-saturation | reference hue | Δsaturation | **colour patches** |
| value-vs-hue | reference L* | Δhue | **grey ramp** (neutral-axis cast vs brightness) |
| value-vs-value | reference L* | Δvalue | **grey ramp** (tone accuracy) |

"Colour patches" = the 18 chromatic ColorChecker patches (or chromatic pixels in
gold mode); "grey ramp" = the 6 neutral patches (or near-neutral pixels), which
span brightness and reveal any tint on the neutral axis. Hue axes carry a CIELab
hue colour strip so you can read a drift against its actual colour. (Note: hue on
near-neutral greys is inherently noisy — value-vs-hue shows cast *direction*,
value-vs-value is the stable tone metric.)

## Reference (target) — two modes

- **`--target chart`** — an in-frame **X-Rite/Calibrite ColorChecker Classic 24**
  is the reference (absolute accuracy → ΔE-style drift). Locate the chart **once**
  (interactive 4-corner picker, or `--chart-corners`); the **same location is
  reused for every image**, so a multi-image run only asks once.
- **`--target GOLD_IMAGE`** — one image is the gold standard; every other image is
  **registered** to it (ORB + homography) and compared per pixel.

The baked chart reference is the X-Rite *"November 2014 edition and newer"* chart,
measured on an i1Pro 2 (M0), stored as CIELab (D50) and adapted to the chosen
`--ref-illuminant` (`D50`/`D55`/`D65`; D65 = daylight, the default).

## Usage

```bash
python color_eval.py IMAGE [IMAGE ...] --target chart          # in-frame chart
python color_eval.py IMAGE [IMAGE ...] --target reference.jpg  # gold image
```

`IMAGE` may be files, folders, or globs. The 4-corner chart picker needs a display
(**mouse-wheel zoom**, centred on the cursor, + live 24-patch overlay to verify
orientation before you accept: click 1 = dark-skin corner, 2 = far end of that top
row, 3 = opposite/bottom corner, 4 = white-patch corner; it handles tilt / rotation
/ mirror and small hand-held charts). Headless: pass
`--chart-corners x0,y0,x1,y1,x2,y2,x3,y3` (fractions, order TL, TR, BR, BL).

Options: `--ref-illuminant D50|D55|D65`, `--chart-grid 6x4`, `--chart-image N`
(which image to locate the chart on), `--long <px>`, `--no-gui`, `--out DIR`.

## Outputs

By default the report is written **next to the first image**, in a folder named
`<first-image-name>_color_report/` (override with `--out`). It contains:

- `drift.png` — the four drift curves, all images overlaid. In chart mode each
  data point's **patch number is labelled under the x-axis**.
- `report.md` — overall fidelity table + per-hue-band and per-brightness drift
  tables, with the graphic embedded. In chart mode it also embeds the three
  swatch-check grids below.
- `report.pdf` — `report.md` rendered to PDF via the `markdown-pdf` package (A4
  landscape). Images are embedded at **native resolution** (crisp when zoomed) and
  the file is deflate-optimised (~0.4 MB). Needs `pip install markdown-pdf
  linkify-it-py`; if missing, the PDF is skipped with a hint and the rest of the
  report is still written. No pandoc/LaTeX/wkhtmltopdf needed.
- **chart-check grids** (chart mode) — reference swatch row on top, each image's
  sampled patches below (column = patch #):
  - `chart_check.png` — sampled colours **as-is** (confirms mapping + raw drift).
  - `chart_check_hue.png` — sampled at the reference **L\*** (lightness matched) →
    hue + chroma difference.
  - `chart_check_purehue.png` — sampled at the reference **L\* and chroma** →
    **pure hue** difference.
- `overall.csv`, `drift_per_hue_band.csv`, `drift_vs_value_greyramp.csv` — machine-readable.
- `chart_per_patch.csv` (chart mode) — per-patch Δhue/Δsat/Δvalue for each image.
- `registration_overlays.jpg` (gold mode) — checkerboard of gold vs each aligned
  image; the scene should be seamless across the squares (validates alignment).

## Notes

- Images are read with OpenCV (BGR); all colour math is in CIELab, so the reference
  and the measured patches are handled in the same space.
- Chart mode assumes a ColorChecker Classic 24 (6 wide × 4 tall, dark-skin patch at
  corner 1). Replace `COLORCHECKER24_LAB_D50` / `--chart-grid` for a different chart.
- Hue is unstable at low chroma; hue stats are chroma-weighted and thresholded.
- Gold mode assumes the images are the **same scene** (so they can be registered).
