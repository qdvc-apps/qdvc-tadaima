"""Cache location, on-disk index, and thumbnail generation.

Pure (no GTK). Uses Pillow for image work; HEIC is best-effort via
``pillow-heif`` if present. Everything here is safe to delete and regenerate:
the cache is disposable, keyed by :func:`qdvc.naming.cache_key`, and versioned
by ``INDEX_VERSION``.

All access to the user's photo folders is **read-only**: images are opened for
reading only; derivatives are written exclusively under the cache directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import (
    INDEX_VERSION,
    SCREEN_JPEG_QUALITY,
    SCREEN_MAX_PX,
    SMALL_JPEG_QUALITY,
    SQUARE_PX,
    THUMB_MAX_PX,
)
from .models import ImageRecord
from .naming import cache_key, derivative_filename, is_supported_image

# --- optional Pillow / HEIF ---------------------------------------------
try:
    from PIL import Image  # type: ignore

    _HAVE_PIL = True
except Exception:  # pragma: no cover - environment dependent
    _HAVE_PIL = False

if _HAVE_PIL:
    try:  # best-effort HEIC support
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
        _HAVE_HEIF = True
    except Exception:  # pragma: no cover
        _HAVE_HEIF = False
else:  # pragma: no cover
    _HAVE_HEIF = False


_CACHE_DIRNAME = "qdvc-tadaima"
_INDEX_FILENAME = "index.json"
_DERIVATIVES_SUBDIR = "derivatives"


def cache_dir() -> Path:
    """Root cache directory under XDG_CACHE_HOME."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / _CACHE_DIRNAME


def derivatives_dir() -> Path:
    """Directory holding all generated derivatives (square, thumb, screen)."""
    return cache_dir() / _DERIVATIVES_SUBDIR


def index_path() -> Path:
    return cache_dir() / _INDEX_FILENAME


def ensure_dirs() -> None:
    derivatives_dir().mkdir(parents=True, exist_ok=True)


# --- index (flat, path -> record) ---------------------------------------
class Index:
    """Disposable flat index of discovered images, persisted as JSON."""

    def __init__(self) -> None:
        self.version = INDEX_VERSION
        self.records: dict[str, ImageRecord] = {}  # keyed by absolute path

    # -- persistence ------------------------------------------------------
    def load(self) -> None:
        p = index_path()
        self.records = {}
        if not p.exists():
            return
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data: dict[str, Any] = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return
        if data.get("version") != INDEX_VERSION:
            # Stale schema — discard; it will be regenerated.
            return
        for path, rec in data.get("records", {}).items():
            try:
                self.records[path] = ImageRecord(
                    path=path,
                    mtime=float(rec["mtime"]),
                    size=int(rec["size"]),
                    key=str(rec["key"]),
                    thumb_path=rec.get("thumb_path"),
                    screen_path=rec.get("screen_path"),
                    square_path=rec.get("square_path"),
                    thumb_w=int(rec.get("thumb_w", 0)),
                    thumb_h=int(rec.get("thumb_h", 0)),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def save(self) -> None:
        ensure_dirs()
        data = {
            "version": INDEX_VERSION,
            "records": {
                path: {
                    "mtime": r.mtime,
                    "size": r.size,
                    "key": r.key,
                    "thumb_path": r.thumb_path,
                    "screen_path": r.screen_path,
                    "square_path": r.square_path,
                    "thumb_w": r.thumb_w,
                    "thumb_h": r.thumb_h,
                }
                for path, r in self.records.items()
            },
        }
        p = index_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, p)  # atomic


# --- scanning ------------------------------------------------------------
# Magic-byte signatures for the formats we support. A correct extension is not
# enough: macOS AppleDouble sidecar files (e.g. ._myphoto.jpg) carry an image
# extension but hold no image data, so we sniff the header before accepting.
_APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"  # AppleDouble / AppleSingle header


def looks_like_image(path: str) -> bool:
    """True if *path*'s first bytes match a supported image signature.

    Read-only: opens the file, reads a small header, closes it. Explicitly
    rejects AppleDouble sidecar files even though their extension may match.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    if len(head) < 4:
        return False
    if head[:4] == _APPLEDOUBLE_MAGIC:
        return False  # AppleDouble encoded Macintosh file
    # JPEG: FF D8 FF
    if head[:3] == b"\xff\xd8\xff":
        return True
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    # HEIC/HEIF: ISO-BMFF with 'ftyp' at bytes 4-8 and a HEIF brand.
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"heif", b"mif1", b"msf1", b"hevc", b"hevx"):
            return True
    return False


def scan_images(roots: list[str]) -> list[str]:
    """Return absolute paths of supported images under *roots* (recursive).

    Read-only: directory listing, stat, and a small header read for content
    sniffing. Files whose contents are not a real supported image (e.g. macOS
    AppleDouble ``._name.jpg`` sidecars) are skipped. Symlink loops are avoided
    by not following directory symlinks.
    """
    found: list[str] = []
    seen_dirs: set[str] = set()
    for root in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            real = os.path.realpath(dirpath)
            if real in seen_dirs:
                dirnames[:] = []
                continue
            seen_dirs.add(real)
            for fn in filenames:
                if not is_supported_image(fn):
                    continue
                full = os.path.join(dirpath, fn)
                if looks_like_image(full):
                    found.append(full)
    return found


def sync_index(index: Index, roots: list[str]) -> tuple[list[ImageRecord], list[str]]:
    """Reconcile *index* with the current state of *roots*.

    Returns ``(new_or_changed, removed_paths)``. Does not generate thumbnails
    (that is done separately so it can be throttled/backgrounded).
    """
    current = set(scan_images(roots))
    new_or_changed: list[ImageRecord] = []

    # Additions / modifications.
    for path in current:
        try:
            st = os.stat(path)
        except OSError:
            continue
        existing = index.records.get(path)
        if (
            existing is None
            or existing.mtime != st.st_mtime
            or existing.size != st.st_size
        ):
            rec = ImageRecord(
                path=path, mtime=st.st_mtime, size=st.st_size, key=cache_key(path)
            )
            index.records[path] = rec
            new_or_changed.append(rec)

    # Removals.
    removed = [p for p in index.records if p not in current]
    for p in removed:
        _delete_derivatives(index.records[p])
        del index.records[p]

    return new_or_changed, removed


# --- thumbnail generation -----------------------------------------------
def has_all_derivatives(rec: ImageRecord) -> bool:
    """True if every derivative for *rec* already exists on disk.

    Used so a normal launch regenerates derivatives only for images that are
    missing them, never for the whole library.
    """
    for attr in ("thumb_path", "screen_path", "square_path"):
        p = getattr(rec, attr, None)
        if not p or not os.path.exists(p):
            return False
    return True


def _delete_derivatives(rec: ImageRecord) -> None:
    for attr in ("thumb_path", "screen_path", "square_path"):
        p = getattr(rec, attr, None)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def generate_derivatives(rec: ImageRecord, want_square: bool = True) -> bool:
    """Generate thumbnail/screen/square derivatives for *rec*.

    A ``square`` derivative is now generated for *every* image (it is used both
    for sidebar folder icons and for the filmstrip), so ``want_square`` defaults
    to True and is retained only for API compatibility.

    Returns True on success. Requires Pillow; if unavailable, returns False and
    leaves derivative paths as None (the UI falls back to plain folder icons and
    a "preview unavailable" placeholder).
    """
    if not _HAVE_PIL:
        return False
    ensure_dirs()
    td = derivatives_dir()
    try:
        with Image.open(rec.path) as im:
            im = im.convert("RGB")

            # Screen size — kept at reasonable quality for full-window viewing.
            screen = im.copy()
            screen.thumbnail((SCREEN_MAX_PX, SCREEN_MAX_PX), Image.LANCZOS)
            screen_path = td / derivative_filename(rec.key, "screen", "jpg")
            screen.save(screen_path, "JPEG", quality=SCREEN_JPEG_QUALITY)
            rec.screen_path = str(screen_path)

            # Thumbnail — low-quality JPEG to save disk space. Record its true
            # scaled dimensions so the UI can size the widget to hug the image.
            thumb = im.copy()
            thumb.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX), Image.LANCZOS)
            thumb_path = td / derivative_filename(rec.key, "thumb", "jpg")
            thumb.save(
                thumb_path, "JPEG", quality=SMALL_JPEG_QUALITY, optimize=True
            )
            rec.thumb_path = str(thumb_path)
            rec.thumb_w, rec.thumb_h = thumb.size

            # Square crop — used for sidebar folder icons and the filmstrip.
            sq = _center_square(im, SQUARE_PX)
            square_path = td / derivative_filename(rec.key, "square", "jpg")
            sq.save(square_path, "JPEG", quality=SMALL_JPEG_QUALITY, optimize=True)
            rec.square_path = str(square_path)
        return True
    except Exception:
        return False


def _center_square(im: "Image.Image", size: int) -> "Image.Image":
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = im.crop((left, top, left + side, top + side))
    return cropped.resize((size, size), Image.LANCZOS)


def cache_status(index: Index) -> dict[str, Any]:
    """Human-facing cache stats for the "Cache status" dialog."""
    total = len(index.records)
    with_thumb = sum(1 for r in index.records.values() if r.thumb_path)
    with_screen = sum(1 for r in index.records.values() if r.screen_path)

    bytes_used = 0
    file_count = 0
    td = derivatives_dir()
    if td.exists():
        for entry in td.iterdir():
            try:
                bytes_used += entry.stat().st_size
                file_count += 1
            except OSError:
                pass

    return {
        "indexed_images": total,
        "thumbs_generated": with_thumb,
        "screens_generated": with_screen,
        "cache_files": file_count,
        "cache_bytes": bytes_used,
        "cache_dir": str(cache_dir()),
        "pillow_available": _HAVE_PIL,
        "heif_available": _HAVE_HEIF,
    }


def clear_cache() -> None:
    """Delete all generated derivatives and the index (force regenerate)."""
    td = derivatives_dir()
    if td.exists():
        for entry in td.iterdir():
            try:
                entry.unlink()
            except OSError:
                pass
    ip = index_path()
    if ip.exists():
        try:
            ip.unlink()
        except OSError:
            pass


def human_bytes(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step:
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} PB"
