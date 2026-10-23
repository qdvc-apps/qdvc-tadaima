#!/usr/bin/env python3
"""Regenerate the parity fixture: Tests/TadaimaCoreTests/Fixtures/parity.json.

Runs the GTK 4 edition's pure Python modules (qdvc-tadaima-gtk4/qdvc, next
door in this monorepo) over a fixed set of inputs and records what they
return. TadaimaCoreTests replays every case against the Swift port, so the
two editions agree on paths, the folder tree, the stream's sections and
headings, its geometry and keyboard moves, scanned-folder absorption, what
counts as a photo, cache keys and the camera details shown.

    python3 tools/make_fixtures.py                 # uses ../qdvc-tadaima-gtk4
    python3 tools/make_fixtures.py --python-repo PATH

Needs PyYAML and Pillow (the GTK edition's own dependencies). Never imports
GTK. The output is deterministic, so it diffs cleanly.
"""

import argparse
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Tests" / "TadaimaCoreTests" / "Fixtures" / "parity.json"


def load(python_repo: Path):
    sys.path.insert(0, str(python_repo))
    from PIL import Image  # noqa: F401  (the camera cases need it)
    from PIL.TiffImagePlugin import IFDRational
    from qdvc import cache, config, metadata, naming, stream, tree, ui_prefs
    from qdvc.models import ImageRecord
    return types_ns(cache=cache, config=config, metadata=metadata, naming=naming, stream=stream,
                    tree=tree, ui_prefs=ui_prefs, ImageRecord=ImageRecord, R=IFDRational)


class types_ns:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ----------------------------------------------------------------- inputs
PATHS = [
    "/", "//", "///", "/a//b/./c/../", "/home/james/", "/home/james/Pictures/2024",
    "/a/b/../../..", "/./x", "relative/path/", "", ".", "..", "a/../..", "/a/b/c.jpg",
    "/trailing/slashes///", "/ünïcödé/Fotos/Ålborg", "/a/./b/./c",
]
WITHIN = [
    ("/home/james", "/home/james"), ("/home/james", "/home/james/Pictures/2024"),
    ("/", "/home"), ("/home/james", "/home/jamesbond"), ("/home/james/Pictures", "/home/james"),
    ("/home/james/", "/home/james/x"), ("/a", "/a/../b"),
]
TILDE = [
    ("/home/james/Desktop", "/home/james"), ("/home/james", "/home/james"),
    ("/srv/photos", "/home/james"), ("/home/jamesbond/x", "/home/james"), ("/x/y", "/"),
    ("/home/james/Desktop", None),
]
NAMES = ["a.jpg", "B.JPEG", "c.Png", "d.heic", "e.HEIC", "f.gif", "noext", ".jpg", "x.tar.jpg",
         "photo.jpg.txt", "IMG_0001.JPG"]
HEADERS = {
    "jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01",
    "png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
    "heic": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00",
    "mif1": b"\x00\x00\x00\x1cftypmif1\x00\x00\x00\x00",
    "avif": b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00",
    "appledouble": b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        ",
    "gif": b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x00\x00\x00",
    "short": b"\xff\xd8",
    "empty": b"",
    "text": b"hello, world....",
}
ABSORB = [
    ["/home/james/Pictures/2024", "/home/james/Pictures"],
    ["/a", "/a/", "/b", "/a/c", "/ab"],
    ["/", "/x"],
    ["/x/y", "/x/y/z", "/x/w", "/x/y"],
    [],
]
LIBRARIES = {
    "copenhagen": {
        "roots": ["/home/james/Desktop/holiday-photos-copenhagen", "/home/james/wedding-photos",
                  "/home/james/some-project/project/project-diagrams", "/home/james/empty-root"],
        "files": [
            ("/home/james/Desktop/holiday-photos-copenhagen", ["IMG_3.jpg", "img_1.jpg", "IMG_2.JPG",
                                                               "b.png", "A.png", "a.png", "Z.heic", "c.jpg"]),
            ("/home/james/Desktop/holiday-photos-copenhagen/nyhavn", [f"n{i:02}.jpg" for i in range(14)]),
            ("/home/james/Desktop/holiday-photos-copenhagen/nyhavn/boats", [f"b{i}.jpg" for i in range(9)]),
            ("/home/james/Desktop/holiday-photos-copenhagen/Rundetårn", ["r1.jpg", "r2.jpg", "r3.jpg"]),
            ("/home/james/Desktop/holiday-photos-copenhagen/rundetarn", ["x.jpg"]),
            ("/home/james/Desktop/holiday-photos-copenhagen/Ålborg", ["y.jpg"]),
            ("/home/james/wedding-photos", ["c.png", "d.png"]),
            ("/home/james/wedding-photos/2019/ceremony", [f"w{i}.jpg" for i in range(25)]),
            ("/home/james/some-project/project/project-diagrams", ["e.jpg"]),
        ],
    },
    "root": {
        "roots": ["/"],
        "files": [("/", ["top.jpg"]), ("/srv/photos", ["p1.jpg", "p2.jpg"])],
    },
}
FOCUSES = {
    "copenhagen": ["/", "/home/james", "/home/james/Desktop/holiday-photos-copenhagen",
                   "/home/james/Desktop/holiday-photos-copenhagen/nyhavn", "/home/james/wedding-photos/2019",
                   "/home/james/empty-root"],
    "root": ["/", "/srv"],
}
HOME = "/home/james"
LAYOUT_SIZES = [(1000, 150), (520, 150), (1400, 90), (300, 420), (174, 150)]
MOVES = ["left", "right", "up", "down", "page_up", "page_down", "home", "end"]


def tree_json(node):
    return {
        "path": node.path, "name": node.name, "count": node.image_count,
        "scanned": node.is_scanned_root,
        "images": [r.name for r in node.own_images],
        "children": [tree_json(c) for c in node.children],
    }


def build_library(q, spec):
    records = []
    for folder, names in spec["files"]:
        for name in names:
            path = os.path.join(folder, name)
            records.append(q.ImageRecord(path=path, mtime=0.0, size=1, key=path,
                                         thumb_w=500, thumb_h=375))
    return records, q.tree.build_tree(records, spec["roots"])


def layout_cases(q, s, rng):
    out = []
    for width, slot in LAYOUT_SIZES:
        L = q.stream.StreamLayout(s, width, slot)
        other = q.stream.StreamLayout(s, max(200, width // 2 + 37), slot + 30)
        total = len(s)
        flats = sorted({0, total - 1, total // 2, *[rng.randrange(total) for _ in range(6)]}) if total else []
        points = []
        for f in flats:
            x, y, w, _ = L.cell(f)
            points += [(x + 1, y + 1), (x + w - 0.5, y + w - 0.5), (x + w + 2, y + 5), (x + 5, y + w + 3)]
        points += [(rng.uniform(-10, width + 10), rng.uniform(-10, L.height + 10)) for _ in range(16)]
        ys = [rng.uniform(-20, L.height + 20) for _ in range(12)] + [float(y) for y in L.header_ys[:6]]
        spans = [(a, a + rng.uniform(50, 900)) for a in (rng.uniform(-100, L.height) for _ in range(8))]
        out.append({
            "width": width, "slot": slot, "columns": L.columns, "height": L.height,
            "header_ys": L.header_ys, "rows_ys": L.rows_ys, "end_ys": L.end_ys, "row_counts": L.row_counts,
            "cells": [[f, *L.cell(f)[:2]] for f in flats],
            "photo_at": [[x, y, L.photo_at(x, y)] for x, y in points],
            "section_at": [[y, L.section_at(y)] for y in ys],
            "sections_between": [[a, b, [r.start, r.stop]] for a, b in spans
                                 for r in [L.sections_between(a, b)]],
            "photos_between": [[a, b, L.photos_between(a, b)] for a, b in spans],
            "neighbours": [[f, m, pr, L.neighbour(f, m, pr)] for f in flats for m in MOVES
                           for pr in ([1, 3] if m.startswith("page") else [1])],
            "anchors": [[y, list(a) if a else None, other.y_for_anchor(a), other.width, other.slot]
                        for y in ys for a in [L.anchor_at(y)]],
        })
    return out


def camera_cases(q):
    R = q.R
    from PIL import Image

    def exif(ifd0=None, sub=None, gps=None):
        ex = Image.Exif()
        for k, v in (ifd0 or {}).items():
            ex[k] = v
        if sub:
            ex[0x8769] = sub
        if gps:
            ex[0x8825] = gps
        loaded = Image.Exif()
        loaded.load(ex.tobytes())          # as read from a file
        return loaded

    iphone_gps = {1: "N", 2: (R(55), R(40), R(3456, 100)), 3: "E", 4: (R(12), R(34), R(5)),
                  5: b"\x00", 6: R(12)}
    cases = [
        exif({0x010F: "Apple", 0x0110: "iPhone 15 Pro"},
             {0x9003: "2024:05:31 18:42:07", 0x829A: R(1, 250), 0x829D: R(178, 100), 0x8827: 64,
              0x9204: R(-1, 3), 0x9209: 16, 0x920A: R(6765, 1000), 0xA405: 24,
              0xA434: "iPhone 15 Pro back triple camera 6.765mm f/1.78"}, iphone_gps),
        exif({0x010F: "Canon", 0x0110: "Canon EOS R5"},
             {0x9204: R(0, 1), 0x9209: 0x20, 0x829A: R(2, 1), 0x829D: R(0, 0)}),
        exif({0x010F: "NIKON CORPORATION", 0x0110: "NIKON Z 6"},
             {0x829A: R(1, 3), 0x829D: R(8, 1), 0x8827: 6400, 0x9204: R(2, 3), 0x9209: 25,
              0x920A: R(50, 1), 0xA405: 50, 0xA434: "NIKKOR Z 50mm f/1.8 S"},
             {1: "S", 2: (R(33), R(51), R(3585, 100)), 3: "W", 4: (R(70), R(40), R(1, 1)),
              5: b"\x01", 6: R(35, 10)}),
        exif({0x0110: "X100V"}, {0x829A: R(2, 5), 0xA405: 35, 0x8827: 160}),
        exif({0x010F: "FUJIFILM"}, {0x829A: R(5, 2), 0x920A: R(23, 1), 0xA405: 35}),
        exif({}, {0x9209: 1, 0x829A: R(1, 8000)}, {1: "N", 2: (R(0), R(0), R(0)), 3: "E",
                                                   4: (R(0), R(0), R(0))}),
        exif({0x010F: "  Sony  ", 0x0110: "ILCE-7M3 "}, {0x920A: R(853, 10), 0x829D: R(28, 10)},
             {1: "N", 2: (R(48), R(51), R(2448, 100)), 3: "E", 4: (R(2), R(17), R(4044, 100))}),
        exif({}, {}),
    ]
    out = []
    for ex in cases:
        rows, location = q.metadata.camera_details(ex)
        sub = ex.get_ifd(0x8769) or {}
        gps = ex.get_ifd(0x8825) or {}
        num = q.metadata._num
        lat = q.metadata._gps_degrees(gps.get(2), gps.get(1))
        lon = q.metadata._gps_degrees(gps.get(4), gps.get(3))
        values = {
            "make": q.metadata._text(ex.get(0x010F)) or None,
            "model": q.metadata._text(ex.get(0x0110)) or None,
            "lensModel": q.metadata._text(sub.get(0xA434)) or None,
            "focalLength": num(sub.get(0x920A)), "focalLength35mm": num(sub.get(0xA405)),
            "fNumber": num(sub.get(0x829D)), "exposureTime": num(sub.get(0x829A)),
            "iso": num(sub.get(0x8827)), "exposureBias": num(sub.get(0x9204)),
            "flash": sub.get(0x9209) if isinstance(sub.get(0x9209), int) else None,
            "latitude": lat, "longitude": lon, "altitude": num(gps.get(6)),
            "altitudeBelowSeaLevel": gps.get(5) in (1, b"\x01"),
        }
        out.append({"values": values, "rows": [list(r) for r in rows],
                    "location": list(location) if location else None})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python-repo", type=Path, default=ROOT.parent / "qdvc-tadaima-gtk4",
                    help="the GTK 4 edition's folder (default: ../qdvc-tadaima-gtk4)")
    args = ap.parse_args()
    q = load(args.python_repo.resolve())
    rng = random.Random(20261023)

    libraries, streams = {}, []
    for name, spec in LIBRARIES.items():
        records, root = build_library(q, spec)
        libraries[name] = {"roots": spec["roots"], "records": [r.path for r in records],
                           "tree": tree_json(root)}
        for focus in FOCUSES[name]:
            node = q.tree.find_node(root, focus) or root
            s = q.stream.build_stream(node, home=HOME)
            streams.append({
                "library": name, "focus": focus, "home": HOME,
                "sections": [{"path": x.path, "name": x.name, "context": x.context,
                              "records": [r.path for r in x.records]} for x in s.sections],
                "first_within": [[p, s.first_section_within(p)] for p in
                                 [focus, "/home", HOME, "/nowhere",
                                  "/home/james/Desktop/holiday-photos-copenhagen/nyhavn"]],
                "layouts": layout_cases(q, s, rng),
            })

    md, up = q.metadata, q.ui_prefs
    fixture = {
        "generator": "tools/make_fixtures.py (from qdvc-tadaima-gtk4/qdvc)",
        "paths": [{"in": p, "normpath": os.path.normpath(p), "dirname": os.path.dirname(p),
                   "basename": os.path.basename(p)} for p in PATHS],
        "within": [[a, d, q.tree.is_within(a, d)] for a, d in WITHIN],
        "tilde": [[p, h, q.stream._tilde(p, h)] for p, h in TILDE],
        "cache_keys": [[p, q.naming.cache_key(p)] for p in PATHS if p.startswith("/")],
        "extensions": [[n, q.naming.is_supported_image(n)] for n in NAMES],
        "sniff": [[k, v.hex(), _sniff(q, v)] for k, v in HEADERS.items()],
        "absorb": [[case, q.config.normalize_scanned_folders(case)] for case in ABSORB],
        "libraries": libraries,
        "streams": streams,
        "reveal": [[f, t, q.tree.reveal_chain(f, t)] for f, t in [
            ("/", "/home/james/Pictures/2024"), ("/home/james", "/home/james/Pictures/2024"),
            ("/home/james", "/home/james/Pictures"), ("/home/james", "/home/james"),
            ("/home/james", "/srv/photos"), ("/home/james", "/home/jamesbond/x")]],
        "forget": [[sorted(e), p, sorted(_forget(q, e, p))] for e, p in [
            ({"/", "/home", "/home/james", "/home/james/Pictures", "/home/jamesbond"}, "/home/james"),
            ({"/a", "/a/b"}, "/"), (set(), "/x")]],
        "fit_within": [[w, h, b, list(up.fit_within(w, h, b))] for w, h, b in [
            (500, 375, 150), (375, 500, 150), (100, 50, 150), (0, 0, 150), (333, 500, 150),
            (500, 333, 90), (500, 375, 420), (1, 9999, 150), (225, 150, 150)]],
        "counts": [[n, up.format_count(n)] for n in [0, 7, 999, 1000, 12345, 1234567, -4321]],
        "sizes": [[n, md.human_size(n)] for n in [0, 1, 1023, 1024, 1536, 10 ** 6, 5 * 1024 ** 3]],
        "exposure": [[t, md.format_exposure_time(t)] for t in
                     [1 / 250, 1 / 60, 0.3, 0.4, 2.5, 1.0, 1 / 3, 1 / 8000, 30.0, 0.0125]],
        "camera_names": [[m, n, md.format_camera(m, n)] for m, n in [
            ("Apple", "iPhone 15 Pro"), ("Canon", "Canon EOS R5"), ("NIKON CORPORATION", "NIKON Z 6"),
            ("", "X100V"), ("FUJIFILM", ""), ("  Sony ", " ILCE-7M3")]],
        "coordinates": [[a, b, md.format_coordinates(a, b)] for a, b in [
            (55.676267, 12.568056), (-33.8599, -70.6669), (0.0, 0.0), (48.85680, 2.29457)]],
        "trim": [[x, p, md._trim(x, p)] for x, p in [
            (6.765, 1), (1.78, 1), (24.0, 0), (2.0, 1), (0.333, 1), (0.05, 1), (10.25, 1)]],
        "camera": camera_cases(q),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    n = sum(len(x["layouts"]) for x in streams)
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(streams)} streams, {n} layouts, "
          f"{len(fixture['camera'])} camera cases")


def _sniff(q, head: bytes) -> bool:
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(head)
    try:
        return q.cache.looks_like_image(fh.name)
    finally:
        os.unlink(fh.name)


def _forget(q, expanded, path):
    e = set(expanded)
    q.tree.forget_subtree(e, path)
    return e


if __name__ == "__main__":
    main()
