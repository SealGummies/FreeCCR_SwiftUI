# Unicode Character Support and Robust Image Loading Fix

This document summarizes the changes made to fix Unicode character support and robust image loading in FreeCCR, specifically for handling file paths containing Chinese, Japanese, or other non-ASCII characters, and for handling problematic image files.

## Problem Description

The application was failing to load and export images when:

1. File paths contained Unicode characters (like Chinese or Japanese text)
2. TIFF files had encoding issues that caused OpenCV to fail with errors like:
   ```
   'original_ptr == real_mat.data' must be 'true'
   ```
3. Image export operations with Unicode paths
4. RAW file processing with Unicode paths
5. EXIF data reading from Unicode paths

## Changes Made

### 1. Core Image Processing (`src/core/ccr_image.py`)

- **Path Normalization**: Added `os.path.normpath()` to normalize Unicode file paths in the constructor
- **Multi-Method Image Reading**: Implemented cascading fallback reading methods:
  1. **OpenCV imread** (primary method)
  2. **tifffile library** (for problematic TIFF files)
  3. **PIL/Pillow** (maximum compatibility fallback)
- **EXIF Reading**: Added path normalization for EXIF data extraction
- **Optional Dependencies**: Graceful handling when tifffile or PIL are not available

Key improvements:
- Use `tifffile.imread()` for TIFF files when OpenCV fails
- Use `PIL.Image.open()` as final fallback for maximum format support
- Proper color space conversion between RGB (PIL/tifffile) and BGR (OpenCV)
- Comprehensive error handling with informative logging

### 2. Image Export (`src/core/ccr_processor.py`)

- **Safe Export Functions**: Added `safe_cv2_imwrite()` and `safe_tifffile_imwrite()` functions
- **Unicode Path Utilities**: Added `safe_unicode_path()` function for path normalization
- **Fallback Export Methods**: When direct file writing fails, use memory buffer encoding as fallback

Key functions added:
```python
def safe_unicode_path(file_path: str) -> str
def safe_cv2_imwrite(output_path: str, image: np.ndarray) -> bool
def safe_tifffile_imwrite(output_path: str, image: np.ndarray, **kwargs) -> bool
```

### 3. Backend Processing (`src/core/ccr_backend.py`)

- **Folder Loading**: Enhanced `load_images_from_folder()` with Unicode support
- **Error Handling**: Added fallback using `os.scandir()` for Windows Unicode issues
- **Path Validation**: Normalize all file paths before processing
- **Better Logging**: Added detailed logging for loading success/failure statistics
- **Graceful Failure Handling**: Skip problematic files instead of stopping entire process

Key improvements:
- Use `os.scandir()` as fallback on Windows for Unicode folder reading
- Manual pattern matching when glob fails with Unicode paths
- Normalize all paths using `os.path.normpath()`
- Track and report loading statistics (success vs failure counts)
- Continue loading even when individual files fail

### 4. User Interface (`src/ui/main_window.py`)

- **Path Validation**: Added Unicode path validation for file and folder dialogs
- **User Warnings**: Display warnings when Unicode paths might cause issues
- **Safe Path Handling**: Normalize paths before passing to backend

Functions added:
```python
def normalize_unicode_path(file_path: str) -> str
def validate_unicode_path(file_path: str) -> bool
```

### 5. Image Preview Widget (`src/widgets/image_preview.py`)

- **Export Path Validation**: Check Unicode paths before export operations
- **User Feedback**: Warn users about problematic Unicode paths
- **Safe Export**: Use normalized paths for all export operations

## Technical Solutions Implemented

### 1. Multi-Method Image Reading Strategy
```python
# Method 1: OpenCV (primary)
img = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)

# Method 2: tifffile (for problematic TIFFs)
if img is None and file_path.endswith('.tif'):
    img = tifffile.imread(file_path)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

# Method 3: PIL/Pillow (maximum compatibility)
if img is None:
    pil_img = PILImage.open(file_path)
    img = np.array(pil_img)
    # Handle color space conversion
```

### 2. Path Normalization
- Use `os.path.normpath()` and `os.path.abspath()` consistently
- Handle relative and absolute paths properly
- Ensure cross-platform compatibility

### 3. Fallback File Operations
- **OpenCV Images**: Use `cv2.imdecode()` with binary file reading when `cv2.imread()` fails
- **TIFF Files**: Use `tifffile` library for problematic TIFF files
- **File Writing**: Use memory buffer encoding when direct file writing fails
- **Directory Reading**: Use `os.scandir()` as fallback for `os.listdir()` on Windows

### 4. Error Handling and Logging
- Graceful degradation when image reading fails
- Detailed logging of which method succeeded
- User-friendly error messages
- Continue processing remaining files when individual files fail
- Loading statistics reporting

### 5. Cross-Platform Support
- Windows-specific Unicode handling using `os.scandir()`
- macOS/Linux compatibility maintained
- Proper encoding handling across platforms

## User-Facing Improvements

1. **Better Error Messages**: Users now see specific warnings about Unicode path issues
2. **Automatic Path Validation**: Paths are validated before processing
3. **Robust File Loading**: Files can still be loaded even when OpenCV fails
4. **Loading Statistics**: Users see how many files loaded successfully vs failed
5. **Export Safety**: Export operations are more robust with Unicode paths
6. **Graceful Failure**: Application continues working even when some files can't be loaded

## Example Error Handling Output

Before:
```
[ERROR] OpenCV can't read data: 'original_ptr == real_mat.data' must be 'true'
Application crashes or stops loading
```

After:
```
Successfully read TIFF using tifffile: DD581.tif
Loading complete: 34 images loaded successfully, 2 failed
Failed to load DD589.tif: Unsupported format
```

## Testing Recommendations

To test the improvements:

1. **Unicode Path Testing**:
   - Create folders with Chinese/Japanese characters: `测试文件夹`, `テストフォルダ`
   - Place image files in these folders
   - Try loading images from these folders

2. **Problematic TIFF Testing**:
   - Use TIFF files that cause OpenCV errors
   - Verify fallback to tifffile library works
   - Test large TIFF files and various TIFF encodings

3. **Export Testing**:
   - Export images to folders with Unicode names
   - Test both TIFF and JPEG export formats
   - Verify proper file naming with Unicode characters

## Known Limitations

1. Some very old or corrupted image files may still fail to load
2. Very long Unicode paths may still cause issues on some systems
3. Some file systems may not support certain Unicode characters
4. Performance may be slightly slower due to multiple reading attempts

## Dependencies

The enhanced image reading requires these optional dependencies for maximum compatibility:
- `tifffile` - For robust TIFF file reading
- `Pillow` (PIL) - For maximum image format support

If these are not available, the application will fall back to OpenCV-only reading with appropriate warnings.

## Future Improvements

1. Add Unicode support testing to the build process
2. Consider using pathlib for more robust path handling
3. Add configuration option to enable/disable Unicode warnings
4. Implement automatic path sanitization for problematic characters
5. Add progress indicators for file loading with fallback methods
6. Cache successful reading methods for better performance
