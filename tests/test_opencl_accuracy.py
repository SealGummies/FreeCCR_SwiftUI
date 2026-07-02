#!/usr/bin/env python3
"""
Test script to verify OpenCL kernel produces identical results to CPU version
and measure performance improvements.
"""

import numpy as np
import sys
import os
import time

# Add repo root AND src/ to the path (like the other test files): ccr_processor's
# own internal imports use `from core...`, so src/ must be importable — otherwise
# this file only collects when another test happens to have added src/ first.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import adjust_image, adjust_image_opencl, OPENCL_AVAILABLE, cleanup_opencl

def test_adjustment_accuracy():
    """Test that OpenCL produces identical results to CPU version."""
    
    if not OPENCL_AVAILABLE:
        print("OpenCL not available, skipping accuracy test")
        return True
    
    print("Testing OpenCL vs CPU accuracy...")
    
    # Test with multiple image sizes to show performance scaling
    image_sizes = [
        (100, 100, "Small"),      # 30K pixels - Small test image
        (512, 512, "Medium"),     # 786K pixels - Medium image
        (1920, 1080, "Large"),    # 6.2M pixels - Full HD image
    ]
    
    for width, height, size_name in image_sizes:
        print(f"\n--- Testing {size_name} Image ({width}x{height}) ---")
        
        # Create a test image with various brightness levels and colors
        test_img = np.zeros((height, width, 3), dtype=np.uint16)
        
        # Create gradients and patterns for testing
        for i in range(height):
            for j in range(width):
                # Create a color gradient
                r = int((i / height) * 65535)
                g = int((j / width) * 65535) 
                b = int(((i + j) / (height + width)) * 65535)
                test_img[i, j] = [r, g, b]
        
        print(f"Image size: {test_img.shape}, Total pixels: {width * height:,}")
        
        # Calculate tint balance factor once for this image (as would be done during loading)
        img_norm = test_img.astype(np.float32) / 65535.0
        rb_means = np.mean(img_norm[..., [0, 2]], axis=(0, 1))  # [r_mean, b_mean]
        current_rb_ratio = rb_means[0] / (rb_means[1] + 1e-8)
        tint_balance_factor = 1.0 + 0.2 * np.tanh((current_rb_ratio - 1.0) * 2)
        print(f"Calculated tint balance factor: {tint_balance_factor:.6f}")
        
        # Test only a few representative cases for larger images to save time
        if size_name == "Small":
            test_cases = [
                # (kelvin, tint, exposure, brightness, blackpoint, whitepoint, contrast, saturation)
                (0, 0, 0, 0, 0, 0, 0, 0),  # No adjustment
                (50, 0, 0, 0, 0, 0, 0, 0),  # Temperature only
                (0, 30, 0, 0, 0, 0, 0, 0),  # Tint only
                (0, 0, 20, 0, 0, 0, 0, 0),  # Exposure only
                (0, 0, 0, 25, 0, 0, 0, 0),  # Brightness only
                (0, 0, 0, 0, 10, 0, 0, 0),  # Blackpoint only
                (0, 0, 0, 0, 0, -15, 0, 0),  # Whitepoint only
                (0, 0, 0, 0, 0, 0, 40, 0),  # Contrast only
                (0, 0, 0, 0, 0, 0, 0, 30),  # Saturation only
                (25, -20, 15, 10, 5, -10, 20, 15),  # Mixed adjustments
                (-30, 40, -10, -15, -5, 20, -25, -20),  # Mixed negative
            ]
        else:
            # For larger images, test fewer cases to save time
            test_cases = [
                (0, 0, 0, 0, 0, 0, 0, 0),  # No adjustment
                (25, -20, 15, 10, 5, -10, 20, 15),  # Mixed adjustments
                (-30, 40, -10, -15, -5, 20, -25, -20),  # Mixed negative
            ]
    
        max_difference = 0
        failed_cases = []
        total_cpu_time = 0
        total_opencl_time = 0
        
        for i, params in enumerate(test_cases):
            kelvin, tint, exposure, brightness, blackpoint, whitepoint, contrast, saturation = params
            
            print(f"Test case {i+1}: kelvin={kelvin}, tint={tint}, exposure={exposure}, "
                  f"brightness={brightness}, blackpoint={blackpoint}, whitepoint={whitepoint}, "
                  f"contrast={contrast}, saturation={saturation}")
            
            # Measure CPU execution time
            cpu_start = time.perf_counter()
            cpu_result = adjust_image(test_img, kelvin, tint, exposure, brightness, 
                                    blackpoint, whitepoint, contrast, saturation, tint_balance_factor)
            cpu_end = time.perf_counter()
            cpu_time = cpu_end - cpu_start
            
            # Measure OpenCL execution time
            opencl_start = time.perf_counter()
            opencl_result = adjust_image_opencl(test_img, kelvin, tint, exposure, brightness,
                                              blackpoint, whitepoint, contrast, saturation, tint_balance_factor)
            opencl_end = time.perf_counter()
            opencl_time = opencl_end - opencl_start
            
            # Calculate speedup
            speedup = cpu_time / opencl_time if opencl_time > 0 else float('inf')
            
            total_cpu_time += cpu_time
            total_opencl_time += opencl_time
            
            # Compare results
            diff = np.abs(cpu_result.astype(np.float32) - opencl_result.astype(np.float32))
            max_diff_case = np.max(diff)
            mean_diff_case = np.mean(diff)
            
            print(f"  Max difference: {max_diff_case:.3f}")
            print(f"  Mean difference: {mean_diff_case:.6f}")
            print(f"  CPU time: {cpu_time*1000:.2f}ms")
            print(f"  OpenCL time: {opencl_time*1000:.2f}ms")
            print(f"  Speedup: {speedup:.2f}x")
            
            if max_diff_case > max_difference:
                max_difference = max_diff_case
            
            # Consider test failed if max difference > 1 (out of 65535)
            if max_diff_case > 1.0:
                failed_cases.append((i+1, params, max_diff_case))
                print(f"  ❌ FAILED - difference too large")
            else:
                print(f"  ✅ PASSED")
            
            print()
        
        # Calculate overall statistics for this image size
        overall_speedup = total_cpu_time / total_opencl_time if total_opencl_time > 0 else float('inf')
        print(f"{size_name} Image Results:")
        print(f"  Overall maximum difference: {max_difference:.3f}")
        print(f"  Total CPU time: {total_cpu_time*1000:.2f}ms")
        print(f"  Total OpenCL time: {total_opencl_time*1000:.2f}ms")
        print(f"  Overall speedup: {overall_speedup:.2f}x")
        
        if failed_cases:
            print(f"  ❌ {len(failed_cases)} test cases failed:")
            for case_num, params, diff in failed_cases:
                print(f"    Case {case_num}: {params} (max diff: {diff:.3f})")
            return False
        else:
            print(f"  ✅ All test cases passed!")
    
    return True

def test_opencl_initialization():
    """Test OpenCL initialization."""
    if not OPENCL_AVAILABLE:
        print("OpenCL not available")
        return False
    
    from core.ccr_processor import _initialize_opencl
    
    success = _initialize_opencl()
    if success:
        print("✅ OpenCL initialization successful")
        return True
    else:
        print("❌ OpenCL initialization failed")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("OpenCL Accuracy Test")
    print("=" * 60)
    
    # Test initialization first
    if not test_opencl_initialization():
        sys.exit(1)
    
    # Test accuracy
    if not test_adjustment_accuracy():
        print("\n❌ Some accuracy tests failed!")
        sys.exit(1)
    
    print("\n✅ All tests passed!")
    
    # Cleanup
    cleanup_opencl()
