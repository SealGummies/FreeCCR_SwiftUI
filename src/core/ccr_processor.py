import numpy as np
import cv2
import os
import time
import tifffile
import gc

# Try to import PyOpenCL, but handle gracefully if not available
try:
    import pyopencl as cl
    import pyopencl.array as cl_array
    OPENCL_AVAILABLE = True
except ImportError:
    print("PyOpenCL not available. GPU acceleration will be disabled.")
    OPENCL_AVAILABLE = False
    cl = None
    cl_array = None

# Global OpenCL cache
_opencl_cache = {
    'ctx': None,
    'queue': None,
    'program': None,
    'kernel': None,
    'device_name': None
}

def _initialize_opencl():
    """
    Initialize OpenCL environment and compile kernel once. Cache the results.
    Returns True if successful, False otherwise.
    """
    global _opencl_cache
    
    # Check if PyOpenCL is available
    if not OPENCL_AVAILABLE:
        return False
    
    # Check if already initialized
    if _opencl_cache['program'] is not None:
        return True
    
    try:
        # Setup OpenCL context and queue automatically
        platforms = cl.get_platforms()
        if not platforms:
            print("No OpenCL platforms found")
            return False
        
        # Use the first available platform and device
        platform = platforms[0]
        devices = platform.get_devices()
        if not devices:
            print("No OpenCL devices found")
            return False
        
        device = devices[0]
        ctx = cl.Context([device])
        queue = cl.CommandQueue(ctx)
        
        # OpenCL kernel that exactly matches the CPU version logic
        kernel_code = """
        __kernel void adjust(
            __global float *img,
            __global float *params,
            int n_pixels
        ) {
            int gid = get_global_id(0);
            if (gid >= n_pixels) return;

            float kelvin_shift = params[0];
            float tint_shift = params[1];
            float exposure = params[2];
            float brightness = params[3];
            float blackpoint = params[4];
            float whitepoint = params[5];
            float contrast = params[6];
            float saturation = params[7];
            float balance_factor = params[8];  // Global balance factor calculated on CPU
            float highlights = params[9];
            float shadows = params[10];
            float ch_input_gain   = params[11];
            float ch_master_shift = params[12];
            float ch_master_gain  = params[13];
            float ch_r_shift      = params[14];
            float ch_r_gain       = params[15];
            float ch_r_blackpoint = params[16];
            float ch_g_shift      = params[17];
            float ch_g_gain       = params[18];
            float ch_g_blackpoint = params[19];
            float ch_b_shift      = params[20];
            float ch_b_gain       = params[21];
            float ch_b_blackpoint = params[22];
            float sub_saturation  = params[23];

            int idx = gid * 3;
            float r = img[idx];
            float g = img[idx+1];
            float b = img[idx+2];

            // Temperature and Tint (Lightroom-like perceptual adjustments)
            if (kelvin_shift != 0.0f || tint_shift != 0.0f) {
                // Calculate luminance for tone-aware masking
                float img_norm_r = r / 65535.0f;
                float img_norm_g = g / 65535.0f;
                float img_norm_b = b / 65535.0f;
                float luminance = img_norm_r * 0.299f + img_norm_g * 0.587f + img_norm_b * 0.114f;
                
                // Create smooth asymmetric tone-aware strength curve (Lightroom-like)
                // Define strength levels for different tonal regions
                float shadow_strength = 0.8f;      // 80% strength in shadows (0-30% luminance)
                float midtone_strength = 1.0f;     // 100% strength in midtones (30-60% luminance)  
                float highlight_strength = 0.25f;  // 25% strength in highlights (60-100% luminance)
                
                // Transition points
                float shadow_to_mid = 0.3f;       // Shadows to midtones transition at 30% luminance
                float mid_to_highlight = 0.6f;    // Midtones to highlights transition at 60% luminance
                
                // Create smooth asymmetric curve using sigmoid blending
                float tone_curve;
                
                if (luminance <= shadow_to_mid) {
                    // Shadow region (0-30%): smooth transition from 80% to 100%
                    float shadow_progress = clamp(luminance / shadow_to_mid, 0.0f, 1.0f);
                    tone_curve = shadow_strength + (midtone_strength - shadow_strength) * shadow_progress;
                } else if (luminance <= mid_to_highlight) {
                    // Midtone region (30-60%): stay at 100% strength
                    tone_curve = midtone_strength;
                } else {
                    // Highlight region (60-100%): smooth sigmoid transition from 100% to 25%
                    float highlight_progress = (luminance - mid_to_highlight) / (1.0f - mid_to_highlight);
                    // Use sigmoid for smooth natural rolloff
                    float sigmoid_factor = 1.0f / (1.0f + exp(-8.0f * (highlight_progress - 0.5f)));
                    tone_curve = midtone_strength - (midtone_strength - highlight_strength) * sigmoid_factor;
                }
                
                // Temperature (R/B scaling with logarithmic perceptual response)
                if (kelvin_shift != 0.0f) {
                    // Map slider values [-100, 100] to Kelvin temperatures [2000K, 8000K]
                    // Neutral point (slider 0) = 5000K
                    float neutral_kelvin = 5000.0f;
                    float current_kelvin = neutral_kelvin + (kelvin_shift / 100.0f) * 3000.0f;
                    
                    // Calculate Kelvin delta from neutral
                    float kelvin_delta = current_kelvin - neutral_kelvin;
                    
                    // Logarithmic scaling for Kelvin - stronger impact at low end
                    float kelvin_abs = fabs(kelvin_delta);
                    
                    // Create logarithmic response curve based on actual Kelvin values
                    // Linear scale: full 3000K swing = 40% R/B shift
                    float perceptual_scale = (kelvin_delta / 3000.0f) * 0.40f;

                    // tone_curve already covers spatial (shadow/highlight) weighting
                    float effective_scale = perceptual_scale * tone_curve;
                    
                    float r_scale = 1.0f + effective_scale;
                    float b_scale = 1.0f - effective_scale;
                    
                    r *= r_scale;  // R
                    b *= b_scale;  // B
                }

                // Tint (G-M scaling with perceptual mapping and enhanced midtone sensitivity)
                if (tint_shift != 0.0f) {
                    // Use the global balance factor calculated on CPU for exact matching
                    // This ensures identical results between CPU and OpenCL versions
                    
                    // Enhanced midtone and skin tone sensitivity for tint
                    // Tint is most visible in skin tones and neutral areas
                    float skin_tone_sensitivity = 1.0f + 0.5f * exp(-12.0f * pow(luminance - 0.35f, 2.0f));  // Peak at 35% luminance
                    
                    // Create perceptual tint curve - stronger response in certain ranges
                    float tint_abs = fabs(tint_shift);
                    float perceptual_tint = 0.0f;
                    if (tint_abs > 0.0f) {
                        // Sigmoid-like curve for tint perception
                        perceptual_tint = tanh(tint_abs * 0.02f) * sign(tint_shift) * 0.18f;
                    }
                    
                    // Apply perceptual tint with tone awareness, balance factor, and skin tone sensitivity
                    float effective_tint = perceptual_tint * tone_curve * balance_factor * skin_tone_sensitivity;
                    
                    // Tint primarily affects green, with complementary adjustments to R/B
                    float g_scale = 1.0f - effective_tint;  // Green channel (inverse of tint shift)
                    float r_tint_scale = 1.0f + (0.3f * effective_tint);  // Slight red compensation
                    float b_tint_scale = 1.0f + (0.3f * effective_tint);  // Slight blue compensation
                    
                    g *= g_scale;  // G
                    r *= r_tint_scale;  // R  
                    b *= b_tint_scale;  // B
                }
            }

            // Exposure (Adobe-like, tone-aware to preserve highlights)
            if (exposure != 0.0f) {
                // Calculate luminance for tone-aware exposure mapping
                float img_norm_r = r / 65535.0f;
                float img_norm_g = g / 65535.0f;
                float img_norm_b = b / 65535.0f;
                float luminance = img_norm_r * 0.299f + img_norm_g * 0.587f + img_norm_b * 0.114f;
                
                // Create smooth, continuous tone-aware exposure curve
                float transition_midpoint = 0.80f;   // Where the curve inflection point is (80% luminance)
                float transition_width = 0.15f;      // Controls smoothness of transition
                float min_strength = 0.03f;          // Minimum exposure effect in pure highlights (3%)
                float max_strength = 1.0f;           // Maximum exposure effect in shadows/midtones
                
                // Smooth sigmoid-like curve for continuous transition
                float exposure_curve = min_strength + (max_strength - min_strength) * (
                    1.0f / (1.0f + exp((luminance - transition_midpoint) / transition_width))
                );
                
                // Apply tone-aware exposure
                float exposure_scale = exposure * 2.0f / 100.0f;
                float exposure_factor = pow(2.0f, exposure_scale);  // Base exposure factor in stops (±2 EV)
                
                // Create per-pixel exposure factors
                float pixel_exposure_factor = 1.0f + (exposure_factor - 1.0f) * exposure_curve;
                
                r *= pixel_exposure_factor;
                g *= pixel_exposure_factor;
                b *= pixel_exposure_factor;
            }
            
            // Brightness (Adobe-like: lift lower midtones, preserve highlights)
            if (brightness != 0.0f) {
                float img_norm_r = r / 65535.0f;
                float img_norm_g = g / 65535.0f;
                float img_norm_b = b / 65535.0f;
                
                float brightness_scale = brightness / 8.0f;   // -10.0 to +10.0 for -100 to +100
                // The curve parameter: positive = lift, negative = compress
                float curve = 1.0f - 0.3f * brightness_scale;  // 2.2 to 0.8 for -100 to +100

                // Use pow directly like CPU version, without fmax protection
                img_norm_r = pow(img_norm_r, curve);
                img_norm_g = pow(img_norm_g, curve);
                img_norm_b = pow(img_norm_b, curve);
                
                r = img_norm_r * 65535.0f;
                g = img_norm_g * 65535.0f;
                b = img_norm_b * 65535.0f;
            }

            // Highlights / Shadows (anchored per-channel tone-region roll-off)
            // Region bumps are zero at both endpoints so pure black and pure
            // white stay anchored; highlights roll off smoothly below white.
            if (highlights != 0.0f || shadows != 0.0f) {
                float hs_peak = 0.10546875f;  // peak of x^3*(1-x); normalizes bumps to 1.0
                float hs_strength = 0.30f;    // max channel offset at the bump peak
                float h_amt = highlights / 100.0f;
                float s_amt = shadows / 100.0f;

                float xr = r / 65535.0f;
                float omr = 1.0f - xr;
                xr = xr + h_amt * hs_strength * (xr*xr*xr) * omr / hs_peak
                        + s_amt * hs_strength * xr * (omr*omr*omr) / hs_peak;

                float xg = g / 65535.0f;
                float omg = 1.0f - xg;
                xg = xg + h_amt * hs_strength * (xg*xg*xg) * omg / hs_peak
                        + s_amt * hs_strength * xg * (omg*omg*omg) / hs_peak;

                float xb = b / 65535.0f;
                float omb = 1.0f - xb;
                xb = xb + h_amt * hs_strength * (xb*xb*xb) * omb / hs_peak
                        + s_amt * hs_strength * xb * (omb*omb*omb) / hs_peak;

                r = clamp(xr, 0.0f, 1.0f) * 65535.0f;
                g = clamp(xg, 0.0f, 1.0f) * 65535.0f;
                b = clamp(xb, 0.0f, 1.0f) * 65535.0f;
            }

            // Black/White point (Adobe-like: remap input range)
            if (blackpoint != 0.0f || whitepoint != 0.0f) {
                float img_norm_r = r / 65535.0f;
                float img_norm_g = g / 65535.0f;
                float img_norm_b = b / 65535.0f;
                
                // Map [-100, 100] to [0, 0.2] for black, [1, 0.8] for white
                float black_clip = clamp(blackpoint, -100.0f, 100.0f) / 300.0f;
                float white_clip = clamp(whitepoint, -100.0f, 100.0f) / 300.0f;  // -0.2 to +0.2
                float black_val = 0.0f + black_clip;
                float white_val = 1.0f - white_clip;
                float range = white_val - black_val;
                
                // Piecewise linear remap
                if (range > 1e-6f) {
                    img_norm_r = clamp((img_norm_r - black_val) / range, 0.0f, 1.0f);
                    img_norm_g = clamp((img_norm_g - black_val) / range, 0.0f, 1.0f);
                    img_norm_b = clamp((img_norm_b - black_val) / range, 0.0f, 1.0f);
                }
                
                r = img_norm_r * 65535.0f;
                g = img_norm_g * 65535.0f;
                b = img_norm_b * 65535.0f;
            }
            
            // Contrast (continuous S-curve for both positive and negative)
            if (contrast != 0.0f) {
                float img_norm_r = r / 65535.0f;
                float img_norm_g = g / 65535.0f;
                float img_norm_b = b / 65535.0f;
                
                float midpoint = 0.5f;
                // Map contrast [-100, 100] to k [-0.95, 0.95]
                float k = clamp(contrast / 105.0f, -0.95f, 0.95f);
                
                // S-curve: compress for negative, expand for positive, fixed endpoints
                img_norm_r = ((1.0f + k) * (img_norm_r - midpoint)) / (1.0f + k * fabs(img_norm_r - midpoint) * 2.0f) + midpoint;
                img_norm_g = ((1.0f + k) * (img_norm_g - midpoint)) / (1.0f + k * fabs(img_norm_g - midpoint) * 2.0f) + midpoint;
                img_norm_b = ((1.0f + k) * (img_norm_b - midpoint)) / (1.0f + k * fabs(img_norm_b - midpoint) * 2.0f) + midpoint;
                
                r = img_norm_r * 65535.0f;
                g = img_norm_g * 65535.0f;
                b = img_norm_b * 65535.0f;
            }

            // Mid-high tone weighted saturation adjustment
            if (saturation != 0.0f) {
                float img_norm_r = r / 65535.0f;
                float img_norm_g = g / 65535.0f;
                float img_norm_b = b / 65535.0f;
                
                // Convert RGB to grayscale using luminance weights
                float gray = img_norm_r * 0.299f + img_norm_g * 0.587f + img_norm_b * 0.114f;
                
                float luminance_offset = gray - 0.50f;
                float mid_high_weight = exp(-(luminance_offset * luminance_offset) / (0.35f * 0.35f));
                
                // Create dynamic saturation factor based on mid-high tone weighting
                // Maximum effect at 65% luminance, minimal effect in deep shadows/highlights
                float min_saturation_factor = 0.2f;  // 20% of full saturation in extremes
                float saturation_curve = min_saturation_factor + (1.0f - min_saturation_factor) * mid_high_weight;
                
                // Apply the mid-high tone weighted saturation scaling
                float saturation_scale = 1.0f + (saturation / 100.0f);  // Base saturation scale
                float dynamic_saturation_scale = 1.0f + (saturation_scale - 1.0f) * saturation_curve;
                
                // Blend between grayscale and original based on mid-high tone weighted saturation
                img_norm_r = gray + dynamic_saturation_scale * (img_norm_r - gray);
                img_norm_g = gray + dynamic_saturation_scale * (img_norm_g - gray);
                img_norm_b = gray + dynamic_saturation_scale * (img_norm_b - gray);
                
                img_norm_r = clamp(img_norm_r, 0.0f, 1.0f);
                img_norm_g = clamp(img_norm_g, 0.0f, 1.0f);
                img_norm_b = clamp(img_norm_b, 0.0f, 1.0f);
                
                r = img_norm_r * 65535.0f;
                g = img_norm_g * 65535.0f;
                b = img_norm_b * 65535.0f;
            }

            // Subtractive (film-density) saturation: scale each pixel's
            // chromaticity ratios by a power while pinning the dominant
            // channel, so saturation is gained by absorbing light in the
            // other channels (darker, denser colors) instead of adding it.
            if (sub_saturation != 0.0f) {
                float sr = clamp(r / 65535.0f, 0.0f, 1.0f);
                float sg = clamp(g / 65535.0f, 0.0f, 1.0f);
                float sb = clamp(b / 65535.0f, 0.0f, 1.0f);
                float mx = fmax(sr, fmax(sg, sb));
                if (mx > 1e-6f) {
                    float gamma_s = pow(2.0f, sub_saturation / 100.0f);
                    sr = mx * pow(sr / mx, gamma_s);
                    sg = mx * pow(sg / mx, gamma_s);
                    sb = mx * pow(sb / mx, gamma_s);
                }
                r = sr * 65535.0f;
                g = sg * 65535.0f;
                b = sb * 65535.0f;
            }

            // Per-channel levels controls (linear domain, post-conversion data).
            // Blackpoint/Gain mirror the regular Black/White Point sliders
            // (same /300 mapping) per channel; Shift is a uniform additive
            // offset; Input Gain is a pre-everything exposure multiplier.
            if (ch_input_gain != 0.0f || ch_master_shift != 0.0f || ch_master_gain != 0.0f ||
                ch_r_shift != 0.0f || ch_r_gain != 0.0f || ch_r_blackpoint != 0.0f ||
                ch_g_shift != 0.0f || ch_g_gain != 0.0f || ch_g_blackpoint != 0.0f ||
                ch_b_shift != 0.0f || ch_b_gain != 0.0f || ch_b_blackpoint != 0.0f) {

                float ig = pow(2.0f, ch_input_gain / 50.0f);
                float rs  = clamp(ch_master_shift + ch_r_shift, -100.0f, 100.0f) / 300.0f;
                float rg  = clamp(ch_master_gain + ch_r_gain, -100.0f, 100.0f) / 300.0f;
                float rbp = clamp(ch_r_blackpoint, -100.0f, 100.0f) / 300.0f;
                float gs  = clamp(ch_master_shift + ch_g_shift, -100.0f, 100.0f) / 300.0f;
                float gg  = clamp(ch_master_gain + ch_g_gain, -100.0f, 100.0f) / 300.0f;
                float gbp = clamp(ch_g_blackpoint, -100.0f, 100.0f) / 300.0f;
                float bs  = clamp(ch_master_shift + ch_b_shift, -100.0f, 100.0f) / 300.0f;
                float bg  = clamp(ch_master_gain + ch_b_gain, -100.0f, 100.0f) / 300.0f;
                float bbp = clamp(ch_b_blackpoint, -100.0f, 100.0f) / 300.0f;

                // Process each channel only when non-neutral, so the normalize
                // round-trip can't drift untouched channels by 1 LSB.
                if (ig != 1.0f || rs != 0.0f || rg != 0.0f || rbp != 0.0f) {
                    float xr = (r / 65535.0f) * ig + rs;
                    xr = (xr - rbp) / ((1.0f - rg) - rbp);
                    r = clamp(xr, 0.0f, 1.0f) * 65535.0f;
                }
                if (ig != 1.0f || gs != 0.0f || gg != 0.0f || gbp != 0.0f) {
                    float xg = (g / 65535.0f) * ig + gs;
                    xg = (xg - gbp) / ((1.0f - gg) - gbp);
                    g = clamp(xg, 0.0f, 1.0f) * 65535.0f;
                }
                if (ig != 1.0f || bs != 0.0f || bg != 0.0f || bbp != 0.0f) {
                    float xb = (b / 65535.0f) * ig + bs;
                    xb = (xb - bbp) / ((1.0f - bg) - bbp);
                    b = clamp(xb, 0.0f, 1.0f) * 65535.0f;
                }
            }

            // Final clamp and store results
            r = clamp(r, 0.0f, 65535.0f);
            g = clamp(g, 0.0f, 65535.0f);
            b = clamp(b, 0.0f, 65535.0f);

            img[idx] = r;
            img[idx+1] = g;
            img[idx+2] = b;
        }
        """

        # Compile the program
        program = cl.Program(ctx, kernel_code).build()
        
        # Create and cache the kernel
        kernel = cl.Kernel(program, "adjust")
        
        # Cache everything
        _opencl_cache['ctx'] = ctx
        _opencl_cache['queue'] = queue
        _opencl_cache['program'] = program
        _opencl_cache['kernel'] = kernel
        _opencl_cache['device_name'] = device.name
        
        print(f"OpenCL initialized successfully - Device: {device.name} on platform: {platform.name}")
        return True
        
    except Exception as e:
        print(f"OpenCL initialization failed: {e}")
        return False


def safe_unicode_path(file_path: str) -> str:
    """
    Ensure file path is properly encoded for Unicode support across different systems.
    """
    return os.path.normpath(file_path)


def safe_cv2_imwrite(output_path: str, image: np.ndarray, params=None) -> bool:
    """
    Safe image writing that handles Unicode file paths.
    params: optional cv2 encode parameters, e.g. [cv2.IMWRITE_JPEG_QUALITY, 92]
    """
    output_path = safe_unicode_path(output_path)
    params = params or []
    try:
        # Try normal cv2.imwrite first
        success = cv2.imwrite(output_path, image, params)
        if success:
            return True

        # If that fails, try encoding to bytes and using alternative method
        try:
            # Get file extension
            _, ext = os.path.splitext(output_path)
            # Encode image to memory buffer
            success, buffer = cv2.imencode(ext, image, params)
            if success:
                # Write buffer to file
                with open(output_path, 'wb') as f:
                    f.write(buffer.tobytes())
                return True
        except Exception as e:
            print(f"Failed to write image with Unicode path handling: {output_path}, error: {e}")
            return False
            
    except Exception as e:
        print(f"Failed to write image: {output_path}, error: {e}")
        return False
    
    return False


def safe_tifffile_imwrite(output_path: str, image: np.ndarray, **kwargs) -> bool:
    """
    Safe TIFF writing that handles Unicode file paths.
    """
    output_path = safe_unicode_path(output_path)
    try:
        tifffile.imwrite(output_path, image, **kwargs)
        return True
    except Exception as e:
        print(f"Failed to write TIFF image: {output_path}, error: {e}")
        return False




def ccr_normalize_with_reference(ccr_image,output_path=None,water_mark=True,jpg_out=False,jpg_quality=95,max_long_side=None) -> np.ndarray:
    """
    Normalize and align the image using the CCR algorithm, using a reference rectangle
    for percentile calculations instead of a crop factor.

    Args:
        ccr_image: CCRImage object

    Returns:
        np.ndarray: CCR-normalized and inverted image, dtype uint16
    """
    print("Starting CCR normalization...")
    total_start_time = time.time()
    
    # Get the working image
    step_start = time.time()
    if output_path is not None: # this is for output
        img = ccr_image.read_image(ccr_image.file_path,preview=False)
    else:  # this is for processing
        img = ccr_image.resized_raw
    if img is None:
        raise ValueError("CCRImage.resized_raw is None")
    print(f"Image loading: {time.time() - step_start:.3f}s")

    # Apply fine rotation rotation 
    step_start = time.time()
    fine_angle = ccr_image.fine_rotation_angle / 100.0
    h_flip = ccr_image.horizontal_mirrored
    v_flip = ccr_image.vertical_mirrored

    # Center of rotation
    h, w = img.shape[:2]
    center = (w // 2, h // 2)

    
    if output_path is not None: # this is for output
        img_ref = ccr_image.resize_image_to_max_pixel(img, 1080)
    else:  # this is for processing
        img_ref = ccr_image.resized_raw
    
    # fine Rotation
    if fine_angle != 0:
        center_ref = (img_ref.shape[1] // 2, img_ref.shape[0] // 2)
        w_ref, h_ref = img_ref.shape[1], img_ref.shape[0]
        rot_mat = cv2.getRotationMatrix2D(center_ref, -fine_angle, 1.0)
        img_ref = cv2.warpAffine(img_ref, rot_mat, (w_ref, h_ref), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,borderValue=0)
        # Clean up rotation matrix as it's no longer needed
        del rot_mat
        # print(f"Rotated image by {angle} degrees")
        # if output_path is not None: # this is for output
        #     # when outputting rotate original image as well
        #     rot_mat = cv2.getRotationMatrix2D(center, -angle, 1.0)
        #     img = cv2.warpAffine(img, rot_mat, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,borderValue=0)
    print(f"Image setup and rotation: {time.time() - step_start:.3f}s")

    # Reference frame
    step_start = time.time()
    reference_rect = ccr_image.reference_frame
    if reference_rect is None:
        raise ValueError("CCRImage.reference_frame is None")

    mapped_rect = map_rect_to_original(
        ccr_image.resized_raw.shape,
        img_ref.shape,
        reference_rect
    )

    x1, y1, x2, y2 = mapped_rect

    img = img.astype(np.float32, copy=False)
    ref_crop = img_ref[y1:y2, x1:x2]
    print(f"Reference frame setup: {time.time() - step_start:.3f}s")

    # # Show the image using matplotlib for debugging
    # plt.figure(figsize=(8, 8))
    # plt.imshow(to_8bit(ref_crop))
    # plt.title("ref_crop")
    # plt.axis('off')
    # plt.show()

    # Black/white point normalization per channel with three-segment linear compression
    step_start = time.time()
    norm = np.empty_like(img, dtype=np.float32)
    norm_ref = np.empty_like(img_ref, dtype=np.float32)
    for c in range(3):
        ch_crop = ref_crop[..., c]
        # Get percentiles for linear mapping with compressed extremes
        p10 = np.percentile(ch_crop, 1)    # 1st percentile
        p90 = np.percentile(ch_crop, 99)    # 99th percentile  
        
        ch_full = img[..., c]
        ch_full_ref = img_ref[..., c]
        
        # Linear mapping: p10->6086, p90->43882:
        # Formula: output = (input - p10) / (p90 - p10) * (43882 - 6086) + 6086
        np.subtract(ch_full, p10, out=norm[..., c])
        np.divide(norm[..., c], (p90 - p10), out=norm[..., c])
        np.multiply(norm[..., c], (65535 - 8192), out=norm[..., c])
        np.add(norm[..., c], 8192, out=norm[..., c])
        np.clip(norm[..., c], 0, 65535, out=norm[..., c])

        np.subtract(ch_full_ref, p10, out=norm_ref[..., c])
        np.divide(norm_ref[..., c], (p90 - p10), out=norm_ref[..., c])
        np.multiply(norm_ref[..., c], (65535 - 8192), out=norm_ref[..., c])
        np.add(norm_ref[..., c], 8192, out=norm_ref[..., c])
        np.clip(norm_ref[..., c], 0, 65535, out=norm_ref[..., c])
    
    # Clean up intermediate arrays
    del ref_crop
    print(f"BWPN: {time.time() - step_start:.3f}s")
      # Optical density alignment (conservative optimization)
    step_start = time.time()
    ref_norm_crop = norm_ref[y1:y2, x1:x2]
    np.add(ref_norm_crop, 1e-6, out=ref_norm_crop)
    od_crop = -np.log10(ref_norm_crop / 65535.0)
    mean_od_crop = np.mean(od_crop, axis=(0, 1))
    target_mean_od = np.mean(mean_od_crop)
    scaling_factors = target_mean_od / (mean_od_crop + 1e-12)  # Only add division by zero protection

    # Apply scaling to full image (keep original approach)
    norm_full = norm
    np.add(norm_full, 1e-6, out=norm_full)
    od_full = -np.log10(norm_full / 65535.0)
    od_aligned_full = od_full * scaling_factors
      # Clean up intermediate arrays
    del ref_norm_crop, od_crop, mean_od_crop, scaling_factors, od_full
    
    np.power(10, -od_aligned_full, out=od_aligned_full)
    od_aligned_full *= 65535.0
    np.clip(od_aligned_full, 0, 65535, out=od_aligned_full)
    rgb_aligned_full = od_aligned_full.astype(np.uint16, copy=False)

    # Invert
    rgb_inverted_full = 65535 - rgb_aligned_full
    
    # Clean up more intermediate arrays
    del od_aligned_full, rgb_aligned_full, norm, norm_ref
    print(f"ODAI: {time.time() - step_start:.3f}s")

    # # --- Brightness normalization using grayscale ---
    # # Convert to grayscale using standard luminance weights
    # gray = np.dot(rgb_inverted_full[..., :3], [0.299, 0.587, 0.114])

    # # Compute histogram and find the peak (mode)
    # hist, bin_edges = np.histogram(gray, bins=256, range=(0, 65535))
    # peak_bin = np.argmax(hist)
    # peak_value = (bin_edges[peak_bin] + bin_edges[peak_bin + 1]) / 2.0

    # # Target: map histogram peak to 55% of 65535
    # target_peak = 0.55 * 65535

    # # Compute scaling factor to map peak to target
    # brightness_scale = target_peak / (peak_value + 1e-6)
    # rgb_scaled = rgb_inverted_full * brightness_scale

    # # Stretch so the brightest point reaches 65535
    # max_scaled = np.max(rgb_scaled)
    # if max_scaled > 0:
    #     stretch_scale = 65535.0 / max_scaled
    # else:
    #     stretch_scale = 1.0    # rgb_brightness_normalized = np.clip(rgb_scaled * stretch_scale, 0, 65535).astype(np.uint16)    # Apply inverted gamma correction for inverted linear data
    # Since we have inverted linear data, apply inverted gamma 1.5
    rgb_norm = rgb_inverted_full.astype(np.float32) / 65535.0
      # Apply inverted gamma 2.2 (use gamma = 2.2 for inverted image)
    gamma_corrected = np.power(np.clip(rgb_norm, 0.0, 1.0), 1.0)
    del rgb_norm

    # Convert to LAB-like processing for saturation
    # Calculate luminance using standard weights
    luminance = np.dot(gamma_corrected[..., :3], [0.299, 0.587, 0.114])
    luminance_expanded = np.expand_dims(luminance, axis=-1)
    
    # Create saturation curve that has minimal effect in shadows and stronger effect in midtones/highlights
    # Using power curve: luminance^0.8 gives gentle increase from shadows to highlights
    saturation_curve = np.power(luminance, 0.8)  # Smooth curve from 0 to 1
    del luminance  # Clean up luminance as it's no longer needed
    base_saturation = 1.15  # 15% maximum saturation increase

    # Calculate dynamic saturation factor: minimal in shadows (1.02), full in highlights (1.12)
    min_saturation = 1.00  # 2% minimum saturation in pure shadows
    saturation_range = base_saturation - min_saturation  # 0.10 range
    dynamic_saturation = min_saturation + saturation_range * saturation_curve
    del saturation_curve
    dynamic_saturation = np.expand_dims(dynamic_saturation, axis=-1)
    
    # Apply luminance-aware saturation by blending between grayscale and color
    gamma_corrected = luminance_expanded + dynamic_saturation * (gamma_corrected - luminance_expanded)
    del luminance_expanded, dynamic_saturation
    gamma_corrected = np.clip(gamma_corrected, 0.0, 1.0)
    
    # Convert back to 16-bit and assign to rgb_brightness_normalized
    # rgb_brightness_normalized = np.clip(gamma_corrected * 65535.0, 0, 65535).astype(np.uint16)

        # Shadow-specific color correction: add warmth and green to dark shadows only
    # Convert back to normalized for shadow correction
    shadow_corrected = gamma_corrected
    # Calculate luminance for curve-based shadow correction
    shadow_luminance = np.dot(shadow_corrected[..., :3], [0.299, 0.587, 0.114])
    
    # Create smooth exponential curves that naturally target shadows
    # These curves provide maximum effect in deep shadows and fade smoothly to highlights
    
    # Warmth curve: exponential decay from shadows (stronger effect in darker areas)
    warmth_curve = np.exp(-shadow_luminance * 4.0)  # Exponential decay, strong in shadows
    warmth_strength = 0.35 * warmth_curve  # 30% max correction in pure black
    del warmth_curve  # Clean up as it's no longer needed
    # Green tint curve: similar but with different decay rate for natural look
    green_curve = np.exp(-shadow_luminance * 3.5)  # Slightly different curve shape
    del shadow_luminance  # Clean up as it's no longer needed
    green_strength = 0.15 * green_curve  # 12% max correction in pure black
    del green_curve  # Clean up as it's no longer needed

    # Apply corrections using smooth curves (no masks or conditionals)
    shadow_corrected[..., 0] *= (1.0 + warmth_strength * 0.8)  # Red: moderate warmth boost
    shadow_corrected[..., 1] *= (1.0 + green_strength)  # Green: boost to counter magenta
    shadow_corrected[..., 2] *= (1.0 - warmth_strength)  # Blue: reduce to counter blue cast
    del warmth_strength, green_strength  # Clean up as they're no longer needed
    
    # Convert back to 16-bit
    rgb_brightness_normalized = np.clip(shadow_corrected * 65535.0, 0, 65535).astype(np.uint16)

    del shadow_corrected, gamma_corrected  # Clean up as they're no longer needed

    # Clean up rgb_inverted_full as it's no longer needed
    del rgb_inverted_full
    gc.collect()
    # --- End of brightness normalization ---

    # --- apply user adjustments --- only when outputting
    step_start = time.time()
    if output_path is not None:  # this is for processing
        rgb_brightness_normalized=ccr_image.apply_adjustments(rgb_brightness_normalized)
    print(f"User adjustments: {time.time() - step_start:.3f}s")

    # --- End of user adjustments ---

    if output_path is not None:  # this is for output
        # User crop (normalized rect in un-rotated/un-flipped space) — applied
        # before flips/rotation so it matches the cropped preview orientation.
        rgb_brightness_normalized = apply_crop_to_image(
            rgb_brightness_normalized, getattr(ccr_image, 'crop_rect', None))
        step_start = time.time()
        # Apply flips and rotation to rgb_brightness_normalized before export
        if h_flip and v_flip:
            rgb_brightness_normalized = cv2.flip(rgb_brightness_normalized, -1)
        elif h_flip:
            rgb_brightness_normalized = cv2.flip(rgb_brightness_normalized, 1)
        elif v_flip:
            rgb_brightness_normalized = cv2.flip(rgb_brightness_normalized, 0)

        # --- ADD THIS BLOCK: rotate pixels for 90/180/270 degree rotation ---
        angle = ccr_image.rotation_angle % 360
        if angle == 90:
            # Rotate 90 degrees clockwise
            rgb_brightness_normalized = np.rot90(rgb_brightness_normalized, k=3)
        elif angle == 180:
            # Rotate 180 degrees
            rgb_brightness_normalized = np.rot90(rgb_brightness_normalized, k=2)
        elif angle == 270:
            # Rotate 270 degrees clockwise (or 90 degrees CCW)
            rgb_brightness_normalized = np.rot90(rgb_brightness_normalized, k=1)
        # --- END BLOCK ---
        print(rgb_brightness_normalized.shape)
        print(f"Flips and rotation transforms: {time.time() - step_start:.3f}s")

        final_mapped_rect = map_rect_to_original(
                img_ref.shape,
                rgb_brightness_normalized.shape,
                reference_rect
            )
        x1, y1, x2, y2 = final_mapped_rect
        del final_mapped_rect  # Clean up as it's no longer needed
        step_start = time.time()
        if water_mark:
            # Ensure the array is contiguous for OpenCV
            rgb_brightness_normalized = np.ascontiguousarray(rgb_brightness_normalized)
            
            # Add a watermark to the image
            watermark_text = "FreeCCR Unpaid Demo"
            font = cv2.FONT_HERSHEY_SIMPLEX
            # Make font_scale so text height is about 1/10 of image height
            # At font_scale=1, text height is about 32 px, so scale accordingly
            font_scale = rgb_brightness_normalized.shape[1] / (30 * 32)
            font_thickness = max(3, int(font_scale * 2))
            text_size = cv2.getTextSize(watermark_text, font, font_scale, font_thickness)[0]
            text_x = max(0, int(x2 - text_size[0] - 10))
            text_y = max(text_size[1], int(y2 - text_size[1] - 10))
            cv2.putText(
                rgb_brightness_normalized,
                watermark_text,
                (text_x, text_y),
                font,
                font_scale,
                (30000, 30000, 30000),
                font_thickness
            )
        print(f"Watermark: {time.time() - step_start:.3f}s")

        print(f"Rotated image by {angle} degrees (no crop)")
        step_start = time.time()
        if output_path is not None: # this is for output
            # when outputting rotate original image as well
            h, w = rgb_brightness_normalized.shape[:2]
            center = (w // 2, h // 2)
            rot_mat = cv2.getRotationMatrix2D(center, -fine_angle, 1.0)
            abs_cos = abs(rot_mat[0, 0])
            abs_sin = abs(rot_mat[0, 1])
            new_w = int(w * abs_cos + h * abs_sin)
            new_h = int(h * abs_cos + w * abs_sin)
            rot_mat[0, 2] += (new_w - w) / 2
            rot_mat[1, 2] += (new_h - h) / 2
            try:
                rgb_brightness_normalized = cv2.warpAffine(
                    rgb_brightness_normalized, rot_mat, (new_w, new_h),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=0
                )
            except Exception as e:
                print(f"Warning: warpAffine failed due to image size or memory error: {e}")
            # Clean up rotation variables
            del rot_mat, center, abs_cos, abs_sin
        print(f"Final rotation: {time.time() - step_start:.3f}s")

            # angle = ccr_image.rotation_angle
            # if angle != 0:
            #     rot_mat = cv2.getRotationMatrix2D(center, -angle, 1.0)
            #     rgb_brightness_normalized = cv2.warpAffine(
            #         rgb_brightness_normalized,
            #         rot_mat,
            #         (w, h),
            #         flags=cv2.INTER_LINEAR,
            #         borderMode=cv2.BORDER_CONSTANT,
            #         borderValue=0
            #     )

        # Ensure output_path has proper extension and handle Unicode
        step_start = time.time()
        output_path = safe_unicode_path(output_path)
        if max_long_side:
            rgb_brightness_normalized = ccr_image.resize_image_to_max_pixel(rgb_brightness_normalized, max_long_side)
        if jpg_out:
            output_path = os.path.splitext(output_path)[0] + ".jpg"
            # Convert to 8-bit for JPEG output
            rgb_brightness_normalized_8 = to_8bit(rgb_brightness_normalized)
            # Ensure output is RGB, not BGR
            rgb_brightness_normalized_8 = cv2.cvtColor(rgb_brightness_normalized_8, cv2.COLOR_RGB2BGR)
            success = safe_cv2_imwrite(output_path, rgb_brightness_normalized_8,
                                       [cv2.IMWRITE_JPEG_QUALITY, int(jpg_quality)])
            del rgb_brightness_normalized_8  # Clean up 8-bit copy
            if success:
                print(f"Normalized image saved to {output_path}")
            else:
                raise IOError(f"Failed to save normalized image to {output_path}")
        else:
            output_path = os.path.splitext(output_path)[0] + ".tiff"
            success = safe_tifffile_imwrite(output_path, rgb_brightness_normalized, compression='deflate')
            if success:
                print(f"Normalized image saved to {output_path}")
            else:
                raise IOError(f"Failed to save normalized image to {output_path}")
        print(f"File saving: {time.time() - step_start:.3f}s")
        #debug ----------------------v

        # img_disp = to_8bit(rgb_brightness_normalized)
        # # Draw the mapped reference rectangle on the display
        
        # cv2.rectangle(img_disp, (x1, y1), (x2 - 1, y2 - 1), (0, 255, 0), 2)
        # if img_disp.ndim == 2:
        #     img_disp = cv2.cvtColor(img_disp, cv2.COLOR_GRAY2RGB)
        # plt.figure(figsize=(8, 8))
        # plt.imshow(img_disp)
        # plt.title("CCR Normalized Image")
        # plt.axis('off')
        # plt.show()
        total_elapsed = time.time() - total_start_time
        print(f"TOTAL CCR normalization time: {total_elapsed:.3f}s")
        gc.collect()
        return None  # Return None for output processing

    total_elapsed = time.time() - total_start_time
    print(f"TOTAL CCR normalization time: {total_elapsed:.3f}s")
    return rgb_brightness_normalized

def ccr_normalize_with_bwpoint(ccr_image, black_point_bgr, white_point_bgr,
                               output_path=None, water_mark=True, jpg_out=False,
                               jpg_quality=95, max_long_side=None):
    """
    Film negative conversion using the same pipeline as ccr_normalize_with_reference
    but with explicit per-channel B/W points instead of auto-detected percentiles.

    black_point_bgr: (B,G,R) scan values of transparent/clear film area (HIGH values).
                     Transparent areas → output black (0) after inversion.
    white_point_bgr: (B,G,R) scan values of dense/exposed film area (LOW values).
                     Dense areas → output white (65535) after inversion.

    Pipeline: BWPN (user B/W points) → inversion → saturation boost → shadow correction
    ODAI is skipped because per-channel B/W point mapping already normalises channels.
    """
    total_start_time = time.time()

    # --- Load working image ---
    if output_path is not None:
        img = ccr_image.read_image(ccr_image.file_path, preview=False)
    else:
        img = ccr_image.resized_raw
    if img is None:
        raise ValueError("CCRImage: could not load image data for B/W point conversion")

    # --- Fine rotation (same as main pipeline) ---
    fine_angle = ccr_image.fine_rotation_angle / 100.0
    h_flip = ccr_image.horizontal_mirrored
    v_flip = ccr_image.vertical_mirrored
    h, w = img.shape[:2]
    if fine_angle != 0:
        center_rot = (w // 2, h // 2)
        rot_mat = cv2.getRotationMatrix2D(center_rot, -fine_angle, 1.0)
        img = cv2.warpAffine(img, rot_mat, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        del rot_mat

    # --- BWPN: B/W point values are absolute anchors, constant across the whole roll ---
    #
    # With no_auto_bright=True in rawpy, film base and dense-area values are identical in every
    # frame. The sampled B/W points are applied directly as fixed per-channel anchors.
    img_f = img.astype(np.float32)
    norm = np.empty_like(img_f)
    for c in range(3):
        p_hi = max(float(black_point_bgr[c]), 1.0)   # transparent film (film base) → maps to black
        p_lo = max(float(white_point_bgr[c]), 1.0)   # dense film (exposed area) → maps to white

        denom = p_hi - p_lo
        if abs(denom) < 1.0:
            norm[..., c] = 0.0
            continue
        np.subtract(img_f[..., c], p_lo, out=norm[..., c])
        np.divide(norm[..., c], denom, out=norm[..., c])
        np.multiply(norm[..., c], 65535.0, out=norm[..., c])
        np.clip(norm[..., c], 0, 65535, out=norm[..., c])
    del img_f
    print(f"BWPN (user points): {time.time() - total_start_time:.3f}s")

    # --- Inversion (ODAI skipped: per-channel B/W points already equalise channels) ---
    rgb_inverted = (65535.0 - norm).clip(0, 65535).astype(np.uint16)
    del norm
    gc.collect()

    # --- Saturation boost (identical to main pipeline) ---
    rgb_norm = rgb_inverted.astype(np.float32) / 65535.0
    gamma_corrected = np.power(np.clip(rgb_norm, 0.0, 1.0), 1.0)
    del rgb_norm

    luminance = np.dot(gamma_corrected[..., :3], [0.299, 0.587, 0.114])
    luminance_expanded = np.expand_dims(luminance, axis=-1)
    saturation_curve = np.power(luminance, 0.8)
    del luminance
    base_saturation = 1.15
    min_saturation = 1.00
    dynamic_saturation = min_saturation + (base_saturation - min_saturation) * saturation_curve
    del saturation_curve
    dynamic_saturation = np.expand_dims(dynamic_saturation, axis=-1)
    gamma_corrected = luminance_expanded + dynamic_saturation * (gamma_corrected - luminance_expanded)
    del luminance_expanded, dynamic_saturation
    gamma_corrected = np.clip(gamma_corrected, 0.0, 1.0)

    # --- Shadow warmth correction (identical to main pipeline) ---
    shadow_corrected = gamma_corrected
    shadow_luminance = np.dot(shadow_corrected[..., :3], [0.299, 0.587, 0.114])
    warmth_curve = np.exp(-shadow_luminance * 4.0)
    warmth_strength = 0.35 * warmth_curve
    del warmth_curve
    green_curve = np.exp(-shadow_luminance * 3.5)
    del shadow_luminance
    green_strength = 0.15 * green_curve
    del green_curve
    shadow_corrected[..., 0] *= (1.0 + warmth_strength * 0.8)
    shadow_corrected[..., 1] *= (1.0 + green_strength)
    shadow_corrected[..., 2] *= (1.0 - warmth_strength)
    del warmth_strength, green_strength

    rgb_result = np.clip(shadow_corrected * 65535.0, 0, 65535).astype(np.uint16)
    del shadow_corrected, gamma_corrected, rgb_inverted
    gc.collect()

    # Non-destructive base offsets applied through the adjustment pipeline (UI shows 0).
    ccr_image.contrast_base = 60
    ccr_image.temperature_base = 10

    # --- User adjustments (export only) ---
    if output_path is not None:
        rgb_result = ccr_image.apply_adjustments(rgb_result)

    # --- Export path: flips, rotation, watermark, file write ---
    if output_path is not None:
        # User crop (normalized rect in un-rotated/un-flipped space) — applied
        # before flips/rotation so it matches the cropped preview orientation.
        rgb_result = apply_crop_to_image(rgb_result, getattr(ccr_image, 'crop_rect', None))
        # Flips
        if h_flip and v_flip:
            rgb_result = cv2.flip(rgb_result, -1)
        elif h_flip:
            rgb_result = cv2.flip(rgb_result, 1)
        elif v_flip:
            rgb_result = cv2.flip(rgb_result, 0)

        # 90-degree rotation
        angle = ccr_image.rotation_angle % 360
        if angle == 90:
            rgb_result = np.rot90(rgb_result, k=3)
        elif angle == 180:
            rgb_result = np.rot90(rgb_result, k=2)
        elif angle == 270:
            rgb_result = np.rot90(rgb_result, k=1)

        # Watermark (positioned at bottom-right of image)
        if water_mark:
            rgb_result = np.ascontiguousarray(rgb_result)
            h_out, w_out = rgb_result.shape[:2]
            watermark_text = "FreeCCR Unpaid Demo"
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = w_out / (30 * 32)
            font_thickness = max(3, int(font_scale * 2))
            text_size = cv2.getTextSize(watermark_text, font, font_scale, font_thickness)[0]
            text_x = max(0, w_out - text_size[0] - 10)
            text_y = max(text_size[1], h_out - text_size[1] - 10)
            cv2.putText(rgb_result, watermark_text, (text_x, text_y),
                        font, font_scale, (30000, 30000, 30000), font_thickness)

        # Fine rotation at full resolution
        if fine_angle != 0:
            h_r, w_r = rgb_result.shape[:2]
            center_r = (w_r // 2, h_r // 2)
            rot_mat = cv2.getRotationMatrix2D(center_r, -fine_angle, 1.0)
            abs_cos = abs(rot_mat[0, 0])
            abs_sin = abs(rot_mat[0, 1])
            new_w = int(w_r * abs_cos + h_r * abs_sin)
            new_h = int(h_r * abs_cos + w_r * abs_sin)
            rot_mat[0, 2] += (new_w - w_r) / 2
            rot_mat[1, 2] += (new_h - h_r) / 2
            try:
                rgb_result = cv2.warpAffine(rgb_result, rot_mat, (new_w, new_h),
                                             flags=cv2.INTER_LINEAR,
                                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            except Exception as e:
                print(f"Warning: warpAffine failed: {e}")

        # Write file
        output_path = safe_unicode_path(output_path)
        if max_long_side:
            rgb_result = ccr_image.resize_image_to_max_pixel(rgb_result, max_long_side)
        if jpg_out:
            output_path = os.path.splitext(output_path)[0] + ".jpg"
            img_8 = to_8bit(rgb_result)
            img_8 = cv2.cvtColor(img_8, cv2.COLOR_RGB2BGR)
            if not safe_cv2_imwrite(output_path, img_8,
                                    [cv2.IMWRITE_JPEG_QUALITY, int(jpg_quality)]):
                raise IOError(f"Failed to save image to {output_path}")
        else:
            output_path = os.path.splitext(output_path)[0] + ".tiff"
            if not safe_tifffile_imwrite(output_path, rgb_result, compression='deflate'):
                raise IOError(f"Failed to save image to {output_path}")
        print(f"TOTAL bwpoint normalization time: {time.time() - total_start_time:.3f}s")
        gc.collect()
        return None

    print(f"TOTAL bwpoint normalization time: {time.time() - total_start_time:.3f}s")
    return rgb_result


def to_8bit(img16: np.ndarray) -> np.ndarray:
    # Clip to 16-bit range, then scale to 8-bit
    img16 = np.clip(img16, 0, 65535)
    img8 = (img16 / 257).astype(np.uint8)
    return img8


def apply_crop_to_image(img: np.ndarray, crop_rect_norm) -> np.ndarray:
    """
    Crop an image using a rect of normalized (x1, y1, x2, y2) fractions
    defined in un-rotated/un-flipped image space (the same space as
    resized_raw). Returns the input unchanged when the rect is missing or
    degenerate, so callers can pass it unconditionally.
    """
    if crop_rect_norm is None:
        return img
    h, w = img.shape[:2]
    fx1, fy1, fx2, fy2 = crop_rect_norm
    x1 = max(0, min(w - 1, int(round(fx1 * w))))
    y1 = max(0, min(h - 1, int(round(fy1 * h))))
    x2 = max(x1 + 1, min(w, int(round(fx2 * w))))
    y2 = max(y1 + 1, min(h, int(round(fy2 * h))))
    if (x2 - x1) < 2 or (y2 - y1) < 2:
        return img
    return img[y1:y2, x1:x2]

def auto_fine_angle(img16: np.ndarray, debug: bool = False) -> float:
    """
    Analyze a 16-bit image and estimate the rotation angle (in degrees)
    needed to make the dominant horizontal lines horizontal.

    Args:
        img16 (np.ndarray): 16-bit input image (H, W) or (H, W, 3)
        debug (bool): If True, show the most significant line on the image.

    Returns:
        float: Estimated rotation angle in degrees (positive = counterclockwise)
    """
    # Convert to 8-bit grayscale
    if img16.ndim == 3:
        gray = cv2.cvtColor((img16 / 257).astype(np.uint8), cv2.COLOR_BGR2GRAY)
        img_rgb = cv2.cvtColor((img16 / 257).astype(np.uint8), cv2.COLOR_BGR2RGB)
    else:
        gray = (img16 / 257).astype(np.uint8)
        img_rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)

    # Edge detection
    edges = cv2.Canny(gray, 160, 255, apertureSize=3)

    # if debug:
    #     plt.figure(figsize=(8, 8))
    #     plt.imshow(edges, cmap='gray')
    #     plt.title("Edge Detection")
    #     plt.axis('off')
    #     plt.show()

    # Hough Line Transform
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=gray.shape[1] // 4, maxLineGap=20)
    if lines is None:
        if debug:
            print("No lines detected.")
        return 0.0

    # Find the longest nearly-horizontal line
    max_len = 0
    best_angle = 0.0
    best_line = None
    for line in lines:
        x1, y1, x2, y2 = line[0]
        dx = x2 - x1
        dy = y2 - y1
        length = np.hypot(dx, dy)
        angle = np.degrees(np.arctan2(dy, dx))
        # Consider lines within +/- 30 degrees of horizontal
        if (abs(angle) < 30 or abs(angle) > 150) and length > max_len:
            max_len = length
            best_angle = angle
            best_line = (x1, y1, x2, y2)

    # if debug and best_line is not None:

    #     img_debug = img_rgb.copy()
    #     x1, y1, x2, y2 = best_line
    #     # Draw the best line in red
    #     cv2.line(img_debug, (x1, y1), (x2, y2), (255, 0, 0), 2)
    #     plt.figure(figsize=(8, 8))
    #     plt.imshow(img_debug)
    #     plt.title(f"Longest horizontal-like line (angle={best_angle:.2f}°)")
    #     plt.axis('off')
    #     plt.show()

    # The angle is relative to the x-axis; positive = counterclockwise
    # Return negative to indicate the rotation needed to deskew
    return -best_angle if best_line is not None else 0.0


def auto_frame_v2(img16: np.ndarray, fine_rotation_angle: int, debug: bool = False) -> tuple:
    """
    Optimized auto_frame using white/black area masking and largest rectangle detection.
    Based on the methodology from the POC notebook with improved workflow.
    
    Args:
        img16 (np.ndarray): 16-bit input image (H, W) or (H, W, 3)
        fine_rotation_angle (int): Fine rotation angle in hundredths of a degree
        debug (bool): If True, show the detected frame on the image.
        
    Returns:
        tuple: (x1, y1, x2, y2) coordinates of the reference frame rectangle.
    """
    original_shape = img16.shape
    
    # Step 1: Apply fine rotation
    angle = fine_rotation_angle / 100.0
    h, w = img16.shape[:2]
    center = (w // 2, h // 2)
    if angle != 0:
        rot_mat = cv2.getRotationMatrix2D(center, -angle, 1.0)
        img16 = cv2.warpAffine(img16, rot_mat, (w, h), flags=cv2.INTER_LINEAR, 
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    print(f"Rotated image by {angle} degrees, original shape: {original_shape}, new shape: {img16.shape}")

    # Step 2: Create white area mask
    def create_white_area_mask(image, threshold_percentile=95, min_brightness=0.9):
        """Create a binary mask for white/bright areas in the image"""
        if image.ndim == 3:
            # Convert RGB to grayscale using luminance formula
            gray = 0.2 * image[..., 0] + 0.3 * image[..., 1] + 0.5 * image[..., 2]
        else:
            gray = image.copy()
        
        # Normalize to 0-1 range
        gray_norm = (gray - gray.min()) / (np.ptp(gray) + 1e-8)
        
        # Create binary mask for white areas
        white_mask = gray_norm > min_brightness
        return white_mask

    # Step 3: Create black area mask  
    def create_black_area_mask(image, red_weight=0.5, blue_weight=0.2, threshold=0.02):
        """Create a binary mask for black/dark areas using red and blue channel weighted grayscale"""
        if image.ndim == 3:
            # Calculate green weight to ensure weights sum to 1
            green_weight = 1.0 - red_weight - blue_weight
            green_weight = max(0.0, green_weight)  # Ensure non-negative
            
            # Weighted grayscale conversion with all three channels
            gray = (red_weight * image[..., 0] + 
                   green_weight * image[..., 1] + 
                   blue_weight * image[..., 2])
        else:
            gray = image.copy()
        
        # Normalize to 0-1 range
        gray_norm = (gray - gray.min()) / (np.ptp(gray) + 1e-8)
        
        # Create binary mask for black areas
        black_mask = gray_norm < threshold
        return black_mask

    # Step 4: Create combined mask
    white_mask = create_white_area_mask(img16, min_brightness=0.9)
    black_mask = create_black_area_mask(img16, red_weight=0.6, blue_weight=0.2, threshold=0.02)
    
    # Combine white and black masks using OR operation
    combined_mask = np.logical_or(white_mask, black_mask)
    
    # Apply morphological operations to clean up the combined mask
    kernel = np.ones((5, 5), np.uint8)
    combined_mask_cleaned = cv2.morphologyEx(combined_mask.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    combined_mask = combined_mask_cleaned.astype(bool)

    # Step 5: Find largest non-masked rectangle using optimized histogram method
    def find_largest_non_masked_rectangle(mask):
        """Find the largest rectangle that contains only False values in a binary mask"""
        # Invert mask so we're looking for areas with True values (non-masked areas)
        inverted_mask = ~mask
        
        rows, cols = inverted_mask.shape
        heights = np.zeros(cols, dtype=int)
        max_area = 0
        best_rect = (0, 0, 0, 0, 0)  # (top, left, height, width, area)
        
        for row in range(rows):
            # Update heights histogram
            for col in range(cols):
                if inverted_mask[row, col]:
                    heights[col] += 1
                else:
                    heights[col] = 0
            
            # Find largest rectangle in current histogram
            stack = []
            for col in range(cols + 1):
                h = heights[col] if col < cols else 0
                
                while stack and heights[stack[-1]] > h:
                    height = heights[stack.pop()]
                    width = col if not stack else col - stack[-1] - 1
                    area = height * width
                    
                    if area > max_area:
                        max_area = area
                        left = 0 if not stack else stack[-1] + 1
                        top = row - height + 1
                        best_rect = (top, left, height, width, area)
                
                stack.append(col)
        
        # Shrink the rectangle's long side by 2%, shrink both ends
        # Also shrink the height side by 0.5%
        top, left, height, width, area = best_rect
        if height > width:
            # Height is the long side
            shrink_amount = int(height * 0.02)
            height = max(1, height - 2 * shrink_amount)
            top += shrink_amount
            # Also shrink width by 0.5%
            width_shrink = int(width * 0.01)
            width = max(1, width - 2 * width_shrink)
            left += width_shrink
        else:
            # Width is the long side  
            shrink_amount = int(width * 0.02)
            width = max(1, width - 2 * shrink_amount)
            left += shrink_amount
            # Also shrink height by 0.5%
            height_shrink = int(height * 0.01)
            height = max(1, height - 2 * height_shrink)
            top += height_shrink
        
        area = height * width
        best_rect = (top, left, height, width, area)
        
        return best_rect

    # Find the largest valid rectangle
    top, left, rect_height, rect_width, area = find_largest_non_masked_rectangle(combined_mask)
    
    if rect_height == 0 or rect_width == 0:
        # fallback: whole image
        print("Warning: No valid rectangle found, using whole image")
        return map_rect_to_original(img16.shape, original_shape, (0, 0, w, h))
    
    # Convert to x1, y1, x2, y2 format
    x1, y1 = left, top
    x2, y2 = left + rect_width, top + rect_height
    
    # Add small padding if possible
    padding = 2
    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(w, x2 + padding)
    y2 = min(h, y2 + padding)
    
    # Map the rectangle back to original image size
    final_rect = map_rect_to_original(img16.shape, original_shape, (x1, y1, x2, y2))
    
    print(f"Detected rectangle: {rect_width}x{rect_height} (area: {area} pixels, "
          f"{area/(h*w)*100:.2f}% of image)")
    
    # Debug visualization (if enabled)
    # if debug:
    #     img_disp = to_8bit(img16)
    #     if img_disp.ndim == 2:
    #         img_disp = cv2.cvtColor(img_disp, cv2.COLOR_GRAY2RGB)
    #     cv2.rectangle(img_disp, (x1, y1), (x2 - 1, y2 - 1), (0, 255, 0), 2)
    #     plt.figure(figsize=(8, 8))
    #     plt.imshow(img_disp)
    #     plt.title("auto_frame_v2: Final Cropping Box")
    #     plt.axis('off')
    #     plt.show()
    
    return final_rect

def auto_frame(img16: np.ndarray, fine_rotation_angle: int, debug: bool = False) -> tuple:
    """
    Automatically determine the reference frame for an image by detecting the largest rectangle,
    excluding pure black and pure white areas.

    Args:
        img16 (np.ndarray): 16-bit input image (H, W) or (H, W, 3)
        fine_rotation_angle (int): Fine rotation angle in hundredths of a degree
        debug (bool): If True, show the detected frame on the image.

    Returns:
        tuple: (x1, y1, x2, y2) coordinates of the reference frame rectangle.
    """
    # Apply fine rotation
    angle = fine_rotation_angle / 100.0
    h, w = img16.shape[:2]
    center = (w // 2, h // 2)
    if angle != 0:
        rot_mat = cv2.getRotationMatrix2D(center, -angle, 1.0)
        img16 = cv2.warpAffine(img16, rot_mat, (w, h), flags=cv2.INTER_LINEAR,  borderMode=cv2.BORDER_CONSTANT,borderValue=0)

    img8 = to_8bit(img16)

    # Select only black and white pixels
    if img8.ndim == 3:
        img_hsv = cv2.cvtColor(img8, cv2.COLOR_BGR2HSV)
        v = img_hsv[..., 2]
        s = img_hsv[..., 1]
        black_mask = v < 40
        white_mask = (v > 240) & (s < 30)
        bw_mask = (black_mask | white_mask).astype(np.uint8)
    else:
        black_mask = img8 < 50
        white_mask = img8 > 240
        bw_mask = (black_mask | white_mask).astype(np.uint8)


    # if debug:
    #     plt.figure(figsize=(8, 8))
    #     plt.imshow(bw_mask, cmap='gray')
    #     plt.title("Inverted BW Mask (Content Region)")
    #     plt.axis('off')
    #     plt.show()
    # Morphological closing to connect black and white regions
    kernel = np.ones((301, 301), np.uint8)
    bw_mask_closed = cv2.morphologyEx(bw_mask, cv2.MORPH_CLOSE, kernel)

    # Invert the mask to get the content region
    content_mask = 1 - bw_mask_closed



    # Find largest connected component in the content mask
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(content_mask, connectivity=8)
    if num_labels <= 1:
        # Only background found
        return (0, 0, img8.shape[1], img8.shape[0])
    # Ignore label 0 (background), find largest
    largest_label = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    x = stats[largest_label, cv2.CC_STAT_LEFT]
    y = stats[largest_label, cv2.CC_STAT_TOP]
    w = stats[largest_label, cv2.CC_STAT_WIDTH]
    h = stats[largest_label, cv2.CC_STAT_HEIGHT]
    x1, y1, x2, y2 = x, y, x + w, y + h

    # if debug:
    #     img_debug = img8.copy()
    #     if img_debug.ndim == 2:
    #         img_debug = cv2.cvtColor(img_debug, cv2.COLOR_GRAY2RGB)
    #     cv2.rectangle(img_debug, (x1, y1), (x2 - 1, y2 - 1), (255, 0, 0), 2)
    #     plt.figure(figsize=(8, 8))
    #     plt.imshow(img_debug)
    #     plt.title("Detected Largest Valid Rectangle")
    #     plt.axis('off')
    #     plt.show()

    return (x1, y1, x2, y2)

def map_rect_to_original(resized_shape, original_shape, rect):
    """
    Map rectangle coordinates from resized image to original image size.

    Args:
        resized_shape (tuple): (height, width) of resized image
        original_shape (tuple): (height, width) of original image
        rect (tuple): (x1, y1, x2, y2) in resized image

    Returns:
        tuple: (x1, y1, x2, y2) mapped to original image coordinates (rounded to int)
    """
    rh, rw = resized_shape[:2]
    oh, ow = original_shape[:2]
    x1, y1, x2, y2 = rect

    scale_x = ow / rw
    scale_y = oh / rh

    x1o = int(round(x1 * scale_x))
    y1o = int(round(y1 * scale_y))
    x2o = int(round(x2 * scale_x))
    y2o = int(round(y2 * scale_y))
    return (x1o, y1o, x2o, y2o)


def compute_neutral_temp_tint(r: float, g: float, b: float,
                              tint_balance_factor: float = 1.0) -> tuple:
    """
    Given the mean RGB (0–65535) of a user-picked neutral reference point,
    compute the temperature and tint slider values [-100, 100] that make that
    point neutral (R == G == B) after adjust_image's temperature/tint stage.

    Inverts the same perceptual formulas adjust_image applies:
        temperature:  r *= (1 + s),  b *= (1 - s)
                      s = (slider/100) * 0.40 * tone_curve
        tint:         g *= (1 - t),  r *= (1 + 0.3t),  b *= (1 + 0.3t)
                      t = tanh(slider * 0.02) * 0.18 * tone_curve
                          * balance_factor * skin_tone_sensitivity
    Solving r(1+s) = b(1-s) gives s; then m(1+0.3t) = g(1-t) gives t, where
    m is the common R/B value after the temperature step.
    """
    eps = 1e-6
    r = max(float(r), eps)
    g = max(float(g), eps)
    b = max(float(b), eps)
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 65535.0

    # Same piecewise tone-aware strength curve as adjust_image (luminance is
    # taken from the un-adjusted pixel there too, so this matches apply time).
    if lum <= 0.3:
        tone = 0.8 + 0.2 * min(max(lum / 0.3, 0.0), 1.0)
    elif lum <= 0.6:
        tone = 1.0
    else:
        progress = (lum - 0.6) / 0.4
        sigmoid = 1.0 / (1.0 + np.exp(-8.0 * (progress - 0.5)))
        tone = 1.0 - 0.75 * sigmoid

    # Temperature: choose s so the R and B channels meet.
    s = (b - r) / (b + r)
    temp_slider = float(np.clip(s * 100.0 / (0.40 * tone), -100.0, 100.0))

    # Use the achieved (possibly clamped) scale for the tint step.
    s_eff = (temp_slider / 100.0) * 0.40 * tone
    m = (r * (1.0 + s_eff) + b * (1.0 - s_eff)) / 2.0

    # Tint: choose t so G meets the common R/B level m.
    t = (g - m) / (g + 0.3 * m)
    skin = 1.0 + 0.5 * np.exp(-12.0 * (lum - 0.35) ** 2)
    denom = 0.18 * tone * tint_balance_factor * skin
    x = float(np.clip(t / max(denom, eps), -0.999, 0.999))
    tint_slider = float(np.clip(np.arctanh(x) / 0.02, -100.0, 100.0))

    return int(round(temp_slider)), int(round(tint_slider))


def adjust_image(
    img16: np.ndarray,
    kelvin_shift: float = 0.0,
    tint_shift: float = 0.0,
    exposure: float = 0.0,
    brightness: float = 0.0,
    blackpoint: float = 0.0,
    whitepoint: float = 0.0,
    contrast: float = 0.0,
    saturation: float = 0.0,
    tint_balance_factor: float = 1.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    ch_input_gain: float = 0.0,
    ch_master_shift: float = 0.0,
    ch_master_gain: float = 0.0,
    ch_r_shift: float = 0.0,
    ch_r_gain: float = 0.0,
    ch_r_blackpoint: float = 0.0,
    ch_g_shift: float = 0.0,
    ch_g_gain: float = 0.0,
    ch_g_blackpoint: float = 0.0,
    ch_b_shift: float = 0.0,
    ch_b_gain: float = 0.0,
    ch_b_blackpoint: float = 0.0,
    sub_saturation: float = 0.0,
) -> np.ndarray:
    """
    Apply temperature, tint, exposure, brightness, blackpoint, whitepoint, highlights, shadows,
    contrast, and saturation adjustments to a 16-bit image.
    All input factors are in range [-100, 100], 0 = no change.
    Returns a 16-bit image.
    """
    img = img16.astype(np.float32)

    # Map input factors from [-100, 100] to useful ranges
    kelvin_scale = 0.003 * kelvin_shift      # -1.0 to +1.0
    tint_scale = 0.002 * tint_shift          # -1.0 to +1.0
    exposure_scale = exposure * 2.0 / 100.0  # -2.0 to +2.0 stops
    brightness_scale = brightness / 8.0
    blackpoint_scale = 1.0 - (blackpoint / 100.0)    # -1.0 to +1.0 (fraction of 65535)
    whitepoint_scale = 1.0 + (whitepoint / 100.0)  # 0.0 to 2.0 (scaling factor)
    contrast_scale = 1.0 + (contrast / 100.0)      # 0.0 to 2.0
    saturation_scale = 1.0 + (saturation / 100.0)  # 0.0 to 2.0

    # Temperature and Tint (Lightroom-like perceptual adjustments)
    if kelvin_shift != 0.0 or tint_shift != 0.0:
        # Calculate luminance for tone-aware masking
        img_norm = img / 65535.0
        luminance = np.dot(img_norm[..., :3], [0.299, 0.587, 0.114])
        
        # Create smooth asymmetric tone-aware strength curve (Lightroom-like)
        # Shadows get strong effect, midtones get maximum effect, highlights get minimal effect
        # Using piecewise smooth curves for natural transitions
        
        # Define strength levels for different tonal regions
        shadow_strength = 0.8      # 80% strength in shadows (0-30% luminance)
        midtone_strength = 1.0     # 100% strength in midtones (30-60% luminance)  
        highlight_strength = 0.25  # 25% strength in highlights (60-100% luminance)
        
        # Transition points
        shadow_to_mid = 0.3       # Shadows to midtones transition at 30% luminance
        mid_to_highlight = 0.6    # Midtones to highlights transition at 60% luminance
        
        # Create smooth asymmetric curve using sigmoid blending
        tone_curve = np.zeros_like(luminance)
        
        # Shadow region (0-30%): smooth transition from 80% to 100%
        shadow_mask = luminance <= shadow_to_mid
        shadow_progress = np.clip(luminance[shadow_mask] / shadow_to_mid, 0, 1)
        tone_curve[shadow_mask] = shadow_strength + (midtone_strength - shadow_strength) * shadow_progress
        
        # Midtone region (30-60%): stay at 100% strength
        midtone_mask = (luminance > shadow_to_mid) & (luminance <= mid_to_highlight)
        tone_curve[midtone_mask] = midtone_strength
        
        # Highlight region (60-100%): smooth sigmoid transition from 100% to 25%
        highlight_mask = luminance > mid_to_highlight
        highlight_progress = (luminance[highlight_mask] - mid_to_highlight) / (1.0 - mid_to_highlight)
        # Use sigmoid for smooth natural rolloff
        sigmoid_factor = 1.0 / (1.0 + np.exp(-8 * (highlight_progress - 0.5)))
        tone_curve[highlight_mask] = midtone_strength - (midtone_strength - highlight_strength) * sigmoid_factor
        
        # Expand tone_curve to match image dimensions for broadcasting
        tone_curve = np.expand_dims(tone_curve, axis=-1)
        
        # Temperature (R/B scaling with logarithmic perceptual response)
        if kelvin_shift != 0.0:
            # Map slider values [-100, 100] to Kelvin temperatures [2000K, 8000K]
            # Neutral point (slider 0) = 5000K
            neutral_kelvin = 5000.0
            if kelvin_shift > 0:
                # Positive shift: 0 to +100 maps to 5000K to 8000K
                current_kelvin = neutral_kelvin + (kelvin_shift / 100.0) * 3000.0
            else:
                # Negative shift: -100 to 0 maps to 2000K to 5000K
                current_kelvin = neutral_kelvin + (kelvin_shift / 100.0) * 3000.0
            
            # Calculate Kelvin delta from neutral
            kelvin_delta = current_kelvin - neutral_kelvin
            
            # Logarithmic scaling for Kelvin - stronger impact at low end
            # Simulate the fact that 3000K->4000K has more visual impact than 7000K->8000K
            kelvin_abs = abs(kelvin_delta)
            
            # Create logarithmic response curve based on actual Kelvin values
            # Linear scale: 1K delta ≈ 0.013% R/B shift; full 3000K = 40% shift
            perceptual_scale = (kelvin_delta / 3000.0) * 0.40

            # tone_curve already handles spatial (shadow/highlight) weighting
            effective_scale = perceptual_scale * tone_curve[..., 0]
            
            r_scale = 1.0 + effective_scale
            b_scale = 1.0 - effective_scale
            
            img[..., 0] *= r_scale  # R
            img[..., 2] *= b_scale  # B

        # Tint (G-M scaling with perceptual mapping and enhanced midtone sensitivity)
        if tint_shift != 0.0:
            # Perceptual mapping for tint - non-linear response based on existing color balance
            # Tint impact varies with the current white balance state
            
            # Use pre-calculated balance factor instead of calculating it here
            balance_factor = tint_balance_factor
            
            # Enhanced midtone and skin tone sensitivity for tint
            # Tint is most visible in skin tones and neutral areas
            skin_tone_sensitivity = 1.0 + 0.5 * np.exp(-12 * (luminance - 0.35)**2)  # Peak at 35% luminance
            skin_tone_sensitivity = np.expand_dims(skin_tone_sensitivity, axis=-1)
            
            # Create perceptual tint curve - stronger response in certain ranges
            tint_abs = abs(tint_shift)
            if tint_abs > 0:
                # Sigmoid-like curve for tint perception
                perceptual_tint = np.tanh(tint_abs * 0.02) * np.sign(tint_shift) * 0.18
            else:
                perceptual_tint = 0.0
            
            # Apply perceptual tint with tone awareness, balance factor, and skin tone sensitivity
            effective_tint = perceptual_tint * tone_curve[..., 0] * balance_factor * skin_tone_sensitivity[..., 0]
            
            # Tint primarily affects green, with complementary adjustments to R/B
            g_scale = 1.0 - effective_tint  # Green channel (inverse of tint shift)
            r_scale = 1.0 + (0.3 * effective_tint)  # Slight red compensation
            b_scale = 1.0 + (0.3 * effective_tint)  # Slight blue compensation
            
            img[..., 1] *= g_scale  # G
            img[..., 0] *= r_scale  # R  
            img[..., 2] *= b_scale  # B

    # Exposure (Adobe-like, tone-aware to preserve highlights)
    if exposure != 0.0:
        # Calculate luminance for tone-aware exposure mapping
        img_norm = img / 65535.0
        luminance = np.dot(img_norm[..., :3], [0.299, 0.587, 0.114])
        
        # Create smooth, continuous tone-aware exposure curve
        # Full effect in shadows/midtones, smoothly reduced effect in highlights
        # Using a smooth sigmoid-like transition instead of sharp cutoff
        transition_midpoint = 0.80   # Where the curve inflection point is (80% luminance)
        transition_width = 0.15      # Controls smoothness of transition
        min_strength = 0.03          # Minimum exposure effect in pure highlights (15%)
        max_strength = 1.0           # Maximum exposure effect in shadows/midtones
        
        # Smooth sigmoid-like curve for continuous transition
        # Formula: strength = min + (max-min) * (1 / (1 + exp((lum - mid) / width)))
        exposure_curve = min_strength + (max_strength - min_strength) * (
            1.0 / (1.0 + np.exp((luminance - transition_midpoint) / transition_width))
        )
        
        # Expand curve to match image dimensions
        exposure_curve = np.expand_dims(exposure_curve, axis=-1)
        
        # Apply tone-aware exposure
        exposure_factor = 2 ** exposure_scale  # Base exposure factor in stops
        
        # Create per-pixel exposure factors
        pixel_exposure_factors = 1.0 + (exposure_factor - 1.0) * exposure_curve
        
        img *= pixel_exposure_factors
    
    # Brightness
    if brightness != 0.0:
        img_norm = img / 65535.0
        brightness_scale = brightness / 8.0
        curve = 1.0 - 0.3 * brightness_scale
        img_norm = np.power(img_norm, curve)
        img_norm = np.clip(img_norm, 0.0, 1.0)
        img = img_norm * 65535.0

    # Highlights / Shadows (anchored per-channel tone-region roll-off)
    # Region "bumps" are zero at both endpoints (0 and 1) so pure black and
    # pure white stay anchored — highlights roll off smoothly below white
    # rather than the white point itself being scaled.
    if highlights != 0.0 or shadows != 0.0:
        HS_PEAK = 0.10546875   # peak of x^3*(1-x), normalizes bumps to peak 1.0
        HS_STRENGTH = 0.30     # max channel offset at the bump peak for full slider
        x = img / 65535.0
        one_minus = 1.0 - x
        # Highlight bump peaks at x=0.75; shadow bump peaks at x=0.25
        wh = (x ** 3) * one_minus / HS_PEAK
        ws = x * (one_minus ** 3) / HS_PEAK
        x = x + (highlights / 100.0) * HS_STRENGTH * wh + (shadows / 100.0) * HS_STRENGTH * ws
        img = np.clip(x, 0.0, 1.0) * 65535.0

    # Black/White point (Adobe-like: remap input range)
    if blackpoint != 0.0 or whitepoint != 0.0:
        img_norm = img / 65535.0
        # Map [-100, 100] to [0, 0.2] for black, [1, 0.8] for white
        black_clip = np.clip(blackpoint, -100, 100) / 300.0  # -0.333 to +0.333 (matches white point scale)
        white_clip = np.clip(whitepoint, -100, 100) / 300.0  # -0.2 to +0.2
        black_val = 0.0 + black_clip
        white_val = 1.0 - white_clip
        # Piecewise linear remap
        img_norm = (img_norm - black_val) / (white_val - black_val)
        img_norm = np.clip(img_norm, 0, 1)
        img = img_norm * 65535.0
    # Contrast (continuous S-curve for both positive and negative)
    if contrast != 0.0:
        img_norm = img / 65535.0
        midpoint = 0.5
        # Map contrast [-100, 100] to k [-0.95, 0.95]
        k = np.clip(contrast / 105.0, -0.95, 0.95)
        # S-curve: compress for negative, expand for positive, fixed endpoints
        def s_curve(x, k):
            return ((1 + k) * (x - midpoint)) / (1 + k * np.abs(x - midpoint) * 2) + midpoint
        img_norm = s_curve(img_norm, k)
        img = img_norm * 65535.0

    # Mid-high tone weighted saturation adjustment
    if saturation != 0.0:
        img_norm = img / 65535.0
        # Convert RGB to grayscale using luminance weights
        gray = np.dot(img_norm[..., :3], [0.299, 0.587, 0.114])
        gray_expanded = np.expand_dims(gray, axis=-1)
        
        # Create mid-high tone weighted curve: bell curve peaked at 65% luminance
        # Using Gaussian-like curve: exp(-((luminance - 0.65) / 0.25)^2)
        mid_high_weight = np.exp(-((gray - 0.50) / 0.35) ** 2)
        
        # Create dynamic saturation factor based on mid-high tone weighting
        # Maximum effect at 65% luminance, minimal effect in deep shadows/highlights
        min_saturation_factor = 0.2  # 20% of full saturation in extremes
        saturation_curve = min_saturation_factor + (1.0 - min_saturation_factor) * mid_high_weight
        
        # Apply the mid-high tone weighted saturation scaling
        dynamic_saturation_scale = 1.0 + (saturation_scale - 1.0) * saturation_curve
        dynamic_saturation_scale = np.expand_dims(dynamic_saturation_scale, axis=-1)
        
        # Blend between grayscale and original based on mid-high tone weighted saturation
        img_norm = gray_expanded + dynamic_saturation_scale * (img_norm - gray_expanded)
        img_norm = np.clip(img_norm, 0, 1)
        img = img_norm * 65535.0

    # Subtractive (film-density) saturation: scale each pixel's chromaticity
    # ratios by a power while pinning the dominant channel, so saturation is
    # gained by absorbing light in the other channels (darker, denser colors)
    # instead of adding it. Mirrors the OpenCL kernel block exactly.
    if sub_saturation != 0.0:
        img_norm = np.clip(img / 65535.0, 0.0, 1.0)
        mx = np.max(img_norm, axis=-1, keepdims=True)
        gamma_s = 2.0 ** (sub_saturation / 100.0)
        safe_mx = np.maximum(mx, 1e-6)
        img_norm = np.where(mx > 1e-6,
                            mx * (img_norm / safe_mx) ** gamma_s,
                            img_norm)
        img = img_norm * 65535.0

    # Per-channel levels controls (linear domain, post-conversion data).
    # Runs only when at least one slider is non-zero; otherwise a no-op.
    #
    # Blackpoint and Gain are per-channel versions of the regular Black Point
    # and White Point sliders (same /300 mapping); Shift is a uniform additive
    # offset that translates the channel's histogram; Input Gain is an
    # exposure-style multiplier applied before everything else.
    #     out = ((in * input_gain + shift) - black_val) / (white_val - black_val)
    # where black_val anchors white and remaps the dark end (regular Black
    # Point behaviour) and white_val = 1 - gain anchors black and moves the
    # bright end (regular White Point behaviour).
    _ch_active = (ch_input_gain, ch_master_shift, ch_master_gain,
                  ch_r_shift, ch_r_gain, ch_r_blackpoint,
                  ch_g_shift, ch_g_gain, ch_g_blackpoint,
                  ch_b_shift, ch_b_gain, ch_b_blackpoint)
    if any(p != 0.0 for p in _ch_active):
        ig = 2.0 ** (ch_input_gain / 50.0)      # slider ±100 → ×0.25…×4 (±2 stops)
        # Master + channel combined, clamped to slider range, then /300 like
        # the regular Black/White Point sliders.
        shifts = (np.clip(ch_master_shift + ch_r_shift, -100, 100) / 300.0,
                  np.clip(ch_master_shift + ch_g_shift, -100, 100) / 300.0,
                  np.clip(ch_master_shift + ch_b_shift, -100, 100) / 300.0)
        gains  = (np.clip(ch_master_gain + ch_r_gain, -100, 100) / 300.0,
                  np.clip(ch_master_gain + ch_g_gain, -100, 100) / 300.0,
                  np.clip(ch_master_gain + ch_b_gain, -100, 100) / 300.0)
        blacks = (np.clip(ch_r_blackpoint, -100, 100) / 300.0,
                  np.clip(ch_g_blackpoint, -100, 100) / 300.0,
                  np.clip(ch_b_blackpoint, -100, 100) / 300.0)

        for c in range(3):
            # Skip neutral channels so the normalize round-trip can't introduce
            # ±1 LSB quantization drift on channels the user didn't touch.
            if ig == 1.0 and shifts[c] == 0.0 and gains[c] == 0.0 and blacks[c] == 0.0:
                continue
            ch = img[..., c] / 65535.0
            if ig != 1.0:
                ch = ch * ig
            if shifts[c] != 0.0:
                ch = ch + shifts[c]
            black_val = blacks[c]
            white_val = 1.0 - gains[c]
            if black_val != 0.0 or white_val != 1.0:
                ch = (ch - black_val) / (white_val - black_val)
            img[..., c] = np.clip(ch, 0.0, 1.0) * 65535.0

    img = np.clip(img, 0, 65535)
    return img.astype(np.uint16)

def adjust_image_opencl(
    img16: np.ndarray,
    kelvin_shift: float = 0.0,
    tint_shift: float = 0.0,
    exposure: float = 0.0,
    brightness: float = 0.0,
    blackpoint: float = 0.0,
    whitepoint: float = 0.0,
    contrast: float = 0.0,
    saturation: float = 0.0,
    tint_balance_factor: float = 1.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    ch_input_gain: float = 0.0,
    ch_master_shift: float = 0.0,
    ch_master_gain: float = 0.0,
    ch_r_shift: float = 0.0,
    ch_r_gain: float = 0.0,
    ch_r_blackpoint: float = 0.0,
    ch_g_shift: float = 0.0,
    ch_g_gain: float = 0.0,
    ch_g_blackpoint: float = 0.0,
    ch_b_shift: float = 0.0,
    ch_b_gain: float = 0.0,
    ch_b_blackpoint: float = 0.0,
    sub_saturation: float = 0.0,
) -> np.ndarray:
    """
    GPU-accelerated (OpenCL) version of adjust_image.
    Uses cached OpenCL context and compiled program for better performance.
    """
    global _opencl_cache

    # Initialize OpenCL if not already done
    if not _initialize_opencl():
        # Fallback to CPU version
        return adjust_image(img16, kelvin_shift, tint_shift, exposure, brightness,
                          blackpoint, whitepoint, contrast, saturation, tint_balance_factor,
                          highlights, shadows,
                          ch_input_gain, ch_master_shift, ch_master_gain,
                          ch_r_shift, ch_r_gain, ch_r_blackpoint,
                          ch_g_shift, ch_g_gain, ch_g_blackpoint,
                          ch_b_shift, ch_b_gain, ch_b_blackpoint,
                          sub_saturation=sub_saturation)

    try:
        # Use cached OpenCL objects
        ctx = _opencl_cache['ctx']
        queue = _opencl_cache['queue']
        kernel = _opencl_cache['kernel']
        
        img = img16.astype(np.float32)
        
        # Use the pre-calculated balance factor instead of calculating it
        balance_factor = tint_balance_factor
        
        img_flat = img.reshape(-1, 3)
        img_buf = cl_array.to_device(queue, img_flat)

        # Prepare parameters as numpy array (params[0..10] existing,
        # params[11..22] channel levels, params[23] subtractive saturation)
        params = np.array([
            kelvin_shift, tint_shift, exposure, brightness,
            blackpoint, whitepoint, contrast, saturation, balance_factor,
            highlights, shadows,
            ch_input_gain, ch_master_shift, ch_master_gain,
            ch_r_shift, ch_r_gain, ch_r_blackpoint,
            ch_g_shift, ch_g_gain, ch_g_blackpoint,
            ch_b_shift, ch_b_gain, ch_b_blackpoint,
            sub_saturation,
        ], dtype=np.float32)

        params_buf = cl_array.to_device(queue, params)

        # Execute the pre-compiled kernel
        n_pixels = img_flat.shape[0]
        kernel(queue, (n_pixels,), None, img_buf.data, params_buf.data, np.int32(n_pixels))
        
        # Get results and reshape
        result = img_buf.get().reshape(img.shape)
        return np.clip(result, 0, 65535).astype(np.uint16)
        
    except Exception as e:
        print(f"OpenCL processing failed: {e}")
        # Fallback to CPU version
        return adjust_image(img16, kelvin_shift, tint_shift, exposure, brightness,
                          blackpoint, whitepoint, contrast, saturation, tint_balance_factor,
                          highlights, shadows,
                          ch_input_gain, ch_master_shift, ch_master_gain,
                          ch_r_shift, ch_r_gain, ch_r_blackpoint,
                          ch_g_shift, ch_g_gain, ch_g_blackpoint,
                          ch_b_shift, ch_b_gain, ch_b_blackpoint,
                          sub_saturation=sub_saturation)



def cleanup_opencl():
    """
    Clean up OpenCL resources. Call this when shutting down the application.
    """
    global _opencl_cache
    
    if not OPENCL_AVAILABLE:
        return
    
    try:
        if _opencl_cache['queue'] is not None:
            _opencl_cache['queue'].finish()
        
        # Reset all cached objects
        _opencl_cache['ctx'] = None
        _opencl_cache['queue'] = None
        _opencl_cache['program'] = None
        _opencl_cache['kernel'] = None
        _opencl_cache['device_name'] = None
        
        print("OpenCL resources cleaned up")
    except Exception as e:
        print(f"Error cleaning up OpenCL resources: {e}")