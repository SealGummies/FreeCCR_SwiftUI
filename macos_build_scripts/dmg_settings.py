import os

# Name of the mounted volume
volume_name = "HaloImageryCCR"

# Path to the .app bundle (relative to the DMG build directory)
applications_link = True  # This will create the Applications symlink

# Icon locations in the DMG window
icon_locations = {
    "HaloImageryCCR.app": (200, 110),
    "Applications": (500, 110),
}

# Background image or use a built-in background
background = "builtin-arrow"

files = [
    "HaloImageryCCR.app",
]

symlinks = { 'Applications': '/Applications' }

# Size of the icons
icon_size = 100

# License file (relative to the settings.py location or absolute path)
license = {
    "default-language": "en_US",
    "licenses": {
        "en_US": "license-HaloImageryCCR.txt"
    }
}
# Window size and position
window_rect = ((200, 120), (1080, 400))