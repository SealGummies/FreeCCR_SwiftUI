"""Slim non-modal status strip shown while tethering (watch-folder) is active.

Inserted into ImagePreview's own internal layout (see spec §9.1.5) — NOT wrapped
around image_preview, which would break its parent().parent() chains.

Styled to fit FreeCCR's default (light) theme: a soft amber "active session"
strip with a red recording dot and a clearly-visible Stop button.
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton


class TetherBanner(QWidget):
    stopRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TetherBanner")
        # Scope the background to the banner itself (objectName selector) so the
        # child labels/button keep their own styling.
        self.setStyleSheet(
            "#TetherBanner { background-color: #fff3cd; "
            "border-bottom: 1px solid #e6cf87; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(8)

        self._dot = QLabel("●")  # ●
        self._dot.setStyleSheet(
            "color: #d9534f; font-size: 13px; background: transparent;")
        self._status = QLabel("")
        self._status.setStyleSheet(
            "color: #5c4a00; font-weight: bold; background: transparent;")
        self._note = QLabel("")
        self._note.setStyleSheet("color: #9a6a00; background: transparent;")
        self._stop = QPushButton("Stop")
        self._stop.setFixedHeight(24)
        self._stop.setStyleSheet(
            "QPushButton { background-color: #d9534f; color: white; border: none; "
            "border-radius: 4px; padding: 2px 14px; }"
            "QPushButton:hover { background-color: #c9302c; }"
            "QPushButton:pressed { background-color: #ac2925; }")
        self._stop.clicked.connect(self.stopRequested)

        layout.addWidget(self._dot)
        layout.addWidget(self._status)
        layout.addWidget(self._note)
        layout.addStretch(1)
        layout.addWidget(self._stop)

    def set_status(self, folder, count, note=""):
        self._status.setText(
            f"Tethering — “{folder}”  ·  {count} captured")
        self._note.setText(note or "")
