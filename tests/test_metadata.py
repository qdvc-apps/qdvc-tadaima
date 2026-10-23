"""Photo metadata: HEIC size/EXIF from the container, and the camera details
shown in the information sidebar.

The HEIC files here are built box by box (ISO/IEC 23008-12 layout: ftyp, meta
with hdlr/pitm/iinf/iloc/iprp, mdat) around a dummy image item. Pillow can't
decode them, which is exactly the case the container reader exists for.
"""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402
from PIL.TiffImagePlugin import IFDRational as R  # noqa: E402

from qdvc.heif import read_heif_info  # noqa: E402
from qdvc.metadata import (  # noqa: E402
    camera_details,
    format_camera,
    format_exposure_time,
    read_metadata,
)


def _exif():
    ex = Image.Exif()
    ex[0x010F] = "Apple"
    ex[0x0110] = "iPhone 15 Pro"
    ex[0x0132] = "2024:06:01 10:00:00"          # DateTime: when it was last edited
    ex[0x8769] = {
        0x9003: "2024:05:31 18:42:07",          # DateTimeOriginal: when it was taken
        0x829A: R(1, 250), 0x829D: R(178, 100), 0x8827: 64,
        0x9204: R(-1, 3), 0x9209: 16, 0x920A: R(6765, 1000), 0xA405: 24,
        0xA434: "iPhone 15 Pro back triple camera 6.765mm f/1.78",
    }
    ex[0x8825] = {
        1: "N", 2: (R(55), R(40), R(3456, 100)), 3: "E", 4: (R(12), R(34), R(5)),
        5: b"\x00", 6: R(12),
    }
    return ex


EXPECTED_ROWS = [
    ("Camera", "Apple iPhone 15 Pro"),
    ("Lens", "iPhone 15 Pro back triple camera 6.765mm f/1.78"),
    ("Focal length", "6.8 mm (24 mm in 35 mm terms)"),
    ("Aperture", "ƒ/1.8"),
    ("Shutter speed", "1/250 s"),
    ("ISO", "64"),
    ("Exposure compensation", "−0.3 EV"),
    ("Flash", "Did not fire"),
    ("Location", "55.67627° N, 12.56806° E · 12 m"),
]


# ------------------------------------------------------------- HEIF builder
def _box(kind, payload):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _full(kind, version, flags, payload):
    return _box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def make_heif(width, height, rotate=0, tiff=None, in_idat=False, ipma_flags=0,
              iloc_version=1):
    """Bytes of a minimal HEIF file: item 1 is the (dummy) primary image,
    item 2 the EXIF block if *tiff* is given."""
    exif_payload = struct.pack(">I", 6) + b"Exif\x00\x00" + tiff if tiff else b""
    image_data = b"\x00" * 32
    items = [1] + ([2] if tiff else [])

    infes = _full(b"infe", 2, 0, struct.pack(">HH", 1, 0) + b"hvc1\x00")
    if tiff:
        infes += _full(b"infe", 2, 0, struct.pack(">HH", 2, 0) + b"Exif\x00")
    iinf = _full(b"iinf", 0, 0, struct.pack(">H", len(items)) + infes)
    props = _full(b"ispe", 0, 0, struct.pack(">II", width, height))
    assoc = [(1, False)]
    if rotate:
        props += _box(b"irot", bytes([rotate]))
        assoc.append((2, True))
    ipco = _box(b"ipco", props)
    if ipma_flags & 1:
        entries = b"".join(struct.pack(">H", i | (0x8000 if e else 0)) for i, e in assoc)
    else:
        entries = bytes(i | (0x80 if e else 0) for i, e in assoc)
    ipma = _full(b"ipma", 0, ipma_flags,
                 struct.pack(">I", 1) + struct.pack(">HB", 1, len(assoc)) + entries)
    iprp = _box(b"iprp", ipco + ipma)
    hdlr = _full(b"hdlr", 0, 0, b"\x00" * 4 + b"pict" + b"\x00" * 13)
    pitm = _full(b"pitm", 0, 0, struct.pack(">H", 1))
    idat = _box(b"idat", exif_payload) if (tiff and in_idat) else b""

    def iloc(offsets):
        id_fmt, count_fmt = (">H", ">H") if iloc_version < 2 else (">I", ">I")
        body = bytes([0x44, 0x00]) + struct.pack(count_fmt, len(items))
        for item, (method, off, length) in zip(items, offsets):
            body += struct.pack(id_fmt, item) + struct.pack(">HH", method, 0)
            body += struct.pack(">H", 1) + struct.pack(">II", off, length)
        return _full(b"iloc", iloc_version, 0, body)

    ftyp = _box(b"ftyp", b"heic" + b"\x00\x00\x00\x00" + b"mif1heic")

    def build(offsets):
        meta = _full(b"meta", 0, 0, hdlr + pitm + iloc(offsets) + iinf + iprp + idat)
        return ftyp + meta

    placeholder = [(0, 0, 0)] * len(items)
    head = build(placeholder)
    mdat_data_start = len(head) + 8
    offsets = [(0, mdat_data_start, len(image_data))]
    if tiff:
        offsets.append((1, 0, len(exif_payload)) if in_idat
                       else (0, mdat_data_start + len(image_data), len(exif_payload)))
    head = build(offsets)
    assert len(head) + 8 == mdat_data_start
    mdat = _box(b"mdat", image_data + (b"" if in_idat else exif_payload))
    return head + mdat


def _write(d, name, data):
    path = os.path.join(d, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


# -------------------------------------------------------------------- tests
def test_heif_size_and_rotation():
    with tempfile.TemporaryDirectory() as d:
        info = read_heif_info(_write(d, "a.heic", make_heif(4032, 3024)))
        assert (info.width, info.height, info.exif) == (4032, 3024, None)
        info = read_heif_info(_write(d, "b.heic", make_heif(4032, 3024, rotate=3)))
        assert (info.width, info.height) == (3024, 4032)   # portrait, as displayed
        info = read_heif_info(_write(d, "c.heic", make_heif(800, 600, rotate=2,
                                                            ipma_flags=1, iloc_version=2)))
        assert (info.width, info.height) == (800, 600)


def test_heif_exif_in_mdat_and_in_idat():
    tiff = _exif().tobytes()[6:]
    with tempfile.TemporaryDirectory() as d:
        for in_idat in (False, True):
            info = read_heif_info(_write(d, f"x{in_idat}.heic",
                                         make_heif(4032, 3024, tiff=tiff, in_idat=in_idat)))
            assert info.exif == tiff


def test_heic_metadata_without_pillow_heif():
    tiff = _exif().tobytes()[6:]
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, "IMG_0001.HEIC", make_heif(4032, 3024, rotate=1, tiff=tiff))
        md = read_metadata(path)
    assert md.dimensions_str == "3024 × 4032 px"
    assert md.date_is_exif and md.date_taken.isoformat() == "2024-05-31T18:42:07"
    assert md.camera == EXPECTED_ROWS
    assert md.location is not None and abs(md.location[0] - 55.676267) < 1e-5


def test_broken_files_degrade_quietly():
    with tempfile.TemporaryDirectory() as d:
        good = make_heif(4032, 3024, tiff=_exif().tobytes()[6:])
        for name, data in (("junk.heic", good[:12] + b"\xff" * 64), ("empty.heic", b"")):
            md = read_metadata(_write(d, name, data))
            assert md.width is None and md.camera == [] and not md.date_is_exif
        # Cut off inside the image data: the header still gives the size.
        md = read_metadata(_write(d, "trunc.heic", good[:300]))
        assert (md.width, md.height) == (4032, 3024) and md.camera == []


def test_jpeg_camera_details_and_date_taken():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "shot.jpg")
        Image.new("RGB", (640, 480)).save(path, exif=_exif())
        md = read_metadata(path)
    assert md.camera == EXPECTED_ROWS
    # DateTimeOriginal (in the Exif sub-IFD) wins over DateTime (in IFD0),
    # which editing software rewrites.
    assert md.date_taken.isoformat() == "2024-05-31T18:42:07"


def test_details_left_out_when_absent_or_meaningless():
    ex = Image.Exif()
    ex[0x010F] = "Canon"
    ex[0x0110] = "Canon EOS R5"
    ex[0x8769] = {0x9204: R(0, 1), 0x9209: 0x20, 0x829A: R(2, 1), 0x829D: R(0, 0)}
    loaded = Image.Exif()
    loaded.load(ex.tobytes())          # as read from a file
    rows, location = camera_details(loaded)
    assert rows == [("Camera", "Canon EOS R5"), ("Shutter speed", "2 s")]
    assert location is None
    assert camera_details(Image.Exif()) == ([], None)


def test_formatters():
    assert format_camera("NIKON CORPORATION", "NIKON Z 6") == "NIKON Z 6"
    assert format_camera("Apple", "iPhone 15 Pro") == "Apple iPhone 15 Pro"
    assert format_camera("", "X100V") == "X100V"
    assert format_exposure_time(1 / 60) == "1/60 s"
    assert format_exposure_time(0.3) == "1/3 s"
    assert format_exposure_time(2.5) == "2.5 s"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ALL METADATA TESTS PASSED ({len(tests)})")
