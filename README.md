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

## Installation

Download the latest release from the [Releases page](https://github.com/toonoumi/FreeCCR/releases).

**Windows** — run `FreeCCR_Install_<version>.exe` and follow the installer.

**macOS** — unzip `FreeCCR_macOS_<version>.zip` and move `FreeCCR.app` to your Applications folder. Because the app is not notarized, macOS Gatekeeper will block it on first launch with a *"damaged and can't be opened"* message. This is expected — the app is not damaged. Run this command once in Terminal to clear the quarantine flag:

```bash
xattr -r -d com.apple.quarantine /Applications/FreeCCR.app
```

Then double-click the app normally. Alternatively, right-click the app → **Open** → **Open** in the dialog.

---

## Film Development & Scanning Labs

If you need a lab to develop and scan your film before using FreeCCR, check the global film developing and scanning lab directory at [FilmPhotoDeveloping.com](http://filmphotodeveloping.com/).

---

## Film Negative Conversion

FreeCCR converts color negative film scans to positive images. It does not guess — it maps what the scanner actually captured. **The software cannot compensate for a bad scan.**

> **Process one roll at a time.** The B/W point anchors are physical properties of a specific film stock, development, and scan session. Mixing frames from different rolls — or even the same roll scanned in separate sessions — will produce inconsistent results.

---

### Workflow 1 — B/W Point (recommended)

This is the most accurate method. You sample two anchor values directly from the scan: the fully exposed head or tail (white point) and the clear film base (black point). FreeCCR then maps the entire roll using those absolute anchors, so every frame inverts consistently regardless of scene content.

> **Scanning requirement for this workflow:** You must scan a frame (or partial frame) that includes the **fully exposed area** of the roll — typically the head or tail leader that was exposed to light before or after shooting. Without it, there is no physical reference to set the white point from.

**Step-by-step:**

1. Load an entire roll — one roll at a time.
2. Find the scan that contains the **fully exposed head or tail** of the roll. On the scanner it appears as the darkest area of the film, because maximum exposure creates maximum dye density.
3. Right-click on that fully exposed area and **set the white point**. This is the densest point the film can reach and anchors the top of the positive tonal range.
4. Find a clear strip of **film base** — the unexposed rebate between frames or the edge of the frame. This is the lightest area on the scan.
5. Right-click on the film base and **set the black point**. This anchors the bottom of the positive tonal range.
6. Click **Convert All**. Every frame on the roll is inverted using the same two anchors.
7. Use the sliders (exposure, contrast, white balance) for per-image fine-tuning after conversion.

**Why this works:** The fully exposed leader defines the absolute maximum density for that film stock and development. The film base defines the absolute minimum. By anchoring to these two physical references, every frame inverts consistently — regardless of how bright or dark each scene was.

**What can go wrong:**
- If no fully exposed area was scanned, you have no valid white point reference. Rescan to include the leader or tail.
- If the film base sample lands on a scratched or fogged area, the black point will be off — resample from a clean edge.
- If the scanner used auto-exposure, the raw values differ between frames and batch conversion will be inconsistent. Rescan with a fixed exposure setting.

---

### Workflow 2 — Auto

The auto workflow analyzes each frame's histogram independently and attempts to set the black and white points automatically. It requires no manual sampling, which makes it faster for simple rolls, but it is inherently per-frame — it has no knowledge of the film base or the physical density range of the stock being used.

Use auto when:
- Frames are simple and well-exposed with no extreme shadows or highlights.
- You want a quick first-pass preview before committing to B/W point work.

Do not rely on auto when:
- Frames vary widely in scene brightness (e.g. interiors next to bright exteriors on the same roll).
- You need consistent tones across multiple frames for stitching or comparison.
- The roll includes underexposed or push-processed film.

---

### Scanning requirements (applies to both workflows)

| Setting | Requirement |
|---|---|
| Exposure | **Fixed, manual** — do not use auto-exposure. Every frame on the roll must be scanned at exactly the same exposure setting. Auto-exposure changes the raw values frame-by-frame, destroying the absolute density information the conversion depends on. |
| Auto-brightness / Auto-correction | **Off** |
| Per-frame color correction | **Off** |
| Bit depth | 16-bit preferred, 14-bit minimum |
| Output color space | Linear or no ICC profile applied |
| Roll handling | **Scan one complete roll per session.** Do not mix frames from different rolls or separate scan sessions. |

**A scan that violates any of these cannot be reliably converted by FreeCCR or any other software.** The physical density information is either absent or corrupted at the point of scanning — no software can recover it after the fact.

---

## Requirements

- Python 3.11.0 exactly (newer versions are incompatible with Nuitka compilation)

---

## Running in development

```bash
git clone https://github.com/yourusername/freeccr.git
cd freeccr
pip install -r requirements.txt
python write_version.py   # required on first run or after tagging
python src/main.py
```

---

## Building for Windows

### Step 1 — Build the standalone executable

```bat
build_exe.bat
```

This generates the version file from the current git tag, then compiles `src/main.py` into a self-contained executable using Nuitka with MinGW64. All dependencies, PyOpenCL kernels, and icon assets are bundled automatically.

Output: `main.dist/` directory containing `freeccr.exe` and all required files.

### Step 2 — Build the installer

Requires **[Inno Setup 6](https://jrsoftware.org/isinfo.php)** installed at `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`. If installed elsewhere, update `ISCC_PATH` in `windows_build_scripts/build_inno_installer.bat`.

Before running, open `windows_build_scripts/inno_setup.iss` and update the `Source:` paths under `[Files]` to point to your local `main.dist\` directory.

```bat
windows_build_scripts\build_inno_installer.bat
```

The script reads the version from `git describe --tags`, injects it into the Inno Setup script, and compiles the installer. Output is written to `win_installer/FreeCCR_Install_<version>.exe`.

---

## Building for macOS

### Prerequisites

- Xcode command line tools
- An Apple Developer ID certificate (for distribution) or Apple Development certificate (for local testing)
- `dmgbuild`: `pip install dmgbuild`

Update the `SIGN_CERTIFICATE` variable at the top of the build script to match your certificate name as it appears in Keychain Access.

### Distribution build — signed and notarized DMG

```bash
bash macos_build_scripts/build_compatible.sh
```

This script:
1. Installs/upgrades dependencies and Nuitka
2. Runs `write_version.py`
3. Compiles with Nuitka targeting macOS 10.15+
4. Assembles `FreeCCR.app` with `Info.plist` and icon
5. Strips extended attributes (required for Gatekeeper)
6. Code-signs all binaries and the app bundle with hardened runtime
7. Packages into `FreeCCR.dmg` via dmgbuild
8. Submits the DMG to Apple for notarization
9. On success, moves the notarized DMG to `release/<version>/FreeCCR.dmg`

Notarization credentials must be stored in the keychain under the profile name `notaryccr`. Run this once to set it up:

```bash
xcrun notarytool store-credentials "notaryccr" \
  --apple-id "your@apple.id" \
  --team-id "YOURTEAMID" \
  --password "app-specific-password"
```

### Development build — local signing only

```bash
bash macos_build_scripts/create_bundle.sh
```

Assembles and locally signs the `.app` bundle and DMG without notarization. Suitable for internal testing.

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
