//! Estimating the film base (orange mask) from a region of the scan.

use crate::Image;

/// A rectangular region in pixel coords (inclusive top-left, exclusive bottom-right).
#[derive(Debug, Clone, Copy)]
pub struct Rect {
    pub x: usize,
    pub y: usize,
    pub w: usize,
    pub h: usize,
}

/// Estimate per-channel film base from `rect` as a high percentile (95th) of the
/// region — robust to a few dark specks while tracking the bright clear-base value.
/// If `rect` is None, uses the whole image.
pub fn sample_base(img: &Image, rect: Option<Rect>) -> [f32; 3] {
    let r = rect.unwrap_or(Rect {
        x: 0,
        y: 0,
        w: img.width,
        h: img.height,
    });
    let mut chans: [Vec<f32>; 3] = [Vec::new(), Vec::new(), Vec::new()];
    for yy in r.y..(r.y + r.h).min(img.height) {
        for xx in r.x..(r.x + r.w).min(img.width) {
            let px = img.pixels[yy * img.width + xx];
            for c in 0..3 {
                chans[c].push(px[c]);
            }
        }
    }
    let mut base = [0.0f32; 3];
    for c in 0..3 {
        if chans[c].is_empty() {
            base[c] = 0.0;
            continue;
        }
        chans[c].sort_by(|a, b| a.partial_cmp(b).unwrap());
        let idx = ((chans[c].len() as f32) * 0.95) as usize;
        let idx = idx.min(chans[c].len().saturating_sub(1));
        base[c] = chans[c][idx];
    }
    base
}

/// Estimate the film base on a frame whose brightest pixels are NOT the film.
///
/// A clear leader shot on a lightbox (a dedicated d_min frame) has a blown-out
/// surround that clips near 1.0 — brighter than the orange mask itself. A plain
/// high-percentile sampler ([`sample_base`]) latches onto that blown surround and
/// returns ~`[1,1,1]`, giving the inversion no mask compensation (a heavy blue
/// cast). This rejects pixels whose max channel exceeds `reject` (clipped /
/// non-film) and returns the `pct` percentile per channel of what remains — the
/// brightest *non-clipped* value, i.e. the clear film base. If rejection leaves
/// too little (<1% of the frame, e.g. a frame with no usable film), it falls back
/// to [`sample_base`] so the result is never empty.
pub fn sample_base_clearfilm(img: &Image, reject: f32, pct: f32) -> [f32; 3] {
    let mut chans: [Vec<f32>; 3] = [Vec::new(), Vec::new(), Vec::new()];
    for px in &img.pixels {
        if px[0].max(px[1]).max(px[2]) <= reject {
            for c in 0..3 {
                chans[c].push(px[c]);
            }
        }
    }
    let min_keep = (img.pixels.len() / 100).max(1);
    if chans[0].len() < min_keep {
        return sample_base(img, None);
    }
    let mut base = [0.0f32; 3];
    for c in 0..3 {
        chans[c].sort_by(|a, b| a.partial_cmp(b).unwrap());
        let idx = (((chans[c].len() as f32) * pct) as usize).min(chans[c].len() - 1);
        base[c] = chans[c][idx];
    }
    base
}

/// Luma band for sampling the base from a deliberately-drawn CLEAR-FILM rect:
/// a central trimmed mean rejects dark specks and specular hot pixels.
pub const BASE_BAND_REBATE: (f32, f32) = (0.1, 0.9);
/// Luma band for the whole-frame FALLBACK: the brightest cluster is the clear
/// film / lightbox. Trims the very top to avoid clipped specular highlights.
pub const BASE_BAND_AUTO: (f32, f32) = (0.90, 0.99);

/// Long-edge cap for whole-region statistics (base color / density percentile).
/// Sampling a downscaled proxy is ~constant-time and plenty accurate for a mean
/// color or a robust percentile; sorting a full-res frame (hundreds of ms at
/// 11–24 MP) on every develop / image-switch / export was the perf regression.
const SAMPLE_CAP: usize = 512;

/// Map a source-coord `rect` onto a downscaled image. `None` → the whole small
/// image. Widths/heights floor at 1px so a tiny crop never yields an empty band.
fn scaled_rect(rect: Option<Rect>, src: &Image, small: &Image) -> Rect {
    match rect {
        None => Rect {
            x: 0,
            y: 0,
            w: small.width,
            h: small.height,
        },
        Some(r) => {
            let sx = small.width as f32 / src.width.max(1) as f32;
            let sy = small.height as f32 / src.height.max(1) as f32;
            Rect {
                x: (r.x as f32 * sx) as usize,
                y: (r.y as f32 * sy) as usize,
                w: ((r.w as f32 * sx) as usize).max(1),
                h: ((r.h as f32 * sy) as usize).max(1),
            }
        }
    }
}

/// Sort a region's pixels by luma, keep the central [lo, hi] luma-rank band, and
/// average RGB over that one pixel set — a coherent clear-film color whose trim
/// rejects dark specks and specular hot pixels. Returns `[0,0,0]` for empty input.
fn coherent_band_avg(mut px: Vec<[f32; 3]>, lo: f32, hi: f32) -> [f32; 3] {
    if px.is_empty() {
        return [0.0, 0.0, 0.0];
    }
    px.sort_by(|a, b| {
        let la = (a[0] + a[1] + a[2]) / 3.0;
        let lb = (b[0] + b[1] + b[2]) / 3.0;
        la.partial_cmp(&lb).unwrap()
    });
    let lo = lo.clamp(0.0, 1.0);
    let hi = hi.clamp(0.0, 1.0);
    let n = px.len();
    let i0 = ((n as f32 * lo) as usize).min(n - 1);
    let i1 = ((n as f32 * hi) as usize).clamp(i0 + 1, n);
    let band = &px[i0..i1];
    let mut sum = [0.0f64; 3];
    for p in band {
        for c in 0..3 {
            sum[c] += p[c] as f64;
        }
    }
    let k = band.len() as f64;
    [
        (sum[0] / k) as f32,
        (sum[1] / k) as f32,
        (sum[2] / k) as f32,
    ]
}

/// Sample the film base as a single COHERENT color: collect the region's pixels,
/// sort by luma, keep the [lo, hi] luma-rank band, and average RGB over that one
/// pixel set. Unlike [`sample_base`] (three independent per-channel percentiles)
/// the result is a real clear-film color, so it removes the orange mask without
/// injecting a per-channel cast. Returns `[0,0,0]` for an empty region. Runs on a
/// downscaled proxy (see [`SAMPLE_CAP`]) so cost is independent of source size.
pub fn sample_base_coherent(img: &Image, rect: Option<Rect>, lo: f32, hi: f32) -> [f32; 3] {
    let small = downscale_for_detect(img, SAMPLE_CAP);
    let r = scaled_rect(rect, img, &small);
    let mut px: Vec<[f32; 3]> = Vec::new();
    for yy in r.y..(r.y + r.h).min(small.height) {
        for xx in r.x..(r.x + r.w).min(small.width) {
            px.push(small.pixels[yy * small.width + xx]);
        }
    }
    coherent_band_avg(px, lo, hi)
}

/// Like [`sample_base_coherent`] but reads source pixels DIRECTLY at full
/// resolution (no whole-image downscale). For the manual base picker, where the
/// rect is a small, deliberately-aimed patch: the 512-proxy point-samples only a
/// handful of source pixels for a small rect (nearest-neighbor at an ~`src/512`
/// stride), so the result is aliased and grain/dust-sensitive. Reading every true
/// pixel in the rect lets the [lo,hi] trim reject specks over a full population,
/// giving a stable color even on a thin rebate. Cheap because the rect is small —
/// do NOT call with a full-frame rect (sorts the whole image; use the proxy).
pub fn sample_base_coherent_fullres(img: &Image, rect: Rect, lo: f32, hi: f32) -> [f32; 3] {
    let mut px: Vec<[f32; 3]> = Vec::new();
    for yy in rect.y..(rect.y + rect.h).min(img.height) {
        for xx in rect.x..(rect.x + rect.w).min(img.width) {
            px.push(img.pixels[yy * img.width + xx]);
        }
    }
    coherent_band_avg(px, lo, hi)
}

/// Default damping for [`auto_wb_gains`]: gains are shrunk toward neutral by this
/// factor. Gray-world is aggressive; applying it at full strength overshoots,
/// especially on film where highlights run warm. 0.7 corrects most of the cast
/// while leaving headroom for the user's manual temp/tint.
pub const AUTO_WB_STRENGTH: f32 = 0.7;

/// Robust gray-world white-balance gains from an (already inverted) image:
/// per-channel multipliers that map a neutral-pixel estimate to gray. Returns
/// `[1,1,1]` for an empty image. Apply as `InversionParams.wb` on a subsequent
/// inversion to neutralize a global cast. Uses [`AUTO_WB_STRENGTH`] damping.
///
/// Unlike a naive average of the brightest pixels — which neutralizes warm
/// highlights (sun/tungsten/skin) and so reads the scene as warm, over-cooling
/// the result into a blue cast — this rejects strongly chromatic pixels (sky,
/// foliage, skin, warm highlights), near-clipped highlights, and shadow noise
/// before averaging, so only genuinely near-neutral pixels drive the estimate.
pub fn auto_wb_gains(img: &Image) -> [f32; 3] {
    auto_wb_gains_strength(img, AUTO_WB_STRENGTH)
}

/// [`auto_wb_gains`] with an explicit damping `strength` in 0..=1 (1 = full
/// gray-world correction, 0 = no correction / `[1,1,1]`).
pub fn auto_wb_gains_strength(img: &Image, strength: f32) -> [f32; 3] {
    if img.pixels.is_empty() {
        return [1.0, 1.0, 1.0];
    }
    // Exposure-invariant brightness gates. The gray-world gains are channel
    // ratios, so a uniform exposure nudge cancels out — *except* through which
    // pixels survive the bright/dark gate. Deriving the gate from the image's own
    // distribution (percentiles of max-channel / luma, both of which scale with
    // exposure) keeps the selected set — and the gains — stable under exposure,
    // which is what made auto-WB jump on small exposure changes (B4). Computed
    // once, deterministically (a total sort), so re-runs are bit-identical.
    let percentile = |key: &dyn Fn(&[f32; 3]) -> f32, q: f32| -> f32 {
        let mut v: Vec<f32> = img.pixels.iter().map(key).collect();
        v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let idx = (((v.len() - 1) as f32) * q).round() as usize;
        v[idx.min(v.len() - 1)]
    };
    let hi = percentile(&|p| p[0].max(p[1]).max(p[2]), 0.95); // drop brightest 5% (often warm-clipped)
    let lo = percentile(&|p| (p[0] + p[1] + p[2]) / 3.0, 0.05); // drop darkest 5% (shadow noise)

    // Collect near-neutral, well-exposed pixels per channel. `sat_max` rejects
    // chromatic content that violates the gray assumption; `hi`/`lo` drop clipped
    // highlights and shadow noise.
    let collect = |sat_max: f32, hi: f32, lo: f32| -> [Vec<f32>; 3] {
        let mut chans: [Vec<f32>; 3] = [Vec::new(), Vec::new(), Vec::new()];
        for p in &img.pixels {
            let mx = p[0].max(p[1]).max(p[2]);
            let mn = p[0].min(p[1]).min(p[2]);
            let luma = (p[0] + p[1] + p[2]) / 3.0;
            if luma < lo || mx > hi {
                continue;
            }
            let sat = if mx > 1e-6 { (mx - mn) / mx } else { 0.0 };
            if sat > sat_max {
                continue;
            }
            for c in 0..3 {
                chans[c].push(p[c]);
            }
        }
        chans
    };

    // Need a meaningful sample. If a strong global cast desaturates too few pixels,
    // relax saturation; if still empty (e.g. all clipped), fall back to everything.
    let min_keep = (img.pixels.len() / 20).max(1); // ≥5%
    let mut chans = collect(0.25, hi, lo);
    if chans[0].len() < min_keep {
        chans = collect(0.6, hi, lo);
    }
    if chans[0].is_empty() {
        chans = collect(1.0, f32::INFINITY, 0.0);
    }
    if chans[0].is_empty() {
        return [1.0, 1.0, 1.0];
    }

    // Per-channel trimmed mean (drop the top/bottom 10%): a robust central
    // estimate that ignores the few chromatic outliers that slip past the gate,
    // so the result doesn't lurch when one such pixel enters/leaves the crop.
    let trimmed_mean = |v: &mut Vec<f32>| -> f64 {
        v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let cut = v.len() / 10;
        let slice = &v[cut..(v.len() - cut).max(cut + 1)];
        slice.iter().map(|&x| x as f64).sum::<f64>() / slice.len() as f64
    };
    let mean = [
        trimmed_mean(&mut chans[0]),
        trimmed_mean(&mut chans[1]),
        trimmed_mean(&mut chans[2]),
    ];
    let gray = (mean[0] + mean[1] + mean[2]) / 3.0;
    let raw = [
        (gray / mean[0].max(1e-6)) as f32,
        (gray / mean[1].max(1e-6)) as f32,
        (gray / mean[2].max(1e-6)) as f32,
    ];
    // Damp toward neutral so the auto seed corrects most of the cast without
    // overshooting; the user can push further with the manual temp/tint sliders.
    let k = strength.clamp(0.0, 1.0);
    [
        1.0 + k * (raw[0] - 1.0),
        1.0 + k * (raw[1] - 1.0),
        1.0 + k * (raw[2] - 1.0),
    ]
}

/// Auto-derive the Cineon `D_max` (negative density range) over a region: per
/// channel take a low transmission percentile (the densest neg / brightest scene),
/// convert to density `log10(base_c / I_low_c)`, and take the max across channels
/// so no channel clips past print white. Clamped to a sane `[1.0, 4.0]`. Sampling
/// within `rect` lets the caller exclude borders (the image-area crop).
pub fn sample_dmax(img: &Image, base: [f32; 3], rect: Option<Rect>) -> f32 {
    sample_dmax_spread(img, base, rect).0
}

/// Like [`sample_dmax`] but also returns the crop's **density spread**: the max
/// across channels of `log10(i_high / i_low)` (99th vs 1st percentile transmission).
/// A flat crop (no real blacks — e.g. cropping into sky/highlights, the B3 trigger)
/// yields a tiny spread; callers use it to reject a range-destroying d_max estimate.
pub fn sample_dmax_spread(img: &Image, base: [f32; 3], rect: Option<Rect>) -> (f32, f32) {
    const LOW_PCT: f32 = 0.01; // 1st percentile transmission (densest neg)
    const HIGH_PCT: f32 = 0.99; // 99th percentile (brightest transmission = base-ish)
    let small = downscale_for_detect(img, SAMPLE_CAP);
    let r = scaled_rect(rect, img, &small);
    let mut chans: [Vec<f32>; 3] = [Vec::new(), Vec::new(), Vec::new()];
    for yy in r.y..(r.y + r.h).min(small.height) {
        for xx in r.x..(r.x + r.w).min(small.width) {
            let px = small.pixels[yy * small.width + xx];
            for c in 0..3 {
                chans[c].push(px[c]);
            }
        }
    }
    let mut d_max = 1.0f32;
    let mut spread = 0.0f32;
    for c in 0..3 {
        if chans[c].is_empty() || base[c] <= 1e-6 {
            continue;
        }
        chans[c].sort_by(|a, b| a.partial_cmp(b).unwrap());
        let n = chans[c].len();
        let lo = chans[c][((n as f32 * LOW_PCT) as usize).min(n - 1)].max(1e-5);
        let hi = chans[c][((n as f32 * HIGH_PCT) as usize).min(n - 1)].max(1e-5);
        d_max = d_max.max((base[c] / lo).log10());
        spread = spread.max((hi / lo).log10());
    }
    (d_max.clamp(1.0, 4.0), spread)
}

/// Cineon `D_max` from a **measured white-point**: the fully-exposed leader,
/// sampled in `rect`. The leader is the densest film (max light recorded), hence
/// the darkest region of the scan, so per channel we take a robust low value (5th
/// percentile, rejecting dust specks) as the white-point and convert to density
/// `log10(base_c / white_c)`, then take the max across channels (keeps `D_max`
/// scalar so white balance stays a separate print-side gain, not baked into the
/// inversion). Clamped to `[1.0, 4.0]`, mirroring `sample_dmax`. Unlike
/// `sample_dmax`, the anchor comes from the user's leader rect, not scene content —
/// giving a roll-constant highlight anchor instead of a per-frame estimate.
pub fn dmax_from_white_point(img: &Image, base: [f32; 3], rect: Option<Rect>) -> f32 {
    const WHITE_PCT: f32 = 0.05; // 5th percentile of the leader patch
    let small = downscale_for_detect(img, SAMPLE_CAP);
    let r = scaled_rect(rect, img, &small);
    let mut chans: [Vec<f32>; 3] = [Vec::new(), Vec::new(), Vec::new()];
    for yy in r.y..(r.y + r.h).min(small.height) {
        for xx in r.x..(r.x + r.w).min(small.width) {
            let px = small.pixels[yy * small.width + xx];
            for c in 0..3 {
                chans[c].push(px[c]);
            }
        }
    }
    let mut d_max = 1.0f32;
    for c in 0..3 {
        if chans[c].is_empty() || base[c] <= 1e-6 {
            continue;
        }
        chans[c].sort_by(|a, b| a.partial_cmp(b).unwrap());
        let idx = ((chans[c].len() as f32) * WHITE_PCT) as usize;
        let white = chans[c][idx.min(chans[c].len() - 1)].max(1e-5);
        let density = (base[c] / white).log10();
        d_max = d_max.max(density);
    }
    d_max.clamp(1.0, 4.0)
}

/// Result of rebate detection: the sampled clear-film base and a 0..1 confidence.
#[derive(Debug, Clone, Copy)]
pub struct RebateBase {
    pub base: [f32; 3],
    pub confidence: f32,
}

/// Outer fraction of each edge searched for the rebate.
const REBATE_BAND_FRAC: f32 = 0.12;
/// Small square patch (downscaled px) slid in 2D within each edge band, so it can
/// sit entirely inside a thin rebate sliver instead of straddling rebate+scene.
const REBATE_PATCH: usize = 14;
/// Uniformity penalty: higher = stricter about flatness.
const REBATE_UNIF_K: f32 = 4.0;
/// "Confident rebate" score: at/above this we trust the detected base outright and
/// show no UI hint. Tuned on real scans — a clean rebate scores ~0.16–0.18 (its
/// orange mask caps luma ~0.24); a dim/partial rebate (e.g. the underexposed
/// "Phoenix" roll ~0.07) falls below and is used only via the anti-blue rule in
/// `auto_base`, flagged low-confidence for an optional repoint.
pub const REBATE_CONFIDENCE: f32 = 0.12;

/// Nearest-neighbour downscale so detection stats are cheap/stable.
fn downscale_for_detect(img: &Image, target_long: usize) -> Image {
    let long = img.width.max(img.height);
    if long <= target_long {
        return img.clone();
    }
    let scale = target_long as f32 / long as f32;
    let w = ((img.width as f32 * scale) as usize).max(1);
    let h = ((img.height as f32 * scale) as usize).max(1);
    let mut pixels = vec![[0.0f32; 3]; w * h];
    for y in 0..h {
        let sy = ((y as f32 / scale) as usize).min(img.height - 1);
        for x in 0..w {
            let sx = ((x as f32 / scale) as usize).min(img.width - 1);
            pixels[y * w + x] = img.pixels[sy * img.width + sx];
        }
    }
    Image {
        width: w,
        height: h,
        pixels,
        ir: None,
    }
}

/// Mean RGB and mean per-channel coefficient-of-variation over a window.
fn patch_stats(img: &Image, x0: usize, y0: usize, pw: usize, ph: usize) -> ([f32; 3], f32) {
    let (mut sum, mut sumsq, mut n) = ([0.0f64; 3], [0.0f64; 3], 0u64);
    for y in y0..(y0 + ph).min(img.height) {
        for x in x0..(x0 + pw).min(img.width) {
            let p = img.pixels[y * img.width + x];
            for c in 0..3 {
                sum[c] += p[c] as f64;
                sumsq[c] += (p[c] as f64) * (p[c] as f64);
            }
            n += 1;
        }
    }
    if n == 0 {
        return ([0.0; 3], 1.0);
    }
    let nf = n as f64;
    let mean = [
        (sum[0] / nf) as f32,
        (sum[1] / nf) as f32,
        (sum[2] / nf) as f32,
    ];
    let mut cv_sum = 0.0f32;
    for c in 0..3 {
        let m = sum[c] / nf;
        let var = (sumsq[c] / nf - m * m).max(0.0);
        cv_sum += (var.sqrt() as f32) / (m as f32).max(1e-4);
    }
    (mean, cv_sum / 3.0)
}

/// bright × uniform × orange (each clamped 0..1). Orange requires the C-41 mask
/// ordering R≥G≥B; a blue/neutral patch scores 0 even if bright and uniform.
fn rebate_score(mean: [f32; 3], cv: f32) -> f32 {
    let bright = ((mean[0] + mean[1] + mean[2]) / 3.0).clamp(0.0, 1.0);
    let uniform = (1.0 - REBATE_UNIF_K * cv).clamp(0.0, 1.0);
    let orange = if mean[0] >= mean[1] && mean[1] >= mean[2] {
        ((mean[0] - mean[2]) / mean[0].max(1e-5)).clamp(0.0, 1.0)
    } else {
        0.0
    };
    bright * uniform * orange
}

/// Slide a small `REBATE_PATCH` square in 2D across the rect region `[rx,ry,rw,rh)`,
/// scoring each placement and keeping the best. A small square (vs a full-band-tall
/// strip) can land entirely inside a thin rebate, avoiding rebate+scene mixing.
fn scan_region(img: &Image, rx: usize, ry: usize, rw: usize, rh: usize, best: &mut RebateBase) {
    let step = (REBATE_PATCH / 2).max(1);
    let mut y = ry;
    while y < ry + rh {
        let mut x = rx;
        while x < rx + rw {
            let (mean, cv) = patch_stats(img, x, y, REBATE_PATCH, REBATE_PATCH);
            let s = rebate_score(mean, cv);
            if s > best.confidence {
                *best = RebateBase {
                    base: mean,
                    confidence: s,
                };
            }
            x += step;
        }
        y += step;
    }
}

/// Detect the C-41 orange-mask film base from the frame's edge bands. Slides a
/// small square across the outer `REBATE_BAND_FRAC` band of each edge, scoring by
/// `rebate_score`, and returns the best patch's mean as `base` with its score as
/// `confidence`.
pub fn detect_rebate_base(img: &Image) -> RebateBase {
    let small = downscale_for_detect(img, 512);
    let (w, h) = (small.width, small.height);
    if w == 0 || h == 0 {
        return RebateBase {
            base: [0.0; 3],
            confidence: 0.0,
        };
    }
    let bw = ((w as f32 * REBATE_BAND_FRAC) as usize).max(REBATE_PATCH);
    let bh = ((h as f32 * REBATE_BAND_FRAC) as usize).max(REBATE_PATCH);
    let mut best = RebateBase {
        base: [0.0; 3],
        confidence: 0.0,
    };
    scan_region(&small, 0, 0, w, bh, &mut best); // top
    scan_region(&small, 0, h.saturating_sub(bh), w, bh, &mut best); // bottom
    scan_region(&small, 0, 0, bw, h, &mut best); // left
    scan_region(&small, w.saturating_sub(bw), 0, bw, h, &mut best); // right
    best
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clearfilm_base_rejects_blown_surround() {
        // 70% orange film + 30% blown white. A plain high-percentile sampler
        // latches onto the white; clearfilm rejects it and returns the orange.
        let orange = [0.40, 0.50, 0.25];
        let white = [0.99, 0.99, 0.99];
        let mut img = Image::new(10, 10); // 100 px
        for i in 0..100 {
            img.pixels[i] = if i < 30 { white } else { orange };
        }
        // sample_base (95th pct) is dominated by the blown surround.
        let naive = sample_base(&img, None);
        assert!(naive[0] > 0.9, "naive base should latch onto white: {naive:?}");
        // clearfilm rejects the >0.92 pixels and recovers the orange base.
        let cf = sample_base_clearfilm(&img, 0.92, 0.95);
        for c in 0..3 {
            assert!(
                (cf[c] - orange[c]).abs() < 1e-3,
                "clearfilm channel {c}: got {} want {}",
                cf[c],
                orange[c]
            );
        }
        // Degenerate: an all-clipped frame leaves nothing → falls back to sample_base.
        let allwhite = Image {
            width: 10,
            height: 10,
            pixels: vec![white; 100],
            ir: None,
        };
        let fb = sample_base_clearfilm(&allwhite, 0.92, 0.95);
        assert!(fb[0] > 0.9, "all-clipped frame must fall back, got {fb:?}");
    }

    #[test]
    fn sample_dmax_recovers_density_range_and_clamps() {
        // base = 1.0; log-spaced transmission from 1.0 (i=0) down to 0.01 (i=99),
        // so the density range spans log10(1/0.01) = 2.0. The 1st-percentile pick
        // lands at the second-densest pixel (~0.0105 transmission → density ~1.98).
        let mut img = Image::new(100, 1);
        for i in 0..100 {
            let t = 10f32.powf(-2.0 * i as f32 / 99.0);
            img.pixels[i] = [t, t, t];
        }
        let d = sample_dmax(&img, [1.0, 1.0, 1.0], None);
        assert!(
            (d - 2.0).abs() < 0.2,
            "expected ~2.0 density range, got {d}"
        );

        // A near-clear region (all bright) → tiny range, clamped up to the floor 1.0.
        let flat = Image {
            width: 4,
            height: 1,
            pixels: vec![[0.9, 0.9, 0.9]; 4],
            ir: None,
        };
        assert!(
            (sample_dmax(&flat, [1.0, 1.0, 1.0], None) - 1.0).abs() < 1e-4,
            "floor 1.0"
        );

        // A pitch-black region (transmission ~0) must not blow up — clamped to 4.0.
        let dark = Image {
            width: 4,
            height: 1,
            pixels: vec![[0.0001, 0.0001, 0.0001]; 4],
            ir: None,
        };
        assert!(
            (sample_dmax(&dark, [1.0, 1.0, 1.0], None) - 4.0).abs() < 1e-4,
            "ceil 4.0"
        );
    }

    #[test]
    fn sample_dmax_downscale_preserves_range_on_large_image() {
        // A >SAMPLE_CAP image must still recover the density range via the 512px
        // proxy (perf fix). Column-wise transmission 1.0 → 0.01 → range ~2.0.
        let (w, h) = (1024usize, 1024usize);
        let mut img = Image::new(w, h);
        for x in 0..w {
            let t = 10f32.powf(-2.0 * x as f32 / (w - 1) as f32);
            for y in 0..h {
                img.pixels[y * w + x] = [t, t, t];
            }
        }
        let d = sample_dmax(&img, [1.0, 1.0, 1.0], None);
        assert!((d - 2.0).abs() < 0.25, "downscaled d_max ~2.0, got {d}");
    }

    #[test]
    fn sample_base_coherent_downscale_keeps_color_and_rect() {
        // >SAMPLE_CAP image: uniform orange border (outer 10%) over a noisy center;
        // the AUTO bright band over a rect covering a border edge must still read
        // the orange via the 512px proxy (rect scaled correctly).
        let (w, h) = (1024usize, 1024usize);
        let orange = [0.42, 0.19, 0.10];
        let mut img = Image::new(w, h);
        for y in 0..h {
            for x in 0..w {
                let edge = y < h / 10;
                img.pixels[y * w + x] = if edge {
                    orange
                } else if (x + y) % 2 == 0 {
                    [0.30, 0.30, 0.30]
                } else {
                    [0.20, 0.20, 0.20]
                };
            }
        }
        // Sample within the top border strip (full width, top 10%).
        let rect = Some(Rect {
            x: 0,
            y: 0,
            w,
            h: h / 10,
        });
        let (blo, bhi) = BASE_BAND_REBATE;
        let b = sample_base_coherent(&img, rect, blo, bhi);
        for c in 0..3 {
            assert!(
                (b[c] - orange[c]).abs() < 0.02,
                "ch {c} = {} (want {})",
                b[c],
                orange[c]
            );
        }
    }

    #[test]
    fn sample_base_coherent_fullres_trims_grain_and_dust_on_thin_rebate() {
        // A THIN orange rebate (12px) on a wide (>>SAMPLE_CAP) image, peppered with
        // grain and dark dust specks. Reading every full-res pixel lets the [lo,hi]
        // trim reject the specks and recover the true mean orange.
        let (w, h) = (4096usize, 64usize);
        let orange = [0.42f32, 0.19, 0.10];
        let band = 12usize; // ~0.3% of width — a narrow rephotographed rebate
        let mut img = Image::new(w, h);
        for y in 0..h {
            for x in 0..w {
                // Deterministic ± grain, plus a sprinkling of near-black dust.
                let grain = (((x * 7 + y * 13) % 5) as f32 - 2.0) * 0.01;
                let dust = (x * 31 + y * 17) % 23 == 0;
                let p = if x < band {
                    if dust {
                        [0.02, 0.02, 0.02]
                    } else {
                        [orange[0] + grain, orange[1] + grain, orange[2] + grain]
                    }
                } else {
                    [0.85, 0.85, 0.80]
                };
                img.pixels[y * w + x] = p;
            }
        }
        // Rect parked inside the rebate (cols 1..11).
        let r = Rect {
            x: 1,
            y: 0,
            w: band - 2,
            h,
        };
        let (lo, hi) = BASE_BAND_REBATE;
        let b = sample_base_coherent_fullres(&img, r, lo, hi);
        for c in 0..3 {
            assert!(
                (b[c] - orange[c]).abs() < 0.02,
                "ch {c} = {} (want {})",
                b[c],
                orange[c]
            );
        }
    }

    #[test]
    fn sample_base_returns_high_percentile() {
        let mut img = Image::new(10, 1);
        for i in 0..10 {
            let v = i as f32 / 10.0;
            img.pixels[i] = [v, 0.5, 0.5];
        }
        let base = sample_base(&img, None);
        assert!(base[0] >= 0.8, "got {}", base[0]);
        assert!((base[1] - 0.5).abs() < 1e-6);
    }

    #[test]
    fn sample_base_respects_rect() {
        let mut img = Image::new(4, 4);
        for y in 0..4 {
            for x in 0..4 {
                img.pixels[y * 4 + x] = if x < 2 && y < 2 {
                    [0.9, 0.9, 0.9]
                } else {
                    [0.1, 0.1, 0.1]
                };
            }
        }
        let base = sample_base(
            &img,
            Some(Rect {
                x: 0,
                y: 0,
                w: 2,
                h: 2,
            }),
        );
        assert!((base[0] - 0.9).abs() < 1e-6);
    }

    #[test]
    fn sample_base_empty_region_is_zero_no_panic() {
        let img = Image::new(4, 4);
        // zero-area rect must not panic
        let base = sample_base(
            &img,
            Some(Rect {
                x: 0,
                y: 0,
                w: 0,
                h: 0,
            }),
        );
        assert_eq!(base, [0.0, 0.0, 0.0]);
    }

    #[test]
    fn warm_highlights_do_not_trigger_blue_cast() {
        use crate::wb::gains_to_cct;
        // A neutral mid-gray scene (the real subject) with a bright WARM highlight
        // region — the common case (sun/tungsten/skin highlights). The estimator
        // must NOT read this as a warm illuminant and over-cool the image (the
        // white-patch-on-warm-highlights bug that made photos come out too blue).
        let mut pixels = vec![[0.45f32, 0.45, 0.45]; 800];
        pixels.extend(std::iter::repeat_n([0.90f32, 0.78, 0.62], 200));
        let img = Image {
            width: 1000,
            height: 1,
            pixels,
            ir: None,
        };

        let g = auto_wb_gains(&img);
        // Blue must not be strongly boosted over red (that is the cast).
        assert!(
            g[2] - g[0] < 0.12,
            "warm highlights over-cooled the image: gains {g:?}"
        );
        // And the seeded temperature must stay near daylight, not swing warm.
        let (temp, _tint) = gains_to_cct(g);
        assert!(temp >= 4900.0, "estimate read scene as warm: {temp}K");
    }

    #[test]
    fn auto_wb_gains_neutralize_a_global_cast() {
        // A uniformly magenta-cast gray (R,B high vs G) -> gains restore neutral.
        let cast = [0.6f32, 0.5, 0.4];
        let img = Image {
            width: 4,
            height: 4,
            pixels: vec![cast; 16],
            ir: None,
        };
        let spread = |p: [f32; 3]| {
            p.iter().cloned().fold(f32::MIN, f32::max) - p.iter().cloned().fold(f32::MAX, f32::min)
        };

        // Default (damped) gains: green reference, red/blue corrected toward it,
        // and the residual cast is reduced (but not fully removed — see below).
        let g = auto_wb_gains(&img);
        assert!(
            g[2] > g[1] && g[1] > g[0],
            "expected B>G>R gains, got {g:?}"
        );
        let corrected = [cast[0] * g[0], cast[1] * g[1], cast[2] * g[2]];
        assert!(
            spread(corrected) < spread(cast),
            "cast not reduced: {corrected:?}"
        );

        // At full strength the gray-world correction fully neutralizes the patch.
        let gf = auto_wb_gains_strength(&img, 1.0);
        let full = [cast[0] * gf[0], cast[1] * gf[1], cast[2] * gf[2]];
        assert!(
            spread(full) < 1e-4,
            "not neutral at full strength: {full:?}"
        );
    }

    #[test]
    fn auto_wb_gains_invariant_to_exposure_scaling() {
        // A scene whose chroma is correlated with brightness: shadows lean red,
        // highlights lean blue (a tame, sub-saturation cast). A uniform exposure
        // nudge scales every pixel equally, which must NOT change the white
        // balance — the gray-world gains are channel ratios, so the scale cancels.
        // The only way it can wobble is if an *absolute* luma gate drops a
        // brightness-correlated slice of pixels; the estimator must avoid that.
        let n = 200usize;
        let pixels: Vec<[f32; 3]> = (0..n)
            .map(|i| {
                let frac = i as f32 / (n - 1) as f32;
                let l = 0.10 + 0.80 * frac; // luma 0.10 .. 0.90
                let c = frac - 0.5; // -0.5 (shadow, red) .. +0.5 (highlight, blue)
                [l * (1.0 - 0.10 * c), l, l * (1.0 + 0.10 * c)]
            })
            .collect();
        let img = Image {
            width: n,
            height: 1,
            pixels: pixels.clone(),
            ir: None,
        };
        let scaled = Image {
            width: n,
            height: 1,
            pixels: pixels
                .iter()
                .map(|p| [p[0] * 1.5, p[1] * 1.5, p[2] * 1.5])
                .collect(),
            ir: None,
        };

        let g = auto_wb_gains(&img);
        let gs = auto_wb_gains(&scaled);
        for c in 0..3 {
            assert!(
                (g[c] - gs[c]).abs() < 0.01,
                "exposure changed WB on channel {c}: {g:?} vs {gs:?}"
            );
        }
    }

    #[test]
    fn auto_wb_gains_deterministic_on_repeat() {
        // Same pixels in → bit-identical gains out (the floor for "same image →
        // same temperature on repeated auto-WB").
        let pixels: Vec<[f32; 3]> = (0..256)
            .map(|i| {
                let l = 0.2 + 0.6 * (i as f32 / 255.0);
                [l * 1.04, l, l * 0.97]
            })
            .collect();
        let img = Image {
            width: 256,
            height: 1,
            pixels,
            ir: None,
        };
        assert_eq!(auto_wb_gains(&img), auto_wb_gains(&img));
    }

    #[test]
    fn coherent_base_is_a_trimmed_mean_of_one_pixel_set() {
        // A clear-film rect: mostly uniform orange, plus a dark speck and a bright
        // specular speck that a central luma band must trim away.
        let mut img = Image::new(10, 1);
        for i in 0..10 {
            img.pixels[i] = [0.43, 0.19, 0.11];
        }
        img.pixels[0] = [0.02, 0.01, 0.01]; // dark speck (trimmed by lo)
        img.pixels[9] = [0.99, 0.98, 0.97]; // specular speck (trimmed by hi)
        let b = sample_base_coherent(&img, None, 0.1, 0.9);
        for c in 0..3 {
            assert!(
                (b[c] - [0.43, 0.19, 0.11][c]).abs() < 1e-3,
                "ch {c} = {}",
                b[c]
            );
        }
    }

    /// Build a HxW image: uniform `border` color in the outer 10% band on all
    /// edges, `center` (optionally noisy) inside.
    fn bordered(w: usize, h: usize, border: [f32; 3], center: [f32; 3], noisy: bool) -> Image {
        let mut img = Image::new(w, h);
        let bw = (w as f32 * 0.10) as usize;
        let bh = (h as f32 * 0.10) as usize;
        for y in 0..h {
            for x in 0..w {
                let edge = x < bw || x >= w - bw || y < bh || y >= h - bh;
                let mut px = if edge { border } else { center };
                if !edge && noisy {
                    let j = if (x + y) % 2 == 0 { 0.18 } else { -0.18 };
                    px = [
                        (px[0] + j).clamp(0.0, 1.0),
                        (px[1] + j).clamp(0.0, 1.0),
                        (px[2] + j).clamp(0.0, 1.0),
                    ];
                }
                img.pixels[y * w + x] = px;
            }
        }
        img
    }

    #[test]
    fn detect_rebate_finds_orange_border_over_textured_center() {
        let orange = [0.42, 0.19, 0.10];
        let img = bordered(200, 150, orange, [0.5, 0.5, 0.5], true);
        let r = detect_rebate_base(&img);
        for (c, &o) in orange.iter().enumerate() {
            assert!((r.base[c] - o).abs() < 0.03, "ch {c}={}", r.base[c]);
        }
        assert!(r.confidence > 0.1, "confidence {}", r.confidence);
    }

    #[test]
    fn detect_rebate_ignores_bright_blue_center_phoenix() {
        let orange = [0.42, 0.19, 0.10];
        let img = bordered(200, 150, orange, [0.30, 0.21, 0.55], false);
        let r = detect_rebate_base(&img);
        assert!(
            r.base[0] > r.base[2],
            "must pick orange (R>B), got {:?}",
            r.base
        );
        assert!((r.base[0] - orange[0]).abs() < 0.05, "base {:?}", r.base);
    }

    #[test]
    fn detect_rebate_low_confidence_when_no_orange_border() {
        let img = Image {
            width: 200,
            height: 150,
            pixels: vec![[0.5, 0.5, 0.5]; 200 * 150],
            ir: None,
        };
        let r = detect_rebate_base(&img);
        assert!(
            r.confidence < 0.05,
            "expected low confidence, got {}",
            r.confidence
        );
    }

    #[test]
    fn coherent_base_bright_band_picks_clear_film_cluster() {
        // A luma gradient (dark scene → bright clear film). The bright band must
        // return ~the bright end, not the per-channel max or the global mean.
        let mut img = Image::new(100, 1);
        for i in 0..100 {
            let v = i as f32 / 99.0;
            img.pixels[i] = [v, v * 0.45, v * 0.26]; // orange-tinted ramp
        }
        let b = sample_base_coherent(&img, None, 0.90, 0.99);
        assert!(b[0] > 0.88, "bright R cluster, got {}", b[0]);
        assert!(
            (b[1] / b[0] - 0.45).abs() < 0.03,
            "G/R ratio {}",
            b[1] / b[0]
        );
        assert!(
            (b[2] / b[0] - 0.26).abs() < 0.03,
            "B/R ratio {}",
            b[2] / b[0]
        );
    }

    #[test]
    fn dmax_from_white_point_uses_channel_max_density() {
        // 4x4 uniform "leader" patch: dense (dark) scan values.
        // base/white per channel: R 0.8/0.08 → 1.0, G 0.8/0.008 → 2.0, B 0.8/0.08 → 1.0.
        // max across channels → 2.0.
        let white = [0.08f32, 0.008, 0.08];
        let pixels = vec![white; 16];
        let img = Image {
            width: 4,
            height: 4,
            pixels,
            ir: None,
        };
        let d = dmax_from_white_point(&img, [0.8, 0.8, 0.8], None);
        assert!((d - 2.0).abs() < 1e-3, "expected ~2.0, got {d}");
    }

    #[test]
    fn dmax_from_white_point_clamps_to_range() {
        // Extremely dense leader would exceed 4.0 → clamp; near-clear would underflow → 1.0.
        let dense = vec![[1e-5f32, 1e-5, 1e-5]; 16];
        let img = Image {
            width: 4,
            height: 4,
            pixels: dense,
            ir: None,
        };
        assert_eq!(dmax_from_white_point(&img, [0.8, 0.8, 0.8], None), 4.0);

        let clearish = vec![[0.79f32, 0.79, 0.79]; 16];
        let img2 = Image {
            width: 4,
            height: 4,
            pixels: clearish,
            ir: None,
        };
        assert_eq!(dmax_from_white_point(&img2, [0.8, 0.8, 0.8], None), 1.0);
    }

    #[test]
    fn flat_crop_has_low_density_spread_ranged_has_high() {
        let base = [1.0, 1.0, 1.0];
        // No real blacks (the B3 trigger: crop into sky/highlights) → tiny spread.
        let flat = Image {
            width: 8,
            height: 8,
            pixels: vec![[0.85, 0.85, 0.85]; 64],
            ir: None,
        };
        let (_d, spread) = sample_dmax_spread(&flat, base, None);
        assert!(spread < 0.1, "flat crop spread should be tiny: {spread}");
        // Spans blacks → brights → substantial density range.
        let mut px = vec![[0.9, 0.9, 0.9]; 32];
        px.extend(vec![[0.02, 0.02, 0.02]; 32]);
        let ranged = Image {
            width: 8,
            height: 8,
            pixels: px,
            ir: None,
        };
        let (_d2, spread2) = sample_dmax_spread(&ranged, base, None);
        assert!(
            spread2 > 0.5,
            "ranged crop spread should be substantial: {spread2}"
        );
    }
}
