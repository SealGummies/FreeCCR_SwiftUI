from typing import List, Optional
from core.ccr_image import CCRImage
from core.ccr_processor import ccr_normalize_with_reference, ccr_normalize_with_bwpoint, auto_fine_angle, auto_frame, auto_frame_v2
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
                img = CCRImage(path)
                print(f"Successfully loaded: {os.path.basename(path)}")
                return path, img
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
                try:
                    print(f"Loading image: {os.path.basename(path)}")
                    img = CCRImage(path)
                    self.images.append(img)
                    print(f"Successfully loaded: {os.path.basename(path)}")
                except Exception as e:
                    print(f"Failed to load {os.path.basename(path)}: {e}")
                    continue
        else:
            # Parallel loading - collect into local list to avoid concurrent modification
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_path = {executor.submit(load_single_image, path): path for path in file_paths}

                for future in concurrent.futures.as_completed(future_to_path):
                    if cancel_flag and cancel_flag():
                        break
                    path, img = future.result()
                    if img is not None:
                        results.append(img)

            results.sort(key=lambda img: os.path.basename(img.file_path))
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

    def sync_adjustment_to_all(self, adjustment: dict):
        """
        Apply the given adjustment settings to all images in the backend.
        
        Args:
            adjustment (dict): Dictionary containing adjustment parameters like
                             'temperature', 'tint', 'exposure', 'brightness', etc.
        """
        if not adjustment:
            return
            
        print(f"Syncing adjustment to {len(self.images)} images: {adjustment}")
        
        for idx, image_obj in enumerate(self.images):
            try:
                # Set the adjustment settings for each image
                image_obj.adjustment_settings = adjustment.copy()
                # Update the thumbnail and preview to reflect the changes
                image_obj.update_thumbnail_and_preview()
                print(f"Applied adjustment to image {idx + 1}/{len(self.images)}")
            except Exception as e:
                print(f"Failed to apply adjustment to image at index {idx}: {e}")

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

    def export_image_by_index(self, idx: int, output_path: str, jpg_output: bool = False):
        """
        Exports the processed image at the given index to the specified output path.
        Routes to bwpoint or reference-frame pipeline depending on how the image was converted.
        """
        if idx is not None and 0 <= idx < len(self.images):
            image_obj = self.images[idx]
            try:
                if image_obj.reference_frame is None and self.black_point_bgr is not None and self.white_point_bgr is not None:
                    # B/W point conversion — re-process from original full-res file
                    ccr_normalize_with_bwpoint(image_obj, self.black_point_bgr, self.white_point_bgr,
                                               output_path=output_path, water_mark=not self.software_activated,
                                               jpg_out=jpg_output)
                else:
                    ccr_normalize_with_reference(image_obj, output_path=output_path,
                                                 water_mark=not self.software_activated, jpg_out=jpg_output)
            except Exception as e:
                print(f"Failed to export image at index {idx}: {e}")
            
    def export_all_images(self, output_folder: str, jpg_output: bool = False, progress_callback=None):
        """
        Exports all processed images to the specified output folder using parallel processing.
        """
        import time
        total_start_time = time.time()
        
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
        
        # Count converted images for progress tracking
        converted_images = [(idx, img) for idx, img in enumerate(self.images) if img.converted]
        total_images = len(converted_images)
        
        print(f"Starting export of {total_images} images...")
        
        if total_images == 0:
            return
        
        def export_single_image(idx_img_tuple):
            idx, img = idx_img_tuple
            import time
            start_time = time.time()
            try:                
                base_name = os.path.splitext(os.path.basename(img.file_path))[0]
                if jpg_output:
                    output_path = os.path.join(output_folder, f"{base_name}_ccr.jpg")
                else:
                    output_path = os.path.join(output_folder, f"{base_name}_ccr.tiff")
                self.export_image_by_index(idx, output_path, jpg_output=jpg_output)
                elapsed = time.time() - start_time
                print(f"Export completed for {base_name} in {elapsed:.2f}s")
                return idx, True, None
            except Exception as e:
                elapsed = time.time() - start_time
                print(f"Export failed for image {idx} after {elapsed:.2f}s: {e}")
                return idx, False, str(e)
        
        # Use parallel processing for exports
        max_workers = min(4, os.cpu_count() or 1)  # Use very few workers for export to avoid I/O contention
        completed_count = 0
        
        if progress_callback:
            progress_callback(0, total_images)
        
        if max_workers == 1:
            # Sequential fallback
            for idx, img in converted_images:
                try:                
                    base_name = os.path.splitext(os.path.basename(img.file_path))[0]
                    if jpg_output:
                        output_path = os.path.join(output_folder, f"{base_name}_ccr.jpg")
                    else:
                        output_path = os.path.join(output_folder, f"{base_name}_ccr.tiff")
                    self.export_image_by_index(idx, output_path, jpg_output=jpg_output)
                except Exception as e:
                    print(f"Failed to export image at index {idx}: {e}")
                completed_count += 1
                if progress_callback:
                    progress_callback(completed_count, total_images)
        else:
            # Parallel export
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_idx = {executor.submit(export_single_image, (idx, img)): idx 
                                for idx, img in converted_images}
                
                for future in concurrent.futures.as_completed(future_to_idx):
                    idx, success, error = future.result()
                    if not success:
                        print(f"Failed to export image at index {idx}: {error}")
                    completed_count += 1
                    if progress_callback:
                        progress_callback(completed_count, total_images)
        
        total_elapsed = time.time() - total_start_time
        print(f"Export completed! Total time: {total_elapsed:.2f}s for {total_images} images ({total_elapsed/total_images:.2f}s per image)")
    
    def remove_image_by_index(self, idx: int):
        """
        Remove the image and its file path at the given index.
        """
        if idx is not None and 0 <= idx < len(self.images):
            del self.images[idx]
            self.file_paths = [img.file_path for img in self.images]

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
                img.update_thumbnail_and_preview()
            except Exception as e:
                print(f"B/W point conversion failed for image {i}: {e}")
            if progress_callback:
                progress_callback(i + 1, total)

# Singleton instance
ccr_backend = CCRBackend()