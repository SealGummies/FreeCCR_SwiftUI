from PySide6.QtWidgets import QMainWindow, QHBoxLayout, QWidget, QFileDialog, QMessageBox, QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel, QLineEdit
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, QEvent, QThread, Signal, QObject
from widgets.thumbnail_list import ThumbnailList
from widgets.image_preview import ImagePreview
from widgets.sliders_panel import SlidersPanel
from core.ccr_backend import ccr_backend
import ctypes
from version import VERSION  # Make sure version.py is in your src folder
import os
import sys
from activation.activation import validate_software
import webbrowser

from utils.unicode_path_utils import normalize_unicode_path, validate_unicode_path

class ImageLoaderWorker(QObject):
    finished = Signal()
    def __init__(self, folder=None, files=None):
        super().__init__()
        self.folder = folder
        self.files = files
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self.folder:
            ccr_backend.load_images_from_folder(self.folder, cancel_flag=lambda: self._cancelled)
        elif self.files:
            ccr_backend.load_images_from_files(self.files, cancel_flag=lambda: self._cancelled)
        self.finished.emit()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FreeCCR")
        self.setGeometry(100, 100, 1860, 1080)
        app_icon = QIcon("./icons/freeccr_logo.png")
        self.setWindowIcon(app_icon)
        # try:
        #     myappid = 'ccr.project.client'
        #     ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        # except Exception:
        #     pass

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)

        self.layout = QHBoxLayout(self.central_widget)

        self.thumbnail_list = ThumbnailList(self.on_image_selected)
        self.thumbnail_list.setFixedWidth(216)

        self.image_preview = ImagePreview(self)
        self.image_preview.setMinimumWidth(900)

        self.sliders_panel = SlidersPanel(self)
        self.sliders_panel.setFixedWidth(300)
        self.sliders_panel.set_sliders_enabled(False)
        self.sliders_panel.image_preview = self.image_preview

        self.layout.addWidget(self.thumbnail_list, 0)
        self.layout.addWidget(self.image_preview, 3)
        self.layout.addWidget(self.sliders_panel, 0)

        self.installEventFilter(self)
        self.create_menu()

        # Activation and verification checks are intentionally bypassed.
        ccr_backend.software_activated = True
        _, license_type = validate_software()
        if license_type:
            self.setWindowTitle(f"FreeCCR - {license_type}")

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self,
            "Confirm Exit",
            "Are you sure you want to exit FreeCCR?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()

    def on_image_selected(self, file_path):
        pass

    def create_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        open_action = file_menu.addAction("Open Files")
        open_action.triggered.connect(self.open_files)

        open_folder_action = file_menu.addAction("Open Folder")
        open_folder_action.triggered.connect(self.open_folder)

        # Add Help menu with About, Licenses, Activation, and Help actions
        help_menu = menu_bar.addMenu("Help")
        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self.show_about_dialog)

        licenses_action = help_menu.addAction("Licenses")
        licenses_action.triggered.connect(self.show_licenses_dialog)

        help_action = help_menu.addAction("Help")
        help_action.triggered.connect(self.open_help_website)

    def show_activation_dialog(self):
        QMessageBox.information(
            self,
            "Activation Not Required",
            "Activation and verification are disabled in this build."
        )

    def show_about_dialog(self):
        QMessageBox.about(
            self,
            "About FreeCCR",
            f"""<b>FreeCCR</b><br>Version {VERSION}<br><br>Copyright © 2025 FreeCCR
            <br>Website: <a href="https://www.freeccr.com">www.freeccr.com</a><br>
            <br>Built along with PySide6 v6.7.1<br>
            <br>For more info, visit PySide6 on PyPI: https://pypi.org/project/PySide6/ <br>
            <br>For third-party licenses, see the Licenses section in the Help menu."""
        )

    def show_licenses_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Third-Party Licenses")
        layout = QVBoxLayout(dialog)

        text_edit = QTextEdit(dialog)
        text_edit.setReadOnly(True)
        licenses_text = self.load_licenses_text()
        text_edit.setPlainText(licenses_text)
        layout.addWidget(text_edit)

        close_button = QPushButton("Close", dialog)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)

        dialog.resize(700, 500)
        dialog.exec()

    def load_licenses_text(self):
        # Try both ./LICENSES and ../LICENSES
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        possible_dirs = [
            os.path.join(base_dir, "LICENSES"),
            os.path.join(os.path.dirname(base_dir), "LICENSES"),
        ]
        licenses_text = ""
        found = False
        for licenses_dir in possible_dirs:
            if os.path.isdir(licenses_dir):
                found = True
                for filename in sorted(os.listdir(licenses_dir)):
                    if filename.lower().endswith(".txt"):
                        path = os.path.join(licenses_dir, filename)
                        try:
                            with open(path, "r", encoding="utf-8") as f:
                                licenses_text += f"===== {filename} =====\n"
                                licenses_text += f.read() + "\n\n"
                        except Exception as e:
                            licenses_text += f"Could not read {filename}: {e}\n\n"
                break
        if not found:
            licenses_text = "No license files found."
        return licenses_text

    def _cleanup_loader(self):
        """Called when the loader thread finishes. Nulls the references so isRunning() is never called on a deleted C++ object."""
        self._loader_thread = None
        self._loader_worker = None

    def _stop_loader_if_running(self):
        """Cancel and join any in-progress loader thread."""
        if getattr(self, '_loader_thread', None) is not None:
            try:
                if self._loader_thread.isRunning():
                    if self._loader_worker is not None:
                        self._loader_worker.cancel()
                    self._loader_thread.quit()
                    self._loader_thread.wait(3000)
            except RuntimeError:
                pass
            self._loader_thread = None
            self._loader_worker = None

    def open_files(self):
        # options = QFileDialog.Options()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Images",
            "",
            "Images (*.dng *.tif *.tiff *.arw *.nef *.cr2 *.cr3 *.raf *.png *.jpg *.jpeg *.rw2 *.3fr *.fff);;All Files (*)"
        )
        if files:
            # Validate and normalize Unicode paths
            valid_files = []
            invalid_files = []
            
            for file_path in files:
                normalized_path = normalize_unicode_path(file_path)
                if validate_unicode_path(normalized_path):
                    valid_files.append(normalized_path)
                else:
                    invalid_files.append(file_path)
            
            if invalid_files:
                invalid_list = '\n'.join(invalid_files[:5])  # Show first 5 invalid files
                if len(invalid_files) > 5:
                    invalid_list += f'\n... and {len(invalid_files) - 5} more files'
                QMessageBox.warning(
                    self,
                    "Unicode Path Warning",
                    f"Some files have paths that may not be processed correctly due to Unicode characters:\n\n{invalid_list}\n\nThese files will be skipped. Please consider moving these files to paths with simpler names."
                )
            
            if valid_files:
                self._stop_loader_if_running()
                self.thumbnail_list.show_loading_dialog()
                self._loader_thread = QThread()
                self._loader_worker = ImageLoaderWorker(files=valid_files)
                self._loader_worker.moveToThread(self._loader_thread)
                self._loader_thread.started.connect(self._loader_worker.run)
                self._loader_worker.finished.connect(self._loader_thread.quit)
                self._loader_worker.finished.connect(self._loader_worker.deleteLater)
                self._loader_thread.finished.connect(self._cleanup_loader)
                self._loader_worker.finished.connect(self.thumbnail_list.load_thumbnails)
                self._loader_thread.start()
            elif files:  # If there were files selected but all were invalid
                QMessageBox.critical(
                    self,
                    "No Valid Files",
                    "None of the selected files could be processed due to Unicode character issues in their paths. Please rename the files or move them to simpler paths."
                )

    def open_folder(self):
        # options = QFileDialog.Options()
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder",
            ""
        )
        if folder:
            # Validate and normalize Unicode path
            normalized_folder = normalize_unicode_path(folder)
            if not validate_unicode_path(normalized_folder):
                QMessageBox.warning(
                    self,
                    "Unicode Path Warning",
                    f"The selected folder path contains characters that may cause issues:\n\n{folder}\n\nPlease consider using a folder with a simpler path name."
                )
                return
                
            self._stop_loader_if_running()
            self.thumbnail_list.show_loading_dialog()
            self._loader_thread = QThread()
            self._loader_worker = ImageLoaderWorker(normalized_folder)
            self._loader_worker.moveToThread(self._loader_thread)
            self._loader_thread.started.connect(self._loader_worker.run)
            self._loader_worker.finished.connect(self._loader_thread.quit)
            self._loader_worker.finished.connect(self._loader_worker.deleteLater)
            self._loader_thread.finished.connect(self._cleanup_loader)
            self._loader_worker.finished.connect(self.thumbnail_list.load_thumbnails)
            self._loader_thread.start()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.thumbnail_list.thumbnail_list.setFocus()
            self.thumbnail_list.thumbnail_list.keyPressEvent(event)
            return True
        return super().eventFilter(obj, event)

    def open_help_website(self):
        webbrowser.open("https://www.freeccr.com/help")

class ActivationDialog(QDialog):
    def __init__(self, parent=None, allow_deactivate=False):
        super().__init__(parent)
        self.setWindowTitle("Activate FreeCCR")
        self.setMinimumWidth(350)
        layout = QVBoxLayout(self)
        self.allow_deactivate = allow_deactivate

        layout.addWidget(QLabel("Please activate your software to continue."))

        self.email_input = QLineEdit(self)
        self.email_input.setPlaceholderText("Email")
        layout.addWidget(self.email_input)

        self.key_input = QLineEdit(self)
        self.key_input.setPlaceholderText("Activation Key")
        layout.addWidget(self.key_input)

        self.activate_btn = QPushButton("Activate", self)
        self.activate_btn.clicked.connect(self.try_activate)
        layout.addWidget(self.activate_btn)

        self.eval_btn = QPushButton("I'm still evaluating", self)
        self.eval_btn.clicked.connect(self.use_unpaid_version)
        layout.addWidget(self.eval_btn)

        self.buy_btn = QPushButton("I need to purchase a license", self)
        self.buy_btn.clicked.connect(self.open_buy_page)
        layout.addWidget(self.buy_btn)

        if allow_deactivate:
            self.deactivate_btn = QPushButton("Deactivate Current Key", self)
            self.deactivate_btn.clicked.connect(self.deactivate_key)
            layout.addWidget(self.deactivate_btn)

            # Pre-fill fields with current activation info if available
            from activation.activation import get_activation_key
            from activation.activation import get_license_type
            key = get_activation_key()
            typ = get_license_type()
            if key:
                self.key_input.setText(key)
                self.deactivate_btn.setEnabled(True)
            else:
                self.deactivate_btn.setEnabled(False)
            if typ:
                self.setWindowTitle(f"Activation ({typ})")

    def try_activate(self):
        email = self.email_input.text().strip()
        key = self.key_input.text().strip()
        if not email or not key:
            QMessageBox.warning(self, "Input Required", "Please enter both email and activation key.")
            return
        from activation.activation import activate_software
        if activate_software(key, email):
            QMessageBox.information(self, "Activated", "Activation successful!")
            self.accept()
        else:
            QMessageBox.critical(self, "Activation Failed", "Activation failed. Please check your key and email.")

    def deactivate_key(self):
        from activation.activation import deactivate_software
        if QMessageBox.question(self, "Deactivate", "Are you sure you want to deactivate this key?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            if deactivate_software():
                QMessageBox.information(self, "Deactivated", "Software deactivated. You can now enter a new key.")
                self.key_input.clear()
                self.email_input.clear()
                ccr_backend.software_activated = False
                self.setWindowTitle("Activate FreeCCR")
            else:
                QMessageBox.warning(self, "Failed", "Failed to deactivate. Maybe already deactivated?")
            self.reject()

    def use_unpaid_version(self):
        QMessageBox.information(self, "Unpaid Version", "You are using the unpaid version. Exported images will have watermarks.")
        self.reject()

    def open_buy_page(self):
        webbrowser.open("https://www.freeccr.com/buy")

    def closeEvent(self, event):
        if not self.allow_deactivate: #opening from within the app, skip the warning
            QMessageBox.information(self, "Unpaid Version", "You are using the unpaid version. Exported images will have watermarks.")
        self.reject()
        event.ignore()