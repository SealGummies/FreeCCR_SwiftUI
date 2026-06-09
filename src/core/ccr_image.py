from typing import Any, Dict, Optional
import numpy as np
import os
import rawpy
import exifread
import cv2
import logging
import time
from PySide6.QtGui import QImage, QPixmap  # or from PySide6.QtGui import QImage, QPixmap if you use PySide
#import lensfunpy  # Make sure lensfunpy is installed
from core.ccr_processor import adjust_image, adjust_image_opencl

# Import optional libraries with fallbacks
try:
    import tifffile
    TIFFFILE_AVAILABLE = True
except ImportError:
    TIFFFILE_AVAILABLE = False
    logging.warning("tifffile not available, TIFF reading may be limited")

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logging.warning("PIL/Pillow not available, some image formats may not be supported")

class CCRImage:
    def __init__(
        self,
        file_path: str,
        thumbnail: Optional[np.ndarray] = None,         # 8-bit RGB (H, W, 3), dtype=np.uint8
        resized_raw: Optional[np.ndarray] = None,       # 16-bit RGB (H, W, 3), dtype=np.uint16
        # Coordinates as 4 uint32 integers: (x1, y1, x2, y2)
        reference_frame: Optional[tuple[int, int, int, int]] = None,
        adjustment_settings: Optional[Dict[str, Any]] = None,
        rotation_angle: int = 0,
        fine_rotation_angle: int = 0, ##remember to divide by 100 to get the actual angle
        horizontal_mirrored: bool = False,
        vertical_mirrored: bool = False,
        converted: bool = False,
        ):
        # Normalize file path to handle Unicode characters properly
        self.file_path = os.path.normpath(file_path)
        self.thumbnail = thumbnail
        self.resized_raw = resized_raw
        self.reference_frame = reference_frame
        self.resized_preview = None  # Placeholder for resized preview, if needed later
        self.adjustment_settings = adjustment_settings if adjustment_settings is not None else {}
        self.rotation_angle = rotation_angle
        self.fine_rotation_angle = fine_rotation_angle
        self.horizontal_mirrored = horizontal_mirrored
        self.vertical_mirrored = vertical_mirrored
        self.converted = converted  # Indicates if the image has been converted to CCR format
        self.contrast_base: int = 0      # Non-destructive base contrast added internally; slider shows 0
        self.temperature_base: int = 0   # Non-destructive base temperature offset; slider shows 0
        self.brightness_base: int = -8   # Non-destructive base brightness offset; slider shows 0
        self.histogram_image = None

        self.info = self.get_camera_and_lens_for_lensfun(self.file_path)  # Extract camera and lens info for lensfun
        
        # Read image from file and populate resized_raw
        img = self.read_image(self.file_path)
        if img is not None:
            # Resize raw to a reasonable working size (e.g., 1080 on long side)
            self.resized_raw = self.resize_image_to_max_pixel(img, 1080)
            #correct lens distortion and vignetting if possible
            corrected = self.correct_lens_distortion_and_vignette()
            if corrected is not None:
                self.resized_raw = corrected
            else:
                logging.warning(f"Could not correct lens distortion for {self.file_path}, using original resized image.")
            
            # Calculate tint balance factor once during loading
            self.tint_balance_factor = self._calculate_tint_balance_factor()
            
            # Populate thumbnail and preview
            self.update_thumbnail_and_preview()
        else:
            raise ValueError(f"Could not read image from file: {self.file_path}")
        print(f"CCRImage initialized: {self.file_path}, info: {self.info}")


    def _calculate_tint_balance_factor(self) -> float:
        """
        Calculate the tint balance factor based on the R/B channel ratio of the original image.
        This only needs to be calculated once during image loading.
        """
        if self.resized_raw is None:
            return 1.0
        
        img_norm = self.resized_raw.astype(np.float32) / 65535.0
        # Calculate R and B channel means in one operation
        rb_means = np.mean(img_norm[..., [0, 2]], axis=(0, 1))  # [r_mean, b_mean]
        current_rb_ratio = rb_means[0] / (rb_means[1] + 1e-8)
        balance_factor = 1.0 + 0.2 * np.tanh((current_rb_ratio - 1.0) * 2)
        
        return balance_factor

    def reload_image(self) -> None:
        """
        Reload the image from the file path and update resized_raw, thumbnail, and preview.
        This is useful if the image file has been modified externally.
        """
        self.contrast_base = 0      # Clear base offsets when reverting to original scan
        self.temperature_base = 0
        self.brightness_base = -8   # Always applied; not tied to conversion state
        img = self.read_image(self.file_path)
        if img is not None:
            self.resized_raw = self.resize_image_to_max_pixel(img, 1080)
            # Recalculate tint balance factor for the new image
            self.tint_balance_factor = self._calculate_tint_balance_factor()
            self.update_thumbnail_and_preview()
        else:
            logging.error(f"Failed to reload image: {self.file_path}")

    def resize_image_to_max_pixel(self, image: np.ndarray, max_long_side: int) -> np.ndarray:
        """
        Resize the image so that its longest side is equal to max_long_side pixels,
        preserving aspect ratio. Returns the resized image.
        This function does not copy if resizing is not needed; otherwise, returns a new resized reference.
        """
        h, w = image.shape[:2]
        if max(h, w) <= max_long_side:
            return image
        if h > w:
            new_h = max_long_side
            new_w = int(w * max_long_side / h)
        else:
            new_w = max_long_side
            new_h = int(h * max_long_side / w)
        return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def read_image(self, file_path: str, preview = True) -> Optional[np.ndarray]:
        # Ensure file path is properly encoded for Unicode support
        file_path = os.path.normpath(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        
        # Treat FFF files as TIFF files
        if ext == ".fff":
            ext = ".tiff"
        
        if ext in [".cr3", ".cr2", ".nef", ".arw", ".dng", ".rw2", ".orf", ".raf", ".srw", ".pef", ".3fr"]:
            try:
                print(f"Starting RAW processing for: {os.path.basename(file_path)}")
                start_time = time.time()
                
                with rawpy.imread(file_path) as raw:
                    # Capture sensor ceiling before postprocess (e.g. 16383 for 14-bit)
                    white_level = raw.white_level

                    # Check if this is a monochrome sensor
                    is_monochrome = False
                    try:
                        # Primary check: number of colors
                        if hasattr(raw, 'num_colors') and raw.num_colors == 1:
                            is_monochrome = True
                        # Secondary check: raw pattern (with error handling)
                        elif hasattr(raw, 'raw_pattern') and hasattr(raw, 'color_desc'):
                            try:
                                if raw.color_desc == b'RGBG' and raw.raw_pattern.max() == 0:
                                    is_monochrome = True
                            except (AttributeError, ValueError):
                                pass
                        # Tertiary check: color description indicates monochrome
                        elif hasattr(raw, 'color_desc') and raw.color_desc in [b'G', b'GRAY', b'GREY']:
                            is_monochrome = True
                    except Exception as e:
                        logging.warning(f"Error detecting monochrome sensor: {e}")
                        is_monochrome = False

                    if is_monochrome:
                        print(f"Detected monochrome sensor for: {os.path.basename(file_path)}")
                        # For monochrome sensors, use different processing
                        rgb = raw.postprocess(
                            output_bps=16,
                            no_auto_bright=False,
                            gamma=(1, 1),
                            user_flip=0,
                            half_size=preview,
                            use_camera_wb=False,  # Disable WB for monochrome
                            four_color_rgb=False,
                            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR                        )
                        # Convert single channel to RGB by duplicating the channel
                        if len(rgb.shape) == 2:
                            rgb = np.stack([rgb, rgb, rgb], axis=2)
                        elif rgb.shape[2] == 1:
                            rgb = np.repeat(rgb, 3, axis=2)
                    else:
                        # Pure/raw sensor readout with minimal processing (greenish result)
                        rgb = raw.postprocess(
                            output_bps=16,
                            no_auto_bright=True,      # Consistent absolute sensor values across all frames
                            gamma=(1, 1),            # Linear gamma (no gamma correction)
                            user_flip=0,              # No rotation
                            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,  # Simple linear demosaic
                            half_size=preview,        # Process at half resolution - much faster!
                            use_camera_wb=False,      # No camera white balance
                            use_auto_wb=False,        # No auto white balance
                            output_color=rawpy.ColorSpace.raw,  # Raw color space (no color correction)
                            no_auto_scale=True,       # No automatic scaling
                            four_color_rgb=False,     # Standard 3-color processing
                        )

                    # Scale native bit depth to full 16-bit range so images display at
                    # correct brightness (e.g. 14-bit data sits in [0,16383] without this).
                    if white_level > 0 and white_level < 65535:
                        print(f"Scaling RAW from {white_level}-ceiling to 16-bit (factor {65535.0/white_level:.4f})")
                        rgb = np.clip(
                            rgb.astype(np.float32) * (65535.0 / white_level),
                            0, 65535
                        ).astype(np.uint16)
                
                elapsed_time = time.time() - start_time
                print(f"RAW processing completed in {elapsed_time:.3f} seconds")
                return rgb
            except Exception as e:
                logging.exception(f"Failed to read RAW image: {file_path}")
                return None
        else:
            # Handle Unicode file paths and OpenCV TIFF issues properly
            img = None
            
            # Try multiple reading methods for better compatibility
            try:
                # Method 1: Try OpenCV imread first (handles most formats)
                img = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)
                
            except Exception as e:
                logging.warning(f"OpenCV imread failed for {file_path}: {e}")
                img = None
            
            # Method 2: If OpenCV fails or returns None, try alternative methods
            if img is None:
                try:
                    # For TIFF files, try using tifffile library which is more robust
                    if file_path.lower().endswith(('.tif', '.tiff')) and TIFFFILE_AVAILABLE:
                        img = tifffile.imread(file_path)
                        # Convert to OpenCV format (BGR if color, grayscale if mono)
                        if len(img.shape) == 3 and img.shape[2] == 3:
                            # RGB to BGR conversion for OpenCV compatibility
                            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                        print(f"Successfully read TIFF using tifffile: {os.path.basename(file_path)}")
                    else:
                        # For other formats, try binary reading with cv2.imdecode
                        with open(file_path, 'rb') as f:
                            file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
                        img = cv2.imdecode(file_bytes, cv2.IMREAD_UNCHANGED)
                        
                except Exception as e:
                    logging.warning(f"Alternative reading method failed for {file_path}: {e}")
                    img = None
            
            # Method 3: Final fallback - try PIL/Pillow for maximum compatibility
            if img is None and PIL_AVAILABLE:
                try:
                    pil_img = PILImage.open(file_path)
                    # Convert PIL image to numpy array
                    img_array = np.array(pil_img)
                    
                    # Handle different PIL modes
                    if pil_img.mode == 'RGB':
                        # PIL uses RGB, OpenCV uses BGR
                        img = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                    elif pil_img.mode == 'RGBA':
                        img = cv2.cvtColor(img_array, cv2.COLOR_RGBA2BGRA)
                    elif pil_img.mode == 'L':
                        img = img_array
                    elif pil_img.mode == 'P':
                        # Convert palette mode to RGB first
                        pil_img = pil_img.convert('RGB')
                        img_array = np.array(pil_img)
                        img = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                    else:
                        img = img_array
                    
                    print(f"Successfully read using PIL: {os.path.basename(file_path)}")
                    
                except Exception as e:
                    logging.error(f"PIL reading also failed for {file_path}: {e}")
                    img = None
            
            if img is None:
                logging.error(f"All reading methods failed for: {file_path}")
                return None
            # Convert grayscale to RGB
            if len(img.shape) == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
            elif img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            # Convert to 16-bit if needed
            if img.dtype != np.uint16:
                img = img.astype(np.uint16) * 257 if img.dtype == np.uint8 else img
            return img
        
    def update_thumbnail_and_preview(self, thumbnail_size: int = 156, preview_size: int = 1080) -> None:
        """
        Populates/updates the thumbnail and resized_preview attributes using the 16-bit resized_raw image.
        Both outputs are 8-bit RGB np.ndarray.
        Applies adjustments before resizing.
        """
        if self.resized_raw is None:
            return

        def to_8bit(img16: np.ndarray) -> np.ndarray:
            # Clip to 16-bit range, then scale to 8-bit
            img16 = np.clip(img16, 0, 65535)
            img8 = (img16 / 257).astype(np.uint8)
            return img8

        # Apply adjustments first
        adjusted_img = self.apply_adjustments(self.resized_raw)

        # Create thumbnail
        thumb_img = self.resize_image_to_max_pixel(adjusted_img, thumbnail_size)
        thumb_img_8 = to_8bit(thumb_img)
        qimage = self.generate_qimage_from_np_array_8(thumb_img_8)
        self.thumbnail = QPixmap.fromImage(qimage)

        # Create preview
        preview_img = self.resize_image_to_max_pixel(adjusted_img, preview_size)
        qimage = self.generate_qimage_from_np_array_8(to_8bit(preview_img))
        self.resized_preview = QPixmap.fromImage(qimage)
        # Calculate histogram for the 8-bit thumbnail (RGB)
        hist = {}
        preview_img_8bit = to_8bit(preview_img)
        for i, color in enumerate(['r', 'g', 'b']):
            hist[color] = cv2.calcHist([preview_img_8bit], [i], None, [256], [0, 256]).flatten()

        # Generate a histogram image (RGB channels overlaid)
        bg_color = np.array([180, 180, 180], dtype=np.uint8)  # dark gray background
        hist_height = 150
        hist_width = 256
        hist_img = np.full((hist_height, hist_width, 3), bg_color, dtype=np.uint8)

        alpha = 0.33  # transparency for histogram lines

        # Prepare a mask to track where all three channels overlap
        overlap_mask = np.zeros((hist_height, hist_width), dtype=np.uint8)

        # Find the global max value across all channels for adaptive scaling
        max_val = max([hist[c].mean() for c in hist]) * 6
        min_scale = 0.1  # Prevent division by zero and avoid too flat lines

        # Draw each channel and accumulate overlap
        for i, color in enumerate([(0, 0, 255), (0, 255, 0), (255, 0, 0)]):  # BGR for OpenCV
            channel = list(hist.keys())[i]
            scale = max(max_val, min_scale)
            h_scaled = (hist[channel] / scale) * (hist_height - 1)
            h_scaled = np.clip(h_scaled, 0, hist_height - 1)
            for x in range(hist_width):
                y1 = hist_height
                y2 = hist_height - int(h_scaled[x])
                overlay = hist_img.copy()
                cv2.line(overlay, (x, y1), (x, y2), color, 1)
                hist_img = cv2.addWeighted(overlay, alpha, hist_img, 1 - alpha, 0)
                # Mark the mask for overlap detection
                for y in range(y2, y1):
                    overlap_mask[y, x] += 1

        # Where all three channels overlap, set to white
        white = np.array([235, 235, 235], dtype=np.uint8)
        hist_img[overlap_mask == 3] = white

        self.histogram_image = QPixmap.fromImage(self.generate_qimage_from_np_array_8(hist_img))

    def generate_qimage_from_np_array_8(self, thumb_img_8):
        h, w, ch = thumb_img_8.shape
        bytes_per_line = ch * w
        qimage = QImage(
            thumb_img_8.data, w, h, bytes_per_line, QImage.Format_RGB888
        )
        return qimage

    def apply_adjustments(self, image: np.ndarray) -> np.ndarray:
        if not self.adjustment_settings and self.contrast_base == 0 and self.temperature_base == 0 and self.brightness_base == 0:
            return image
        s = self.adjustment_settings
        adjusted = adjust_image_opencl(image,
                     s.get('temperature', 0) + self.temperature_base,
                     s.get('tint', 0),
                     s.get('exposure', 0),
                     s.get('brightness', 0) + self.brightness_base,
                     s.get('black_point', 0),
                     s.get('white_point', 0),
                     s.get('contrast', 0) + self.contrast_base,
                     s.get('saturation', 0),
                     self.tint_balance_factor)
        return adjusted

    def __repr__(self):
        return (
            f"CCRImage(file_path={self.file_path!r}, "
            f"thumbnail={'set' if self.thumbnail is not None else 'None'}, "
            f"resized_raw={'set' if self.resized_raw is not None else 'None'}, "
            f"resized_preview={'set' if self.resized_preview is not None else 'None'}, "
            f"reference_frame={self.reference_frame!r}, "
            f"adjustment_settings={self.adjustment_settings!r}, "
            f"tint_balance_factor={getattr(self, 'tint_balance_factor', 1.0):.6f}, "
            f"rotation_angle={self.rotation_angle}, "
            f"fine_rotation_angle={self.fine_rotation_angle}, "
            f"horizontal_mirrored={self.horizontal_mirrored}, "
            f"vertical_mirrored={self.vertical_mirrored})"
        )
    
    @staticmethod
    def get_camera_and_lens_for_lensfun(raw_path: str) -> Dict[str, Optional[Any]]:
        """
        Extract camera and lens info from a raw file and parse for lensfun.

        Args:
            raw_path (str): Path to the raw image file.

        Returns:
            dict: Dictionary with keys suitable for lensfunpy:
                - camera_make
                - camera_model
                - lens_make
                - lens_model
                - focal_length (float)
                - aperture (float)
                - distance (float, meters)
        """
        info: Dict[str, Optional[Any]] = {}
        try:
            # Normalize path to handle Unicode characters
            raw_path = os.path.normpath(raw_path)
            with open(raw_path, 'rb') as f:
                tags = exifread.process_file(f, details=False)
                info['camera_make'] = str(tags.get('Image Make', '')).strip()
                info['camera_model'] = str(tags.get('Image Model', '')).strip()
                info['lens_make'] = str(tags.get('EXIF LensMake', '')).strip()
                info['lens_model'] = str(tags.get('EXIF LensModel', '')).strip()
                if len(info['lens_make']) == 0:
                    lens_model = info['lens_model'].upper()
                    if "DG DN" in lens_model or "DC DN" in lens_model:
                        info['lens_make'] = "Sigma"
                # FocalLength and FNumber may be Ratio objects, convert to float
                focal = tags.get('EXIF FocalLength')
                if focal:
                    try:
                        val = focal.values[0]
                        if hasattr(val, 'num') and hasattr(val, 'den') and val.den != 0:
                            info['focal_length'] = float(val.num) / float(val.den)
                        else:
                            info['focal_length'] = float(val)
                    except Exception as e:
                        logging.warning(f"Error parsing focal length from EXIF: {e}")
                        try:
                            info['focal_length'] = float(str(focal))
                        except Exception as e2:
                            logging.warning(f"Error converting focal length to float: {e2}")
                            info['focal_length'] = None
                else:
                    info['focal_length'] = None
                fnum = tags.get('EXIF FNumber')
                if fnum:
                    try:
                        val = fnum.values[0]
                        if hasattr(val, 'num') and hasattr(val, 'den') and val.den != 0:
                            info['aperture'] = float(val.num) / float(val.den)
                        else:
                            info['aperture'] = float(val)
                    except Exception as e:
                        logging.warning(f"Error parsing aperture from EXIF: {e}")
                        try:
                            info['aperture'] = float(str(fnum))
                        except Exception as e2:
                            logging.warning(f"Error converting aperture to float: {e2}")
                            info['aperture'] = None
                else:
                    info['aperture'] = None
                # Fetch focus distance (in meters)
                dist = tags.get('EXIF SubjectDistance')
                if dist:
                    try:
                        val = dist.values[0]
                        if hasattr(val, 'num') and hasattr(val, 'den') and val.den != 0:
                            info['distance'] = float(val.num) / float(val.den)
                        else:
                            info['distance'] = float(val)
                    except Exception as e:
                        logging.warning(f"Error parsing subject distance from EXIF: {e}")
                        try:
                            info['distance'] = float(str(dist))
                        except Exception as e2:
                            logging.warning(f"Error converting subject distance to float: {e2}")
                            info['distance'] = None
                else:
                    info['distance'] = None
        except Exception as e:
            logging.error(f"Failed to extract EXIF info from {raw_path}: {e}")
            info = {
                'camera_make': None,
                'camera_model': None,
                'lens_make': None,
                'lens_model': None,
                'focal_length': None,
                'aperture': None,
                'distance': None
            }
        return info

    def correct_lens_distortion_and_vignette(self) -> Optional[np.ndarray]:
        """
        Correct lens distortion and vignetting on self.resized_raw (16-bit RGB) using lensfunpy,
        preserving 16-bit data by remapping with OpenCV.
        Returns a new np.ndarray with corrections applied, or raises an exception if correction is not possible.
        """
        return self.resized_raw
        # if self.resized_raw is None or not self.info:
        #     return None  # No image or no lens/camera info available
        # try:
        #     print("here 0")
        #     db = lensfunpy.Database()
        #     cam = db.find_cameras(
        #         self.info.get('camera_make', ''),
        #         self.info.get('camera_model', '')
        #     )[0]
        #     print(cam)
        #     lens = db.find_lenses(cam, self.info.get('lens_make', ''), self.info.get('lens_model', ''))[0]
        #     print(lens)
        # except Exception as e:
        #     logging.error(f"Failed to access lensfun database: {e}")
        #     return None

        # return self.resized_raw

