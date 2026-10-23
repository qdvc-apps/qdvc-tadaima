"""Read display metadata for a photo. Pure — no GTK.

The date a picture was taken (EXIF ``DateTimeOriginal``, falling back to
``DateTime`` and then the file's modification time), its pixel dimensions,
its file size, and a selection of camera details for the information sidebar
(:func:`camera_details`). Read-only: opens the file only to read headers.

HEIC: Pillow reads HEIC only with pillow-heif installed, and even then it can
fail on some files. Whenever Pillow can't open a HEIF file, its dimensions and
EXIF are read straight from the container instead (:mod:`qdvc.heif`), so they
are shown either way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

from .heif import is_heif, read_heif_info

try:
    from PIL import Image  # type: ignore

    _HAVE_PIL = True
except Exception:  # pragma: no cover - environment dependent
    _HAVE_PIL = False

# EXIF tags. Most camera settings live in the Exif sub-IFD, GPS in its own;
# Pillow's getexif() returns only the main IFD (IFD0), so both are fetched
# with get_ifd().
EXIF_IFD, GPS_IFD = 0x8769, 0x8825
MAKE, MODEL, DATETIME = 0x010F, 0x0110, 0x0132
EXPOSURE_TIME, F_NUMBER, ISO = 0x829A, 0x829D, 0x8827
DATETIME_ORIGINAL, EXPOSURE_BIAS, FLASH, FOCAL_LENGTH = 0x9003, 0x9204, 0x9209, 0x920A
FOCAL_35MM, LENS_MODEL = 0xA405, 0xA434


@dataclass
class PhotoMetadata:
    path: str
    width: int | None
    height: int | None
    size_bytes: int
    date_taken: datetime | None
    date_is_exif: bool  # True if from EXIF, False if filesystem fallback
    # Camera details for the information sidebar, in display order, as
    # (label, value) pairs; only those present in the file.
    camera: list[tuple[str, str]] = field(default_factory=list)
    # (latitude, longitude) in decimal degrees, if the photo is geotagged.
    location: tuple[float, float] | None = None

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
            return datetime.strptime(value.strip().rstrip("\x00"), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------- formatting
def _num(value) -> float | None:
    """An EXIF number (int, float, IFDRational, or a 1-tuple of one) as a
    float; None if absent or unusable (e.g. a 0/0 rational)."""
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None if out != out else out  # NaN


def _text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).strip().strip("\x00").strip() if value is not None else ""


def _trim(x: float, places: int = 1) -> str:
    return f"{x:.{places}f}".rstrip("0").rstrip(".")


def format_camera(make: str, model: str) -> str:
    """'Apple' + 'iPhone 15 Pro' -> 'Apple iPhone 15 Pro', but 'Canon' +
    'Canon EOS R5' -> 'Canon EOS R5' and 'NIKON CORPORATION' + 'NIKON Z 6'
    -> 'NIKON Z 6'."""
    make, model = make.strip(), model.strip()
    if not make:
        return model
    if not model:
        return make
    if model.lower().startswith(make.split()[0].lower()):
        return model
    return f"{make} {model}"


def format_exposure_time(seconds: float) -> str:
    if seconds >= 1:
        return f"{_trim(seconds)} s"
    return f"1/{round(1 / seconds)} s"


def format_coordinates(lat: float, lon: float) -> str:
    return (f"{abs(lat):.5f}° {'N' if lat >= 0 else 'S'}, "
            f"{abs(lon):.5f}° {'E' if lon >= 0 else 'W'}")


def _gps_degrees(dms, ref) -> float | None:
    if not isinstance(dms, (tuple, list)) or len(dms) != 3:
        return None
    parts = [_num(x) for x in dms]
    if any(p is None for p in parts):
        return None
    deg = parts[0] + parts[1] / 60 + parts[2] / 3600
    return -deg if _text(ref).upper() in ("S", "W") else deg


def camera_details(exif) -> tuple[list[tuple[str, str]], tuple[float, float] | None]:
    """The camera details worth showing, from a Pillow ``Image.Exif``.

    The selection: what took the photo (camera, lens), how it was exposed
    (focal length with its 35 mm equivalent, aperture, shutter speed, ISO,
    exposure compensation, flash) and where (GPS position and altitude).
    """
    rows: list[tuple[str, str]] = []
    try:
        sub = exif.get_ifd(EXIF_IFD) or {}
    except Exception:
        sub = {}
    try:
        gps = exif.get_ifd(GPS_IFD) or {}
    except Exception:
        gps = {}

    camera = format_camera(_text(exif.get(MAKE)), _text(exif.get(MODEL)))
    if camera:
        rows.append(("Camera", camera))
    lens = _text(sub.get(LENS_MODEL)) or _text(exif.get(LENS_MODEL))
    if lens:
        rows.append(("Lens", lens))

    focal = _num(sub.get(FOCAL_LENGTH))
    f35 = _num(sub.get(FOCAL_35MM))
    if focal:
        text = f"{_trim(focal)} mm"
        if f35 and abs(f35 - focal) >= 1:
            text += f" ({_trim(f35, 0)} mm in 35 mm terms)"
        rows.append(("Focal length", text))
    elif f35:
        rows.append(("Focal length", f"{_trim(f35, 0)} mm in 35 mm terms"))

    f_number = _num(sub.get(F_NUMBER))
    if f_number:
        rows.append(("Aperture", f"ƒ/{_trim(f_number)}"))
    exposure = _num(sub.get(EXPOSURE_TIME))
    if exposure and exposure > 0:
        rows.append(("Shutter speed", format_exposure_time(exposure)))
    iso = _num(sub.get(ISO))
    if iso:
        rows.append(("ISO", f"{int(iso)}"))
    bias = _num(sub.get(EXPOSURE_BIAS))
    if bias is not None and abs(bias) >= 0.05:
        sign = "+" if bias > 0 else "−"
        rows.append(("Exposure compensation", f"{sign}{_trim(abs(bias))} EV"))
    flash = sub.get(FLASH)
    if isinstance(flash, int) and not flash & 0x20:  # 0x20: the camera has no flash
        rows.append(("Flash", "Fired" if flash & 1 else "Did not fire"))

    location = None
    lat = _gps_degrees(gps.get(2), gps.get(1))
    lon = _gps_degrees(gps.get(4), gps.get(3))
    if lat is not None and lon is not None and (lat or lon):
        location = (lat, lon)
        text = format_coordinates(lat, lon)
        alt = _num(gps.get(6))
        if alt is not None:
            below = gps.get(5) in (1, b"\x01")
            text += f" · {'−' if below and alt else ''}{alt:.0f} m"
        rows.append(("Location", text))
    return rows, location


def _exif_date(exif) -> datetime | None:
    try:
        sub = exif.get_ifd(EXIF_IFD) or {}
    except Exception:
        sub = {}
    for raw in (sub.get(DATETIME_ORIGINAL), exif.get(DATETIME_ORIGINAL), exif.get(DATETIME)):
        if raw:
            parsed = _parse_exif_datetime(_text(raw))
            if parsed is not None:
                return parsed
    return None


def read_metadata(path: str) -> PhotoMetadata:
    """Read metadata for the image at *path* (read-only, best-effort)."""
    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        size_bytes = 0

    width = height = None
    exif = None

    if _HAVE_PIL:
        try:
            with Image.open(path) as im:
                width, height = im.size
                try:
                    exif = im.getexif()
                except Exception:
                    exif = None
        except Exception:
            pass

    if width is None or not exif:
        # Pillow couldn't open it (or found no EXIF): a HEIF container's own
        # records still have both.
        try:
            with open(path, "rb") as fh:
                head = fh.read(12)
        except OSError:
            head = b""
        if is_heif(head):
            info = read_heif_info(path)
            if width is None and info.width:
                width, height = info.width, info.height
            if not exif and info.exif and _HAVE_PIL:
                try:
                    exif = Image.Exif()
                    exif.load(info.exif)
                except Exception:
                    exif = None

    date_taken = _exif_date(exif) if exif else None
    date_is_exif = date_taken is not None
    camera: list[tuple[str, str]] = []
    location = None
    if exif:
        try:
            camera, location = camera_details(exif)
        except Exception:
            camera, location = [], None

    # Filesystem fallback for the date.
    if date_taken is None:
        try:
            date_taken = datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            date_taken = None

    return PhotoMetadata(
        path=path,
        width=width,
        height=height,
        size_bytes=size_bytes,
        date_taken=date_taken,
        date_is_exif=date_is_exif,
        camera=camera,
        location=location,
    )
