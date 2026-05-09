from PySide6.QtWidgets import QWidget, QVBoxLayout, QSlider, QLabel, QHBoxLayout, QSizePolicy, QStyleOptionSlider, QFrame, QStyle
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QKeySequence, QShortcut
from core.ccr_backend import ccr_backend

class ResettableSlider(QSlider):
    def mousePressEvent(self, event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle_rect = self.style().subControlRect(
            QStyle.CC_Slider,
            option,
            QStyle.SC_SliderHandle,
            self
        )
        if handle_rect.contains(event.pos()):
            super().mousePressEvent(event)
        else:
            event.ignore()

    def mouseDoubleClickEvent(self, event):
        # Reset to 0 and trigger adjustment update
        old_value = self.value()
        self.setValue(0)
        
        # Find the parent SlidersPanel and trigger adjustment update
        parent_widget = self.parent()
        while parent_widget and not isinstance(parent_widget, SlidersPanel):
            parent_widget = parent_widget.parent()
        
        if parent_widget and old_value != 0:
            # Update the value label
            slider_idx = None
            for i, slider in enumerate(parent_widget.sliders):
                if slider is self:
                    slider_idx = i
                    break
            
            if slider_idx is not None:
                # Update the value label
                slider_layout = parent_widget.layout().itemAt(slider_idx + 1).layout()
                value_label = slider_layout.itemAt(2).widget()
                value_label.setText("0")
                
                # Trigger the adjustment update
                parent_widget.on_slider_changed()
        
        super().mouseDoubleClickEvent(event)

    def initStyleOption(self, option):
        option.initFrom(self)
        option.orientation = self.orientation()
        option.minimum = self.minimum()
        option.maximum = self.maximum()
        option.sliderPosition = self.sliderPosition()
        option.sliderValue = self.value()
        option.singleStep = self.singleStep()
        option.pageStep = self.pageStep()
        option.upsideDown = False
        return option

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            event.ignore()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        # Override wheel event to make each wheel step equal to 1 instead of default 3
        delta = event.angleDelta().y()
        if delta > 0:
            self.setValue(self.value() + 1)
        elif delta < 0:
            self.setValue(self.value() - 1)
        event.accept()

class SlidersPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__()
        self.sliders = []
        self.slider_labels = []
        self.image_slider_map = {}
        self.current_image_id = None
        self.adjustment_keys = ["temperature", "tint", "exposure", "brightness", "white_point", "black_point", "contrast", "saturation"]
        self.copied_adjustment = None  # Store copied adjustment settings
        self._hint_timer = QTimer(self)  # Timer for temporary hints
        self._hint_timer.setSingleShot(True)
        
        # Simple processing flag and debouncing
        self._processing = False
        self._pending_adjustment = None
        self._pending_idx = None
        
        self.initUI()
        self.setup_shortcuts()
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._process_pending_adjustment)

    def initUI(self):
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignTop)  # Align all widgets to the top

        # --- Histogram image container at the top ---
        self.histogram_label = QLabel()
        self.histogram_label.setFixedHeight(150)
        self.histogram_label.setAlignment(Qt.AlignCenter)
        self.histogram_label.setFrameShape(QFrame.NoFrame)  # No border
        self.histogram_label.setText("")
        self.histogram_label.setStyleSheet(
            "background-color: rgb(180,180,180); border: none; border-radius: 12px;"
        )  # Rounded corners and dark gray
        layout.addWidget(self.histogram_label)

        self.slider_labels = [
            "Temperature", "Tint", "Exposure", "Brightness",
            "White Point", "Black Point", "Contrast", "Saturation"
        ]

        self.current_idx = None

        self.temperature_slider_layout = self.create_slider("Temperature")
        self.tint_slider_layout = self.create_slider("Tint")
        self.exposure_slider_layout = self.create_slider("Exposure")
        self.brightness_slider_layout = self.create_slider("Brightness")
        self.white_point_slider_layout = self.create_slider("White Point")
        self.black_point_slider_layout = self.create_slider("Black Point")
        self.contrast_slider_layout = self.create_slider("Contrast")
        self.saturation_slider_layout = self.create_slider("Saturation")

        layout.addLayout(self.temperature_slider_layout)
        layout.addLayout(self.tint_slider_layout)
        layout.addLayout(self.exposure_slider_layout)
        layout.addLayout(self.brightness_slider_layout)
        layout.addLayout(self.white_point_slider_layout)
        layout.addLayout(self.black_point_slider_layout)
        layout.addLayout(self.contrast_slider_layout)
        layout.addLayout(self.saturation_slider_layout)

        # --- Add Reset and Compare buttons inline below sliders ---
        from PySide6.QtWidgets import QPushButton, QHBoxLayout

        buttons_layout = QHBoxLayout()
        self.reset_button = QPushButton("Reset")
        self.compare_button = QPushButton("Compare")
        buttons_layout.addWidget(self.reset_button)
        buttons_layout.addWidget(self.compare_button)
        layout.addLayout(buttons_layout)

        # Add Sync to All button on a new row
        sync_layout = QHBoxLayout()
        self.sync_to_all_button = QPushButton("Sync to All")
        sync_layout.addWidget(self.sync_to_all_button)
        layout.addLayout(sync_layout)

        self.reset_button.clicked.connect(self.on_reset_clicked)
        self.compare_button.pressed.connect(self.on_compare_pressed)
        self.compare_button.released.connect(self.on_compare_released)
        self.compare_button.setCheckable(False)
        self.sync_to_all_button.clicked.connect(self.on_sync_to_all_clicked)

        # --- Add Dynamic Hint section below the buttons ---
        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #666; font-size: 12px; margin-top: 8px;")
        self.hint_label.setText("")  # Start with empty hint
        layout.addWidget(self.hint_label)

        self.setLayout(layout)

    def setup_shortcuts(self):
        """
        Set up keyboard shortcuts for copy/paste functionality.
        """
        # Copy shortcut (Cmd+C on Mac, Ctrl+C on Windows/Linux)
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self)
        self.copy_shortcut.activated.connect(self.copy_adjustment_settings)
        
        # Paste shortcut (Cmd+V on Mac, Ctrl+V on Windows/Linux)
        self.paste_shortcut = QShortcut(QKeySequence.Paste, self)
        self.paste_shortcut.activated.connect(self.paste_adjustment_settings)

    def set_histogram(self, pixmap: QPixmap):
        """
        Set the histogram image in the container.
        """
        if pixmap is not None:
            self.histogram_label.setPixmap(pixmap.scaled(
                self.histogram_label.width(),
                self.histogram_label.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            ))
            self.histogram_label.setText("")  # Remove placeholder text
        else:
            self.histogram_label.clear()
            self.histogram_label.setText("")

    def create_slider(self, label_text):
        slider = ResettableSlider(Qt.Horizontal)
        slider.setMinimum(-100)
        slider.setMaximum(100)
        slider.setValue(0)
        slider.setOrientation(Qt.Horizontal)
        slider.setTickInterval(10)
        slider.setFixedHeight(30)

        label = QLabel(label_text)
        label.setMinimumWidth(70)
        label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        label.setFixedHeight(30)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        value_label = QLabel(str(slider.value()))
        value_label.setMinimumWidth(40)
        value_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # Directly call on_slider_changed without debounce
        def handle_slider_change(val, lbl=value_label):
            lbl.setText(str(val))
            self.on_slider_changed()

        slider.valueChanged.connect(handle_slider_change)

        slider_layout = QHBoxLayout()
        slider_layout.addWidget(label, alignment=Qt.AlignVCenter)
        slider_layout.addWidget(slider, alignment=Qt.AlignVCenter)
        slider_layout.addWidget(value_label, alignment=Qt.AlignVCenter)

        self.sliders.append(slider)

        return slider_layout

    def set_sliders_enabled(self, enabled: bool):
        print(f"Setting sliders enabled: {enabled}")
        for slider in self.sliders:
            slider.setEnabled(enabled)
            if not enabled:
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)
                

    def save_slider_values(self, image_id):
        pass

    def set_current_idx(self, idx):
        # Clear any pending adjustments for the previous image
        self._pending_adjustment = None
        self._pending_idx = None
        self._debounce_timer.stop()
        
        self.current_idx = idx
        adjustment = ccr_backend.get_adjustment_by_index(idx)
        print(f"Setting current index: {idx}, adjustment: {adjustment}")
        if adjustment is None or not adjustment:
            # Set all sliders and labels to 0 if no adjustment
            for i, slider in enumerate(self.sliders):
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)
                slider_layout = self.layout().itemAt(i + 1).layout()
                value_label = slider_layout.itemAt(2).widget()
                value_label.setText("0")
            return

        for i, key in enumerate(self.adjustment_keys):
            if key in adjustment and i < len(self.sliders):
                self.sliders[i].blockSignals(True)
                self.sliders[i].setValue(adjustment[key])
                self.sliders[i].blockSignals(False)
                # --- Update value label as well ---
                slider_layout = self.layout().itemAt(i + 1).layout()  # +1 if histogram is at index 0
                value_label = slider_layout.itemAt(2).widget()
                value_label.setText(str(adjustment[key]))
        if idx is not None:
            ccr_backend.apply_adjustment_by_index(idx)


    def on_slider_changed(self):
        """
        Save the current slider values to the backend when any slider changes.
        Provides immediate visual feedback while debouncing heavy processing.
        """
        if self.current_idx is not None:
            adjustment = {key: slider.value() for key, slider in zip(self.adjustment_keys, self.sliders)}
            
            # Immediate lightweight feedback - just store the adjustment settings
            if 0 <= self.current_idx < len(ccr_backend.images):
                ccr_backend.images[self.current_idx].adjustment_settings = adjustment
            
            # Immediate preview update for visual feedback
            self.parent().parent().image_preview.update_preview(self.current_idx)
            
            # Store the pending adjustment for debounced heavy processing
            self._pending_adjustment = adjustment
            self._pending_idx = self.current_idx
            self._debounce_timer.stop()
            self._debounce_timer.start(150)  # Slightly longer debounce for heavy processing
    
    def _process_pending_adjustment(self):
        """Process the pending adjustment if not already processing."""
        if not self._processing and self._pending_adjustment is not None:
            self._processing = True
            
            # Use QTimer to process in the next event loop iteration
            QTimer.singleShot(0, self._do_backend_processing)
    
    def _do_backend_processing(self):
        """Perform the heavier backend processing operations (thumbnail updates, etc.)."""
        try:
            if self._pending_idx is not None and self._pending_adjustment is not None:
                # Do the heavier processing: update thumbnails and internal caches
                if 0 <= self._pending_idx < len(ccr_backend.images):
                    ccr_backend.images[self._pending_idx].update_thumbnail_and_preview()
        finally:
            self._processing = False
            
            # Clear the pending adjustment since we've processed it
            self._pending_adjustment = None
            self._pending_idx = None
    
    def _check_for_pending(self):
        """Check if there's another pending adjustment to process."""
        if not self._processing and self._pending_adjustment is not None:
            self._process_pending_adjustment()

    def get_slider_values(self):
        return {key: slider.value() for key, slider in zip(self.adjustment_keys, self.sliders)}

    def on_reset_clicked(self):
        # Set all sliders to 0 and update preview
        for i, slider in enumerate(self.sliders):
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
            slider_layout = self.layout().itemAt(i + 1).layout()
            value_label = slider_layout.itemAt(2).widget()
            value_label.setText("0")
        # Save adjustment to backend and update preview
        if self.current_idx is not None:
            adjustment = {key: 0 for key in self.adjustment_keys}
            ccr_backend.set_adjustment_by_index(self.current_idx, adjustment)
            self.parent().parent().image_preview.update_preview(self.current_idx)

    def on_compare_pressed(self):
        # Temporarily show unadjusted image while holding the button
        if self.current_idx is not None:
            self._original_adjustment = ccr_backend.get_adjustment_by_index(self.current_idx)
            # Set all adjustments to 0 but do not save to backend
            for i, slider in enumerate(self.sliders):
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)
                slider_layout = self.layout().itemAt(i + 1).layout()
                value_label = slider_layout.itemAt(2).widget()
                value_label.setText("0")
            # Update preview with temporary adjustment
            ccr_backend.set_adjustment_by_index(self.current_idx, {key: 0 for key in self._original_adjustment or {}})
            self.parent().parent().image_preview.update_preview(self.current_idx)

    def on_compare_released(self):
        # Restore previous adjustment and update preview
        if self.current_idx is not None and hasattr(self, "_original_adjustment"):
            adjustment = self._original_adjustment or {}
            for i, key in enumerate(self.adjustment_keys):
                val = adjustment.get(key, 0)
                self.sliders[i].blockSignals(True)
                self.sliders[i].setValue(val)
                self.sliders[i].blockSignals(False)
                slider_layout = self.layout().itemAt(i + 1).layout()
                value_label = slider_layout.itemAt(2).widget()
                value_label.setText(str(val))
            ccr_backend.set_adjustment_by_index(self.current_idx, adjustment)
            self.parent().parent().image_preview.update_preview(self.current_idx)
            del self._original_adjustment

    def on_sync_to_all_clicked(self):
        """
        Apply the current image's adjustment settings to all images.
        """
        if self.current_idx is not None:
            # Show syncing hint
            self.set_hint("Syncing adjustments to all images...")
            
            # Use QTimer to allow UI to update before starting the operation
            QTimer.singleShot(100, self._perform_sync_to_all)
    
    def _perform_sync_to_all(self):
        """Perform the actual sync operation after UI update."""
        # Get the current adjustment settings
        current_adjustment = {key: slider.value() for key, slider in zip(self.adjustment_keys, self.sliders)}
        print(f"Syncing adjustment to all images: {current_adjustment}")
        
        # Apply to all images in the backend
        ccr_backend.sync_adjustment_to_all(current_adjustment)
        
        # Update the preview for the current image to reflect any changes
        self.parent().parent().image_preview.update_preview(self.current_idx)
        
        # Show completion hint
        self.set_temporary_hint("Synced all adjustments!", duration=4000)

    def copy_adjustment_settings(self):
        """
        Copy the current adjustment settings to clipboard.
        """
        if self.current_idx is not None:
            # Get the current adjustment settings from sliders
            self.copied_adjustment = {key: slider.value() for key, slider in zip(self.adjustment_keys, self.sliders)}
            print(f"Copied adjustment settings: {self.copied_adjustment}")
            self.set_temporary_hint("Adjustments Copied!", duration=4000)
        else:
            print("No image selected to copy adjustment settings from.")
            self.set_temporary_hint("No image selected to copy from", duration=4000)

    def paste_adjustment_settings(self):
        """
        Paste the copied adjustment settings to the current image.
        """
        if self.current_idx is not None and self.copied_adjustment is not None:
            print(f"Pasting adjustment settings: {self.copied_adjustment}")
            
            # Apply the copied settings to the current sliders
            for i, key in enumerate(self.adjustment_keys):
                if key in self.copied_adjustment and i < len(self.sliders):
                    self.sliders[i].blockSignals(True)
                    self.sliders[i].setValue(self.copied_adjustment[key])
                    self.sliders[i].blockSignals(False)
                    # Update value label
                    slider_layout = self.layout().itemAt(i + 1).layout()  # +1 if histogram is at index 0
                    value_label = slider_layout.itemAt(2).widget()
                    value_label.setText(str(self.copied_adjustment[key]))
            
            # Save the adjustment to backend and update preview
            ccr_backend.set_adjustment_by_index(self.current_idx, self.copied_adjustment)
            self.parent().parent().image_preview.update_preview(self.current_idx)
            self.set_temporary_hint("Adjustments Pasted!", duration=2000)
            
        elif self.copied_adjustment is None:
            print("No adjustment settings to paste. Copy settings first with Cmd+C (or Ctrl+C).")
            self.set_temporary_hint("No adjustments to paste. Copy first with Cmd+C", duration=3000)
        else:
            print("No image selected to paste adjustment settings to.")
            self.set_temporary_hint("No image selected to paste to", duration=2000)

    # --- Dynamic Hint Management Methods ---
    def set_hint(self, message, temporary=False, duration=3000):
        """
        Set a hint message in the hint label.
        
        Args:
            message (str): The hint message to display
            temporary (bool): If True, the hint will be cleared after duration
            duration (int): Duration in milliseconds for temporary hints (default: 3000ms)
        """
        self.hint_label.setText(message)
        
        if temporary:
            self._hint_timer.stop()  # Stop any existing timer
            self._hint_timer.timeout.connect(lambda: self.clear_hint())
            self._hint_timer.start(duration)
    
    def clear_hint(self):
        """
        Clear the hint message.
        """
        self.hint_label.setText("")
    
    def set_temporary_hint(self, message, duration=3000):
        """
        Set a temporary hint that will automatically clear after duration.
        
        Args:
            message (str): The hint message to display
            duration (int): Duration in milliseconds (default: 3000ms)
        """
        self.set_hint(message, temporary=True, duration=duration)