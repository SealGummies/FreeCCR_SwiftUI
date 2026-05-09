#!/bin/bash
# filepath: create_icns.sh

# Usage: ./create_icns.sh logo.png output.icns

set -e

if [ $# -ne 2 ]; then
  echo "Usage: $0 input.png output.icns"
  exit 1
fi

INPUT_PNG="$1"
OUTPUT_ICNS="$2"
ICONSET_DIR="icon.iconset"

mkdir -p "$ICONSET_DIR"

# Generate all required icon sizes
sips -z 16 16     "$INPUT_PNG" --out "$ICONSET_DIR/icon_16x16.png"
sips -z 32 32     "$INPUT_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png"
sips -z 32 32     "$INPUT_PNG" --out "$ICONSET_DIR/icon_32x32.png"
sips -z 64 64     "$INPUT_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png"
sips -z 128 128   "$INPUT_PNG" --out "$ICONSET_DIR/icon_128x128.png"
sips -z 256 256   "$INPUT_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png"
sips -z 256 256   "$INPUT_PNG" --out "$ICONSET_DIR/icon_256x256.png"
sips -z 512 512   "$INPUT_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png"
sips -z 512 512   "$INPUT_PNG" --out "$ICONSET_DIR/icon_512x512.png"
cp "$INPUT_PNG" "$ICONSET_DIR/icon_512x512@2x.png"

# Create the .icns file
iconutil -c icns "$ICONSET_DIR" -o "$OUTPUT_ICNS"

# Clean up
rm -r "$ICONSET_DIR"

echo "Created $OUTPUT_ICNS"