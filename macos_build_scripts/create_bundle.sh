#!/bin/bash
set -e

SIGN_CERTIFICATE="Apple Development: alinax@haloimagery.com"

APP_NAME="HaloImageryCCR"
BUNDLE_DIR="${APP_NAME}.app"
CONTENTS_DIR="${BUNDLE_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
PLIST_SRC="macos_build_scripts/Info.plist"
ICON_SRC="src/icons/haloimagery_logo.icns"
NUITKA_BIN="main.dist/main.bin"
DMG_NAME="${APP_NAME}.dmg"
DMG_TEMP_DIR="dmg_temp"
LICENSE_FILE="LICENSES/license-HaloImageryCCR.txt"
DMG_SETTINGS="macos_build_scripts/dmg_settings.py"

# Clean previous bundle and temp
rm -rf "$BUNDLE_DIR" "$DMG_TEMP_DIR" "$DMG_NAME"

# Create bundle structure
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

# Copy executable
cp "$NUITKA_BIN" "$MACOS_DIR/$APP_NAME"

# Copy Info.plist
cp "$PLIST_SRC" "$CONTENTS_DIR/Info.plist"

# Copy icon if it exists
if [ -f "$ICON_SRC" ]; then
    cp "$ICON_SRC" "$RESOURCES_DIR/haloimagery_logo.icns"
fi

# Copy all required .so, .dylib, and resource files from Nuitka build
cp -R main.dist/* "$MACOS_DIR/"
# Remove the copied binary duplicate
rm -f "$MACOS_DIR/main.bin"

#export CODESIGN_ALLOCATE="/Applications/Xcode.app/Contents/Developer/usr/bin/codesign_allocate"
codesign --deep --force --verify --verbose --sign "$SIGN_CERTIFICATE" $BUNDLE_DIR

echo "Bundle created at $BUNDLE_DIR"

# Prepare DMG temp folder with .app and Applications symlink
mkdir -p "$DMG_TEMP_DIR"
cp -R "$BUNDLE_DIR" "$DMG_TEMP_DIR/"
# ln -s /Applications "$DMG_TEMP_DIR/Applications"

mkdir -p "$DMG_TEMP_DIR"
cp -R "$BUNDLE_DIR" "$DMG_TEMP_DIR/"
cp "$LICENSE_FILE" "$DMG_TEMP_DIR/"  # <-- Copy license to DMG root

#ln -s /Applications "$DMG_TEMP_DIR/Applications"  # <-- Enable Applications symlink


# Create DMG using dmgbuild (requires dmgbuild to be installed)
if command -v dmgbuild >/dev/null 2>&1; then
    pushd "$DMG_TEMP_DIR"
    dmgbuild -s "../$DMG_SETTINGS" "$APP_NAME" "../$DMG_NAME"
    popd
    echo "DMG created at $DMG_NAME"
else
    echo "dmgbuild not found. Please install it with 'pip install dmgbuild' to generate a DMG."
fi

# Clean up temp folder
rm -rf "$DMG_TEMP_DIR"