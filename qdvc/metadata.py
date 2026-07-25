"""Read display metadata for a photo. Pure — no GTK.

Extracts the date a picture was taken (EXIF ``DateTimeOriginal``, falling back
to filesystem modification time), pixel dimensions, and human-readable file
size. Read-only: opens the file only to read headers/EXIF.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

try:
    from PIL import Image, ExifTags  # type: ignore

    _HAVE_PIL = True
    # EXIF tag ids we care about.
    _DATETIME_ORIGINAL = next(
        (k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal"), 36867
    )
    _DATETIME = next((k for k, v in ExifTags.TAGS.items() if v == "DateTime"), 306)
except Exception:  # pragma: no cover - environment dependent
    _HAVE_PIL = False
    _DATETIME_ORIGINAL = 36867
    _DATETIME = 306


@dataclass
class PhotoMetadata:
    path: str
    width: int | None
    height: int | None
    size_bytes: int
    date_taken: datetime | None
    date_is_exif: bool  # True if from EXIF, False if filesystem fallback

    @property
    def dimensions_str(self) -> str:
        if self.width and self.height:
            return f"{self.width} × {self.height} px"
        return "Unknown dimensions"

    @property
    def date_str(self) -> str:
        if self.date_taken is None:
            return "Unknown date"
        stamp = self.date_taken.strftime("%-d %b %Y, %H:%M")
        return stamp if self.date_is_exif else f"{stamp} (file date)"


def human_size(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step:
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} PB"


def _parse_exif_datetime(value: str) -> datetime | None:
    # EXIF format: "YYYY:MM:DD HH:MM:SS"
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def read_metadata(path: str) -> PhotoMetadata:
    """Read metadata for the image at *path* (read-only, best-effort)."""
    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        size_bytes = 0

    width = height = None
    date_taken: datetime | None = None
    date_is_exif = False

    if _HAVE_PIL:
        try:
            with Image.open(path) as im:
                width, height = im.size
                exif = None
                try:
                    exif = im.getexif()
                except Exception:
                    exif = None
                if exif:
                    raw = exif.get(_DATETIME_ORIGINAL) or exif.get(_DATETIME)
                    if raw:
                        parsed = _parse_exif_datetime(str(raw))
                        if parsed is not None:
                            date_taken = parsed
                            date_is_exif = True
        except Exception:
            pass

    # Filesystem fallback for the date.
    if date_taken is None:
        try:
            date_taken = datetime.fromtimestamp(os.path.getmtime(path))
            date_is_exif = False
        except OSError:
            date_taken = None

    return PhotoMetadata(
        path=path,
        width=width,
        height=height,
        size_bytes=size_bytes,
        date_taken=date_taken,
        date_is_exif=date_is_exif,
    )
