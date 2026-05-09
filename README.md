# FreeCCR

**FreeCCR** is a cross-platform desktop application for batch image preview, selection, negative conversion, and color correction, supporting a wide range of RAW and standard image formats.

---

## Features

- Accurate Negative Image conversion based on physics
- Fast thumbnail preview for RAW and standard image files
- Batch image loading from folders or file selection
- Image preview with color correction sliders
- Modern, responsive UI built with PySide6 (Qt for Python)
- Unlocked build: no activation or verification required

---

## Requirements

- Python 3.11.0 (newer versions fail with Nuitka compilation)

---

## Installation

1. **Clone the repository:**
    ```sh
    git clone https://github.com/yourusername/freeccr.git
    cd freeccr/src
    ```

2. **Install dependencies:**
    ```sh
    pip install -r requirements.txt
    ```

3. **For Windows**
   ```sh
   ./build_exe.bat
   ```

## Activation

This build does not require activation. Startup verification and license checks are disabled.

## Releases

Release links are not published in this repository.

## License

FreeCCR is licensed under the [GNU Affero General Public License v3.0](LICENSE) (**AGPL-3.0**). Like GPLv3, it is **copyleft**: if you distribute modified versions, you must license those changes under the same terms and provide corresponding source code.

AGPL adds a **network use** rule: if you **run** a modified version as a **service** (including SaaS) so users interact with it **remotely over a network**, you must offer those users the corresponding source—including for code you only deploy on servers. (Plain GPLv3 does not impose that obligation for typical SaaS.)

Bundled third-party libraries remain under their own licenses in [`LICENSES/`](LICENSES/).

## Notes

- The previous activation test key is no longer needed.
