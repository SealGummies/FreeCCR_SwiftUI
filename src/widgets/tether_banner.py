"""Slim non-modal status strip shown while tethering (watch-folder) is active.

Inserted into ImagePreview's own internal layout (see spec §9.1.5) — NOT wrapped
around image_preview, which would break its parent().parent() chains.
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton


class TetherBanner(QWidget):
    stopRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #2b2b2b;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        self._dot = QLabel("●")  # ●
        self._dot.setStyleSheet("color: #e0504a; font-size: 13px;")
        self._status = QLabel("")
        self._status.setStyleSheet("color: #e6e6e6;")
        self._note = QLabel("")
        self._note.setStyleSheet("color: #e0a030;")  # amber for warnings/notes
        self._stop = QPushButton("Stop")
        self._stop.setFixedHeight(24)
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
