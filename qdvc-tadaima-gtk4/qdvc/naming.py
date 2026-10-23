"""id / key / filename helpers. Pure — no GTK."""

from __future__ import annotations

import hashlib
import os

from . import SUPPORTED_EXTENSIONS


def cache_key(path: str) -> str:
    """Stable cache key for a source image path.

    Uses a hash of the absolute, normalised path so that cache files have flat,
    filesystem-safe names and do not collide across folders.
    """
    norm = os.path.normpath(os.path.abspath(path))
    return hashlib.sha1(norm.encode("utf-8", "surrogatepass")).hexdigest()


def is_supported_image(path: str) -> bool:
    """True if *path* has a supported image extension."""
    ext = os.path.splitext(path)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def derivative_filename(key: str, kind: str, ext: str = "jpg") -> str:
    """Cache filename for a derivative. *kind* is thumb/screen/square; *ext* is
    the file extension without a dot (e.g. "jpg", "png")."""
    return f"{key}.{kind}.{ext}"
