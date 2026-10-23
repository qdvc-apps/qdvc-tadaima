"""Read a HEIF/HEIC photo's size and EXIF straight from its container. Pure.

HEIC files are ISO base media files: a tree of boxes. The image size lives in
the primary item's ``ispe`` property (rotated by its ``irot`` property, which
decoders apply), and the EXIF block is an item of type ``Exif`` whose bytes
the ``iloc`` box locates. None of this needs a HEVC decoder or pillow-heif,
so the photo's details are known however the image itself gets decoded.

Read-only, and defensive: anything unexpected yields ``None`` values rather
than an exception.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEIF_BRANDS = (b"heic", b"heix", b"heif", b"mif1", b"msf1", b"hevc", b"hevx", b"heim", b"heis")
_MAX_META = 16 * 1024 * 1024   # the meta box: item tables and properties
_MAX_EXIF = 4 * 1024 * 1024


@dataclass
class HeifInfo:
    width: int | None = None
    height: int | None = None
    exif: bytes | None = None   # TIFF-structured EXIF ("II*\0"/"MM\0*"...), if any


def is_heif(head: bytes) -> bool:
    return len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS


def _boxes(data: bytes, start: int, end: int):
    """(type, payload start, box end) for each box in data[start:end]."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _uint(data: bytes, pos: int, size: int) -> tuple[int, int]:
    """Read a big-endian unsigned int of *size* bytes (0, 4 or 8)."""
    if size == 0:
        return 0, pos
    if size == 4:
        return struct.unpack_from(">I", data, pos)[0], pos + 4
    if size == 8:
        return struct.unpack_from(">Q", data, pos)[0], pos + 8
    if size == 2:
        return struct.unpack_from(">H", data, pos)[0], pos + 2
    raise ValueError(f"unsupported field size {size}")


def _read_meta(f) -> bytes | None:
    """The top-level ``meta`` box's payload, read from file object *f*."""
    f.seek(0, 2)
    file_end = f.tell()
    pos = 0
    while pos + 8 <= file_end:
        f.seek(pos)
        head = f.read(16)
        if len(head) < 8:
            return None
        size, kind = struct.unpack_from(">I4s", head)
        header = 8
        if size == 1 and len(head) >= 16:
            size, header = struct.unpack_from(">Q", head, 8)[0], 16
        elif size == 0:
            size = file_end - pos
        if size < header:
            return None
        if kind == b"meta":
            if size > _MAX_META:
                return None
            f.seek(pos + header)
            return f.read(size - header)
        pos += size
    return None


def _parse_iloc(data: bytes, start: int, end: int) -> dict:
    """item ID -> (construction method, [(offset, length), ...])."""
    version = data[start]
    pos = start + 4
    sizes = data[pos]
    offset_size, length_size = sizes >> 4, sizes & 15
    sizes = data[pos + 1]
    base_offset_size = sizes >> 4
    index_size = sizes & 15 if version in (1, 2) else 0
    pos += 2
    if version < 2:
        count, pos = struct.unpack_from(">H", data, pos)[0], pos + 2
    else:
        count, pos = struct.unpack_from(">I", data, pos)[0], pos + 4
    items = {}
    for _ in range(count):
        if version < 2:
            item_id, pos = struct.unpack_from(">H", data, pos)[0], pos + 2
        else:
            item_id, pos = struct.unpack_from(">I", data, pos)[0], pos + 4
        method = 0
        if version in (1, 2):
            method = struct.unpack_from(">H", data, pos)[0] & 15
            pos += 2
        pos += 2  # data_reference_index
        base, pos = _uint(data, pos, base_offset_size)
        n_extents, pos = struct.unpack_from(">H", data, pos)[0], pos + 2
        extents = []
        for _ in range(n_extents):
            if index_size:
                _index, pos = _uint(data, pos, index_size)
            offset, pos = _uint(data, pos, offset_size)
            length, pos = _uint(data, pos, length_size)
            extents.append((base + offset, length))
        items[item_id] = (method, extents)
        if pos > end:
            raise ValueError("iloc overrun")
    return items


def _parse_iinf(data: bytes, start: int, end: int) -> dict:
    """item ID -> item type (4 bytes)."""
    version = data[start]
    pos = start + 4
    pos += 2 if version == 0 else 4  # entry count; the boxes follow
    types = {}
    for kind, p, e in _boxes(data, pos, end):
        if kind != b"infe":
            continue
        v = data[p]
        if v < 2:
            continue
        q = p + 4
        if v == 2:
            item_id, q = struct.unpack_from(">H", data, q)[0], q + 2
        else:
            item_id, q = struct.unpack_from(">I", data, q)[0], q + 4
        q += 2  # item_protection_index
        types[item_id] = data[q:q + 4]
    return types


def _parse_iprp(data: bytes, start: int, end: int) -> tuple[list, dict]:
    """([(type, payload start, end)] of ipco's properties, item ID -> [indices])."""
    props: list = []
    assoc: dict = {}
    for kind, p, e in _boxes(data, start, end):
        if kind == b"ipco":
            props = list(_boxes(data, p, e))
        elif kind == b"ipma":
            version, flags = data[p], int.from_bytes(data[p + 1:p + 4], "big")
            q = p + 4
            count, q = struct.unpack_from(">I", data, q)[0], q + 4
            for _ in range(count):
                if version < 1:
                    item_id, q = struct.unpack_from(">H", data, q)[0], q + 2
                else:
                    item_id, q = struct.unpack_from(">I", data, q)[0], q + 4
                n, q = data[q], q + 1
                indices = assoc.setdefault(item_id, [])
                for _ in range(n):
                    if flags & 1:
                        value, q = struct.unpack_from(">H", data, q)[0] & 0x7FFF, q + 2
                    else:
                        value, q = data[q] & 0x7F, q + 1
                    indices.append(value)
    return props, assoc


def read_heif_info(path: str) -> HeifInfo:
    """Size (as displayed) and EXIF of the HEIF file at *path*."""
    info = HeifInfo()
    try:
        with open(path, "rb") as f:
            if not is_heif(f.read(12)):
                return info
            meta = _read_meta(f)
            if meta is None or len(meta) < 4:
                return info
            boxes = {}
            for kind, p, e in _boxes(meta, 4, len(meta)):  # meta is a FullBox
                boxes.setdefault(kind, (p, e))
            primary = None
            if b"pitm" in boxes:
                p, _e = boxes[b"pitm"]
                primary = (struct.unpack_from(">H", meta, p + 4)[0] if meta[p] == 0
                           else struct.unpack_from(">I", meta, p + 4)[0])
            if b"iprp" in boxes and primary is not None:
                props, assoc = _parse_iprp(meta, *boxes[b"iprp"])
                rotate = 0
                for index in assoc.get(primary, []):
                    if not 1 <= index <= len(props):
                        continue
                    kind, p, e = props[index - 1]
                    if kind == b"ispe" and e - p >= 12:
                        info.width, info.height = struct.unpack_from(">II", meta, p + 4)
                    elif kind == b"irot" and e - p >= 1:
                        rotate = meta[p] & 3
                if rotate in (1, 3) and info.width:
                    info.width, info.height = info.height, info.width
            if b"iinf" in boxes and b"iloc" in boxes:
                types = _parse_iinf(meta, *boxes[b"iinf"])
                exif_ids = [i for i, t in types.items() if t == b"Exif"]
                if exif_ids:
                    method, extents = _parse_iloc(meta, *boxes[b"iloc"]).get(
                        exif_ids[0], (None, []))
                    info.exif = _exif_from(f, meta, boxes, method, extents)
    except (OSError, ValueError, struct.error, IndexError):
        pass
    return info


def _exif_from(f, meta: bytes, boxes: dict, method, extents) -> bytes | None:
    if not extents or sum(n for _o, n in extents) > _MAX_EXIF:
        return None
    chunks = []
    for offset, length in extents:
        if method == 0:          # offsets into the file
            f.seek(offset)
            chunks.append(f.read(length))
        elif method == 1 and b"idat" in boxes:   # offsets into the idat box
            p, e = boxes[b"idat"]
            chunks.append(meta[p + offset:min(e, p + offset + length)])
        else:
            return None
    payload = b"".join(chunks)
    if len(payload) < 4:
        return None
    # A 4-byte offset to the TIFF header, then (usually) "Exif\0\0".
    skip = struct.unpack_from(">I", payload)[0]
    tiff = payload[4 + skip:]
    if tiff.startswith(b"Exif\x00\x00"):
        tiff = tiff[6:]
    return tiff if tiff[:4] in (b"II*\x00", b"MM\x00*") else None
