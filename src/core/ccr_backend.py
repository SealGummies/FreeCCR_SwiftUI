from typing import List, Optional
import cv2
from core.ccr_image import CCRImage
from core.ccr_processor import (ccr_normalize_with_reference, ccr_normalize_with_bwpoint,
                                ccr_normalize_with_refparams, auto_fine_angle, auto_frame,
                                auto_frame_v2)
import os
import glob
import concurrent.futures
import time

class CCRBackend:
    _instance = None
    software_activated = False  # Flag to indicate if the backend is activated

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CCRBackend, cls).__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.images: List[CCRImage] = []
        self.file_paths: List[str] = []
        self.white_point_bgr = None  # (B, G, R) of dense/exposed area
        self.black_point_bgr = None  # (B, G, R) of transparent/clear area

    def load_images_from_files(self, file_paths: List[str], cancel_flag=None):
        self.images.clear()
        self.file_paths = file_paths
        
        def load_single_image(path):
            try:
                if cancel_flag and cancel_flag():
                    return path, None
                print(f"Loading image: {os.path.basename(path)}")
                # Restores cataloged state (slices, conversion, adjustments)
                # when this file was processed before; plain load otherwise.
                from core.catalog import create_images_for_path
                imgs = create_images_for_path(path)
                for order, img in enumerate(imgs):
                    img._catalog_order = order  # keep slice order within a file
                print(f"Successfully loaded: {os.path.basename(path)}")
                return path, imgs
            except Exception as e:
                print(f"Failed to load {os.path.basename(path)}: {e}")
                return path, None
        
        # Use parallel loading with ThreadPoolExecutor
        max_workers = min(8, os.cpu_count() or 1)
        
        if max_workers == 1:
            # Sequential fallback
            for path in file_paths:
                if cancel_flag and cancel_flag():
                    break
                _path, imgs = load_single_image(path)
                if imgs:
                    self.images.extend(imgs)
        else:
            # Parallel loading - collect into local list to avoid concurrent modification
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_path = {executor.submit(load_single_image, path): path for path in file_paths}

                for future in concurrent.futures.as_completed(future_to_path):
                    if cancel_flag and cancel_flag():
                        break
                    path, imgs = future.result()
                    if imgs:
                        results.extend(imgs)

            # Slices of one file keep their catalog order within the file
            results.sort(key=lambda img: (os.path.basename(img.file_path),
                                          getattr(img, "_catalog_order", 0)))
            self.images = results

        # Keep file_paths derived from actually-loaded images so the two lists stay in sync
        self.file_paths = [img.file_path for img in self.images]

    def clear(self):
        """
        Clear all images and file paths from the backend.
        This is useful for resetting the state of the backend.
        """
        self.images.clear()
        self.file_paths.clear()

    def load_images_from_folder(self, folder_path: str, extensions: Optional[List[str]] = None, cancel_flag=None):
        if extensions is None:
            extensions = [
                "*.dng", "*.tif", "*.tiff", "*.arw", "*.nef", "*.cr2", "*.cr3",
                "*.raf", "*.png", "*.jpg", "*.jpeg", "*.rw2", "*.3fr", "*.sr2", "*.orf", "*.pef", "*.heic", "*.heif",
                "*.fff", "*.dcr", "*.kdc", "*.x3f", "*.srw", "*.erf", "*.nrw", "*.ptx", "*.r3d", "*.raf"
            ]
        
        # Normalize folder path to handle Unicode characters
        folder_path = os.path.normpath(folder_path)
        file_paths = []
        print(f"Loading from folder: {folder_path}")
        print(f"Folder exists: {os.path.exists(folder_path)}")
        
        # Check what files are actually in the folder
        if os.path.exists(folder_path):
            try:
                all_files = os.listdir(folder_path)
                print(f"All files in folder: {all_files[:10]}...")  # Show first 10 files
            except UnicodeDecodeError as e:
                print(f"Unicode error reading folder contents: {e}")
                # Try with different encoding on Windows
                try:
                    import sys
                    if sys.platform == "win32":
                        # Use os.scandir for better Unicode support on Windows
                        all_files = [entry.name for entry in os.scandir(folder_path)]
                        print(f"All files in folder (via scandir): {all_files[:10]}...")
                    else:
                        raise e
                except Exception as e2:
                    print(f"Failed to read folder contents: {e2}")
                    return
        
        # Try both lowercase and uppercase extensions
        for ext in extensions:
            try:
                # Original extension
                pattern1 = os.path.join(folder_path, ext)
                matches1 = glob.glob(pattern1)
                file_paths.extend(matches1)
                
                # Uppercase extension
                pattern2 = os.path.join(folder_path, ext.upper())
                matches2 = glob.glob(pattern2)
                file_paths.extend(matches2)
                
                print(f"Pattern {ext}: {len(matches1)} files, {ext.upper()}: {len(matches2)} files")
            except Exception as e:
                print(f"Error processing pattern {ext}: {e}")
                # Fallback: manually check files
                try:
                    if 'all_files' in locals():
                        ext_no_star = ext[2:] if ext.startswith('*.') else ext  # Remove '*.'
                        matching_files = [f for f in all_files if f.lower().endswith(ext_no_star.lower())]
                        for f in matching_files:
                            full_path = os.path.join(folder_path, f)
                            if os.path.isfile(full_path):
                                file_paths.append(full_path)
                        print(f"Manual pattern {ext}: {len(matching_files)} files")
                except Exception as e2:
                    print(f"Fallback pattern matching failed for {ext}: {e2}")
        
        # Remove duplicates and normalize paths
        file_paths = list(set(os.path.normpath(path) for path in file_paths))
        
        print(f"Found {len(file_paths)} files total: {file_paths[:5]}...")  # Show first 5 files
        
        # Load images and track success/failure
        initial_count = len(self.images)
        self.load_images_from_files(sorted(file_paths), cancel_flag=cancel_flag)
        loaded_count = len(self.images) - initial_count
        failed_count = len(file_paths) - loaded_count
        
        print(f"Loading complete: {loaded_count} images loaded successfully, {failed_count} failed")

    def get_image_by_index(self, idx: int) -> Optional[CCRImage]:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx]
        return None
    
    def set_reference_frame_by_index(self, idx: int, reference_frame: Optional[tuple[int, int, int, int]]):
        if idx is not None and 0 <= idx < len(self.images):
            self.images[idx].reference_frame = reference_frame

    def get_reference_frame_by_index(self, idx: int) -> Optional[tuple[int, int, int, int]]:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx].reference_frame
        return None

    def get_preview_w_ref_frame_by_index(self, idx: int) -> Optional[CCRImage]:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx]
        return None

    def get_image_horizontal_flip_by_index(self, idx: int) -> bool:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx].horizontal_mirrored
        return False
    def set_image_horizontal_flip_by_index(self, idx: int, flip: bool):
        if idx is not None and 0 <= idx < len(self.images):
            self.images[idx].horizontal_mirrored = flip

    def get_image_vertical_flip_by_index(self, idx: int) -> bool:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx].vertical_mirrored
        return False
    
    def set_image_vertical_flip_by_index(self, idx: int, flip: bool):
        if idx is not None and 0 <= idx < len(self.images):
            self.images[idx].vertical_mirrored = flip
    
    def get_image_fine_rotation_by_index(self, idx: int) -> float:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx].fine_rotation_angle
        return 0.0
    
    def set_image_fine_rotation_by_index(self, idx: int, angle: float):
        if idx is not None and 0 <= idx < len(self.images):
            self.images[idx].fine_rotation_angle = angle
    
    def get_image_rotation_by_index(self, idx: int) -> float:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx].rotation_angle
        return 0.0
    
    def set_image_rotation_by_index(self, idx: int, angle: int):
        if idx is not None and 0 <= idx < len(self.images):
            self.images[idx].rotation_angle = angle
    
    def get_converted_state_by_index(self, idx: int) -> bool:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx].converted
        return False

    def get_thumbnail_by_index(self, idx: int) -> Optional[CCRImage]:
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx].thumbnail
        return None
    
    def get_preview_by_index(self, idx: int) -> Optional[CCRImage]:
        if idx is not None and 0 <= idx < len(self.images):
            image = self.images[idx]
            preview = image.resized_preview.copy() if image.resized_preview is not None else None
            return preview
        return None

    def get_histogram_image_by_index(self, idx: int) -> Optional[CCRImage]:
        if idx is not None and 0 <= idx < len(self.images):
            image = self.images[idx]
            histogram_image = image.histogram_image if image.histogram_image is not None else None
            return histogram_image
        return None
    
    def set_adjustment_by_index(self, idx: int, adjustment: dict):
        """
        Sets the adjustment parameters for the image at the given index.
        The adjustment dictionary can contain keys like 'fine_rotation_angle', 'rotation_angle',
        'horizontal_mirrored', 'vertical_mirrored', etc.
        """
        if idx is not None and 0 <= idx < len(self.images):
            self.images[idx].adjustment_settings = adjustment
            self.images[idx].update_thumbnail_and_preview()

    def get_adjustment_by_index(self, idx: int) -> Optional[dict]:
        """
        Returns the adjustment parameters for the image at the given index.
        The returned dictionary can contain keys like 'fine_rotation_angle', 'rotation_angle',
        'horizontal_mirrored', 'vertical_mirrored', etc.
        """
        if idx is not None and 0 <= idx < len(self.images):
            return self.images[idx].adjustment_settings
        return None       

    def apply_adjustment_by_index(self, idx: int):
        """
        Applies the adjustment settings to the image at the given index.
        This method will update the resized_raw and thumbnail of the image.
        """
        if idx is not None and 0 <= idx < len(self.images):
            image_obj = self.images[idx]
            image_obj.update_thumbnail_and_preview()

    def get_image_count(self) -> int:
        return len(self.images)
    
    def get_total_file_paths(self) -> int:
        return len(self.file_paths)

    def get_image_by_path(self, file_path: str) -> Optional[CCRImage]:
        for img in self.images:
            if img.file_path == file_path:
                return img
        return None

    def get_all_file_paths(self) -> List[str]:
        return self.file_paths
    
    def unconvert_negative_by_index(self, idx: int):
        """
        Unconverts the negative image at the given index by resetting the converted state
        and reloading the original image.
        """
        if idx is not None and 0 <= idx < len(self.images):
            image_obj = self.images[idx]
            if image_obj.converted:
                # Reset conversion state
                image_obj.converted = False
                # Reload the original image
                image_obj.reload_image()
                # Update thumbnail and preview
                image_obj.update_thumbnail_and_preview()
            else:
                print(f"Image at index {idx} is not converted.")

    def convert_negative_by_index(self, idx: int):
        """
        Converts the negative image at the given index using CCR normalization with reference.
        Updates the resized_raw in the CCRImage object in-place.
        """
        if idx is not None and 0 <= idx < len(self.images):
            image_obj = self.images[idx]
            if image_obj.converted:
                #reload the image if it has already been converted
                image_obj.reload_image()
            try:
                processed = ccr_normalize_with_reference(image_obj,water_mark=not self.software_activated)
                image_obj.resized_raw = processed
                image_obj.converted = True
                # Snapshot what this conversion was baked with (the zoom
                # hi-res replay must use these, not later edited values)
                image_obj.conversion_inputs = {
                    "mode": "ref",
                    "ref": tuple(image_obj.reference_frame),
                    "fine_rot": image_obj.fine_rotation_angle,
                }
                image_obj.update_thumbnail_and_preview()
            except Exception as e:
                print(f"Failed to convert image at index {idx}: {e}")

    def update_thumbnail_by_index(self, idx: int):
        """
        Updates the thumbnail for the image at the given index.
        This method is useful when the image has been modified and needs a new thumbnail.
        """
        if idx is not None and 0 <= idx < len(self.images):
            image_obj = self.images[idx]
            image_obj.update_thumbnail_and_preview()

    def auto_frame_all_images(self, progress_callback=None):
        """
        Automatically sets the reference frame for all images based on their content using parallel processing.
        """
        # Filter images that need processing
        images_to_process = [(idx, img) for idx, img in enumerate(self.images) if not img.converted]
        
        if not images_to_process:
            return
        
        total_images = len(images_to_process)
        completed_count = 0
        
        if progress_callback:
            progress_callback(0, total_images)
        
        def process_single_image(idx_img_tuple):
            idx, img = idx_img_tuple
            try:
                if img.fine_rotation_angle == 0 and img.reference_frame is None:              
                    img.fine_rotation_angle = int(auto_fine_angle(img.resized_raw, debug=False) * 100)  # Store as integer for fine rotation
                if img.reference_frame is None:
                    img.reference_frame = auto_frame_v2(img.resized_raw, img.fine_rotation_angle, debug=False)
                    self.convert_negative_by_index(idx)
                img.update_thumbnail_and_preview()
                return idx, True, None
            except Exception as e:
                return idx, False, str(e)
        
        # Use parallel processing
        max_workers = min(8, os.cpu_count() or 1)
        
        if max_workers == 1:
            # Sequential fallback
            for idx, img in images_to_process:
                try:
                    if img.fine_rotation_angle == 0 and img.reference_frame is None:              
                        img.fine_rotation_angle = int(auto_fine_angle(img.resized_raw, debug=False) * 100)  # Store as integer for fine rotation
                    if img.reference_frame is None:
                        img.reference_frame = auto_frame_v2(img.resized_raw, img.fine_rotation_angle, debug=False)
                        self.convert_negative_by_index(idx)
                    img.update_thumbnail_and_preview()
                except Exception as e:
                    print(f"Failed to auto-frame image at index {idx}: {e}")
                completed_count += 1
                if progress_callback:
                    progress_callback(completed_count, total_images)
        else:
            # Parallel processing
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_idx = {executor.submit(process_single_image, (idx, img)): idx 
                               for idx, img in images_to_process}
                
                for future in concurrent.futures.as_completed(future_to_idx):
                    idx, success, error = future.result()
                    if not success:
                        print(f"Failed to auto-frame image at index {idx}: {error}")
                    completed_count += 1
                    if progress_callback:
                        progress_callback(completed_count, total_images)

    def convert_all_images(self):
        """
        Converts all images in the backend using CCR normalization with reference.
        Updates the resized_raw in each CCRImage object in-place.
        """
        for idx, img in enumerate(self.images):
            if img.converted:
                continue
            try:
                self.convert_negative_by_index(idx)
            except Exception as e:
                print(f"Failed to convert image at index {idx}: {e}")

    def export_image_by_index(self, idx: int, output_path: str, jpg_output: bool = False,
                              jpg_quality: int = 95, max_long_side: int = None) -> bool:
        """
        Exports the processed image at the given index to the specified output path.
        Routes to bwpoint or reference-frame pipeline depending on how the image was converted.
        Returns True on success.
        """
        if idx is not None and 0 <= idx < len(self.images):
            image_obj = self.images[idx]
            try:
                ci = getattr(image_obj, "conversion_inputs", None)
                if ci is not None and ci.get("mode") == "ref_params":
                    # Sliced child of a reference-converted parent: replay the
                    # stored conversion constants at full resolution.
                    ccr_normalize_with_refparams(image_obj, ci["p_lo"], ci["p_hi"], ci["od"],
                                                 output_path=output_path,
                                                 water_mark=not self.software_activated,
                                                 jpg_out=jpg_output, jpg_quality=jpg_quality,
                                                 max_long_side=max_long_side)
                elif ci is not None and ci.get("mode") == "bw":
                    # Use the anchors BAKED at convert time — resampling the
                    # global points later must not change this image's export.
                    black_point, white_point = ci["bw"]
                    ccr_normalize_with_bwpoint(image_obj, black_point, white_point,
                                               output_path=output_path,
                                               water_mark=not self.software_activated,
                                               jpg_out=jpg_output, jpg_quality=jpg_quality,
                                               max_long_side=max_long_side)
                elif image_obj.reference_frame is None and self.black_point_bgr is not None and self.white_point_bgr is not None:
                    # Legacy/un-snapshotted B/W point conversion — global anchors
                    ccr_normalize_with_bwpoint(image_obj, self.black_point_bgr, self.white_point_bgr,
                                               output_path=output_path, water_mark=not self.software_activated,
                                               jpg_out=jpg_output, jpg_quality=jpg_quality,
                                               max_long_side=max_long_side)
                else:
                    ccr_normalize_with_reference(image_obj, output_path=output_path,
                                                 water_mark=not self.software_activated, jpg_out=jpg_output,
                                                 jpg_quality=jpg_quality, max_long_side=max_long_side)
                return True
            except Exception as e:
                print(f"Failed to export image at index {idx}: {e}")
        return False

    def export_items(self, items, jpg_output: bool = False, jpg_quality: int = 95,
                     max_long_side: int = None, progress_callback=None, cancel_check=None) -> dict:
        """
        Exports specific images to explicit output paths using parallel processing.

        Args:
            items: list of (idx, absolute_output_path) tuples; filename building is
                   owned by the caller
            progress_callback: optional callable(current, total)
            cancel_check: optional zero-arg callable; once it returns True, queued
                          exports are abandoned (in-flight ones finish)

        Returns:
            dict with "exported", "failed", "cancelled" counts/flag and
            "failures" as a list of (idx, message) tuples.
        """
        import time
        total_start_time = time.time()

        result = {"exported": 0, "failed": 0, "cancelled": False, "failures": []}
        total_items = len(items)
        if total_items == 0:
            return result

        print(f"Starting export of {total_items} images...")

        def cancelled():
            return cancel_check is not None and cancel_check()

        def export_single_image(idx, output_path):
            if cancelled():
                return idx, None, None  # None success = not attempted
            start_time = time.time()
            success = self.export_image_by_index(idx, output_path, jpg_output=jpg_output,
                                                 jpg_quality=jpg_quality, max_long_side=max_long_side)
            elapsed = time.time() - start_time
            base_name = os.path.basename(output_path)
            if success:
                print(f"Export completed for {base_name} in {elapsed:.2f}s")
                return idx, True, None
            print(f"Export failed for image {idx} after {elapsed:.2f}s")
            return idx, False, "export failed (see log)"

        # Use parallel processing for exports
        max_workers = min(4, os.cpu_count() or 1)  # Use very few workers for export to avoid I/O contention
        completed_count = 0

        if progress_callback:
            progress_callback(0, total_items)

        if max_workers == 1:
            # Sequential fallback
            for idx, output_path in items:
                if cancelled():
                    result["cancelled"] = True
                    break
                idx, success, error = export_single_image(idx, output_path)
                if success:
                    result["exported"] += 1
                else:
                    result["failed"] += 1
                    result["failures"].append((idx, error))
                completed_count += 1
                if progress_callback:
                    progress_callback(completed_count, total_items)
        else:
            # Parallel export
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_idx = {executor.submit(export_single_image, idx, path): idx
                                 for idx, path in items}

                for future in concurrent.futures.as_completed(future_to_idx):
                    try:
                        idx, success, error = future.result()
                    except concurrent.futures.CancelledError:
                        result["cancelled"] = True
                        continue
                    if success is None:
                        result["cancelled"] = True
                    elif success:
                        result["exported"] += 1
                    else:
                        result["failed"] += 1
                        result["failures"].append((idx, error))
                    completed_count += 1
                    if progress_callback:
                        progress_callback(completed_count, total_items)
                    if cancelled() and not result["cancelled"]:
                        result["cancelled"] = True
                        # Cancel anything not yet started; in-flight exports finish
                        for f in future_to_idx:
                            f.cancel()

        total_elapsed = time.time() - total_start_time
        print(f"Export finished in {total_elapsed:.2f}s: {result['exported']} exported, "
              f"{result['failed']} failed{', cancelled' if result['cancelled'] else ''}")
        return result
    
    def remove_image_by_index(self, idx: int):
        """
        Remove the image and its file path at the given index.
        """
        if idx is not None and 0 <= idx < len(self.images):
            del self.images[idx]
            self.file_paths = [img.file_path for img in self.images]

    def save_catalog(self):
        """Persist the edit state (conversion, slices, crop, adjustments) of
        all loaded images so reopening the files restores it. Cheap; called
        after significant operations and on app close."""
        if not self.images:
            return
        try:
            from core.catalog import update_for_images
            update_for_images(self.images)
        except Exception as e:
            print(f"Catalog save failed: {e}")

    @staticmethod
    def _clean_slice_cuts(cuts) -> list:
        """Sorted cut fractions with 0/1 boundaries; drops cuts within 1% of
        an edge or of each other."""
        bounds = [0.0]
        for value in sorted(c for c in cuts if 0.01 <= c <= 0.99):
            if value - bounds[-1] >= 0.01:
                bounds.append(value)
        bounds.append(1.0)
        return bounds

    def slice_image_by_index(self, idx: int, x_cuts, y_cuts, progress_callback=None) -> int:
        """
        Split the image at idx into a grid of separate images along the given
        cut positions (fractions of the image's displayed frame; x_cuts are
        vertical lines, y_cuts horizontal). The slices replace the original
        in the list, in reading order (left-to-right, top-to-bottom).

        The parent's edits carry over: its fine rotation is BAKED into the
        slices (cuts are made on the rotated frame, exactly as displayed),
        its conversion is replayed on each slice with the parent's own
        constants so colors match, and adjustments/bases/orientation are
        inherited. Each slice's source_ops chain maps back to the ORIGINAL
        file, so zoom detail and full-res export read the correct region at
        full quality. The source is decoded only once.
        Returns the number of slices created (0 = nothing done).
        """
        img_obj = self.get_image_by_index(idx)
        if img_obj is None:
            return 0
        xs = self._clean_slice_cuts(x_cuts)
        ys = self._clean_slice_cuts(y_cuts)
        total = (len(xs) - 1) * (len(ys) - 1)
        if total <= 1:
            return 0

        # One shared decode (the parent's own slice chain is applied inside)
        full = img_obj.read_image(img_obj.file_path, preview=True)
        if full is None:
            return 0

        # Conversion replay: derive the parent's conversion constants so each
        # slice can be converted to look exactly like the parent did.
        parent_ci = img_obj.conversion_inputs if img_obj.converted else None
        norm_params = None
        if parent_ci is not None and parent_ci.get("mode") == "ref":
            from core.ccr_processor import compute_reference_norm_params
            ref_small = img_obj.resize_image_to_max_pixel(full, 1080)
            p_lo, p_hi, od = compute_reference_norm_params(
                ref_small, parent_ci["ref"], parent_ci["fine_rot"])
            norm_params = (tuple(float(v) for v in p_lo),
                           tuple(float(v) for v in p_hi),
                           tuple(float(v) for v in od))
        elif parent_ci is not None and parent_ci.get("mode") == "ref_params":
            norm_params = (parent_ci["p_lo"], parent_ci["p_hi"], parent_ci["od"])

        # Bake the parent's current fine rotation: the cuts were placed on
        # the rotated display, so the slices are cut from the rotated frame.
        baked_rotation = img_obj.fine_rotation_angle or 0
        if baked_rotation:
            h0, w0 = full.shape[:2]
            matrix = cv2.getRotationMatrix2D((w0 // 2, h0 // 2),
                                             -baked_rotation / 100.0, 1.0)
            full = cv2.warpAffine(full, matrix, (w0, h0), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=0)

        h, w = full.shape[:2]
        parent_full = img_obj.original_full_size or (h, w)
        # Nested slices must extend the PARENT's name (scan_s2 -> scan_s2_s1):
        # deriving from the file basename would make cousins collide and
        # exports could silently overwrite each other.
        stem, ext = os.path.splitext(img_obj.display_name
                                     or os.path.basename(img_obj.file_path))

        children = []
        if progress_callback:
            progress_callback(0, total)
        for yi in range(len(ys) - 1):
            for xi in range(len(xs) - 1):
                fx1, fx2 = xs[xi], xs[xi + 1]
                fy1, fy2 = ys[yi], ys[yi + 1]
                cx1 = max(0, min(w - 1, int(round(fx1 * w))))
                cy1 = max(0, min(h - 1, int(round(fy1 * h))))
                cx2 = max(cx1 + 1, min(w, int(round(fx2 * w))))
                cy2 = max(cy1 + 1, min(h, int(round(fy2 * h))))
                crop = full[cy1:cy2, cx1:cx2]
                child_full = (max(1, int(round((fy2 - fy1) * parent_full[0]))),
                              max(1, int(round((fx2 - fx1) * parent_full[1]))))

                # Replay the parent's conversion on this slice
                child_ci = None
                if norm_params is not None:
                    from core.ccr_processor import apply_reference_normalization
                    crop = apply_reference_normalization(crop, *norm_params)
                    child_ci = {"mode": "ref_params", "p_lo": norm_params[0],
                                "p_hi": norm_params[1], "od": norm_params[2]}
                elif parent_ci is not None and parent_ci.get("mode") == "bw":
                    from core.ccr_processor import apply_bwpoint_normalization
                    black_point, white_point = parent_ci["bw"]
                    crop = apply_bwpoint_normalization(crop, black_point, white_point)
                    child_ci = {"mode": "bw", "bw": parent_ci["bw"], "fine_rot": 0}

                index = len(children) + 1
                child = CCRImage(
                    img_obj.file_path,
                    adjustment_settings=dict(img_obj.adjustment_settings),
                    rotation_angle=img_obj.rotation_angle,
                    horizontal_mirrored=img_obj.horizontal_mirrored,
                    vertical_mirrored=img_obj.vertical_mirrored,
                    converted=child_ci is not None,
                    source_ops=img_obj.source_ops + [(baked_rotation, (fx1, fy1, fx2, fy2))],
                    preloaded_img=crop,
                    preloaded_full_size=child_full,
                    display_name=f"{stem}_s{index}{ext}",
                )
                child.conversion_inputs = child_ci
                # Inherit the parent's perceptual tint factor: the child's
                # ctor derived one from CONVERTED pixels, which would render
                # an inherited tint setting differently than the parent did.
                child.tint_balance_factor = img_obj.tint_balance_factor
                # Inherit the non-destructive base offsets; rebuild the
                # preview when the inherited state differs from what the
                # ctor already rendered with.
                child.contrast_base = img_obj.contrast_base
                child.temperature_base = img_obj.temperature_base
                child.brightness_base = img_obj.brightness_base
                if ((child.contrast_base, child.temperature_base,
                        child.brightness_base) != (0, 0, -8)
                        or img_obj.adjustment_settings.get("tint")):
                    child.update_thumbnail_and_preview()
                children.append(child)
                if progress_callback:
                    progress_callback(len(children), total)

        self.images[idx:idx + 1] = children
        self.file_paths = [im.file_path for im in self.images]
        print(f"Sliced {os.path.basename(img_obj.file_path)} into {len(children)} images")
        return len(children)

    def set_white_point(self, bgr_tuple):
        self.white_point_bgr = bgr_tuple

    def set_black_point(self, bgr_tuple):
        self.black_point_bgr = bgr_tuple

    def apply_bwpoint_to_all_images(self, progress_callback=None):
        """
        Apply B/W point film negative conversion to all loaded images using the same
        pipeline as ccr_normalize_with_reference, but with user-specified B/W points
        instead of auto-detected percentiles.  Always converts from original scan data.
        """
        if self.black_point_bgr is None or self.white_point_bgr is None:
            raise ValueError("Both black and white points must be set before applying.")
        total = len(self.images)
        if progress_callback:
            progress_callback(0, total)
        for i, img in enumerate(self.images):
            try:
                # Ensure we start from original (unprocessed) scan data
                if img.converted:
                    img.reload_image()
                processed = ccr_normalize_with_bwpoint(
                    img, self.black_point_bgr, self.white_point_bgr
                )
                if processed is not None:
                    img.resized_raw = processed
                img.converted = True
                img.conversion_inputs = {
                    "mode": "bw",
                    "bw": (tuple(self.black_point_bgr), tuple(self.white_point_bgr)),
                    "fine_rot": img.fine_rotation_angle,
                }
                img.update_thumbnail_and_preview()
            except Exception as e:
                print(f"B/W point conversion failed for image {i}: {e}")
            if progress_callback:
                progress_callback(i + 1, total)

# Singleton instance
ccr_backend = CCRBackend()