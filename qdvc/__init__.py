"""QDVC Tadaima — a read-only photo browser for the filesystem.

Pure package. No GTK imports anywhere in this top-level package; the GTK 4 /
libadwaita front-end lives in ``qdvc.gtk4``.
"""

APP_ID = "qdvc.Tadaima"
APP_NAME = "QDVC Tadaima"
__version__ = "0.1.0"

# Themed freedesktop icon name (present on typical GNOME/MATE installs).
ICON_NAME = "emblem-photos"

# Supported image formats (extensions, lower-case, incl. dot). HEIC support is
# best-effort and depends on pillow-heif / a working HEIF loader being present.
SUPPORTED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic")

# Thumbnail / preview magic numbers. Saved as constants; may be revisited.
THUMB_MAX_PX = 500      # "Thumbnail" size: max width AND max height.
SCREEN_MAX_PX = 2000    # "Screen" size: max width AND max height.
SQUARE_PX = 64          # "Square" derivative crop size (sidebar icons + filmstrip).

# JPEG quality for cache derivatives. The small derivatives (thumbnail, square)
# are deliberately low quality to save disk space; the screen preview is kept
# at a more usable quality for full-window viewing.
SMALL_JPEG_QUALITY = 55
SCREEN_JPEG_QUALITY = 82

# Bump whenever the cached per-file index schema changes.
INDEX_VERSION = 3
