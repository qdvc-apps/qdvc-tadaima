#!/usr/bin/env python3
"""Generate the app icon: Resources/AppIcon.svg (vector master) and
Resources/AppIcon.icns (every macOS size, rendered natively from the vector).

The design is the GTK 4 edition's icon (../qdvc-tadaima-gtk4/tools/make_icon.py)
in the macOS family's style: a white photo print filling the whole icon shape,
holding a landscape — a blue sky, a yellow sun with its halo, a far and a near
green hill — with a glossy orange badge carrying a white house, as "tadaima"
is what you call out when you come home. The landscape and badge are placed as
in the GTK icon, rescaled from its print to the square macOS tile. (The GTK
icon's second, tilted print is left out: here the print is the tile itself,
so there is nothing for it to peek out from behind.)

Geometry follows Apple's macOS icon template: a 1024x1024 canvas holding an
824x824 tile (100 px transparent margin) with a 185.4 px continuous-curvature
("squircle") corner, plus a soft drop shadow. The corner is the curve UIKit
draws for continuous corners, as reverse-engineered and published by PaintCode
(https://www.paintcodeapp.com/blogpost/code-for-ios-7-rounded-rectangles).

Usage:

    python3 tools/make_icon.py            # writes Resources/AppIcon.{svg,icns}
    python3 tools/make_icon.py --preview  # also writes build/icon-preview.png

Requires rsvg-convert (macOS: `brew install librsvg`; Debian/Ubuntu:
`apt install librsvg2-bin`). No other dependencies; the .icns is written
directly, so Apple's iconutil is not needed.
"""

import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "Resources"

# ---------------------------------------------------------------------------
# Palette: the GTK icon's Adwaita colours, deepened a little for the macOS
# style's gradients.

PAPER_TOP, PAPER_BOTTOM = "#FFFFFF", "#F1F2F4"
SKY_TOP, SKY_BOTTOM = "#4A8FE6", "#A8CBF5"           # Adwaita blue 2 → 1
SUN, SUN_HALO = "#F6D32D", "#F8E45C"                 # Adwaita yellow 3, 2
FAR_HILL_TOP, FAR_HILL_BOTTOM = "#57E389", "#33D17A"  # Adwaita green 3 → 4
NEAR_HILL_TOP, NEAR_HILL_BOTTOM = "#2EC27E", "#1F8A55"  # green 4/5, deepened
BADGE_LIGHT, BADGE_MID, BADGE_DARK = "#FFC27A", "#F57C14", "#B84000"  # Adwaita orange
FEATURES = "#FFFFFF"

# ---------------------------------------------------------------------------
# Apple's template geometry.

TILE_ORIGIN, TILE_SIZE, CORNER_RADIUS = 100, 824, 185.4
SHADOW_BLUR_RADIUS, SHADOW_OFFSET_Y, SHADOW_OPACITY = 28, 12, 0.5

# Continuous-corner segments around the top-right corner, in units of the
# radius: (distance from the right edge, distance from the top edge).
_CORNER = [
    ("L", (1.52866471, 0.0)),
    ("C", (1.08849323, 0.0), (0.86840689, 0.0), (0.66993427, 0.06549600)),
    ("L", (0.63149399, 0.07491100)),
    ("C", (0.37282392, 0.16905899), (0.16906013, 0.37282401), (0.07491176, 0.63149399)),
    ("C", (0.0, 0.86840701), (0.0, 1.08849299), (0.0, 1.52866483)),
]


def continuous_rounded_rect(x, y, w, h, r):
    """SVG path of a rounded rectangle with Apple's continuous corners."""
    r = min(r, min(w, h) / 2 / 1.52866483)
    corners = [
        lambda u, v: (x + w - u * r, y + v * r),        # top-right
        lambda u, v: (x + w - v * r, y + h - u * r),    # bottom-right
        lambda u, v: (x + u * r, y + h - v * r),        # bottom-left
        lambda u, v: (x + v * r, y + u * r),            # top-left
    ]

    def fmt(p):
        return f"{p[0]:.2f},{p[1]:.2f}"

    d = [f"M{fmt((x + 1.52866483 * r, y))}"]
    for corner in corners:
        for seg in _CORNER:
            if seg[0] == "L":
                d.append("L" + fmt(corner(*seg[1])))
            else:
                d.append("C" + " ".join(fmt(corner(*p)) for p in seg[1:]))
    d.append("Z")
    return " ".join(d)


TILE = continuous_rounded_rect(TILE_ORIGIN, TILE_ORIGIN, TILE_SIZE, TILE_SIZE, CORNER_RADIUS)

# The photo on the print: a white border all round, a little deeper at the
# bottom, as on a print.
PHOTO_X, PHOTO_Y, PHOTO_W, PHOTO_H, PHOTO_R = 166, 166, 692, 616, 70
PHOTO = continuous_rounded_rect(PHOTO_X, PHOTO_Y, PHOTO_W, PHOTO_H, PHOTO_R)

# The GTK icon's photo is 80 x 56 units; its landscape, in those units.
GTK_W, GTK_H = 80, 56


def _p(u, v):
    """A point of the GTK icon's photo (units) on this one (pixels)."""
    return PHOTO_X + u * PHOTO_W / GTK_W, PHOTO_Y + v * PHOTO_H / GTK_H


def _hill(points):
    """A hill: M + two cubic curves across, then down and back (GTK path)."""
    (m, c1, c2) = points
    s = f"M{_fmt(_p(*m))} C{_fmt(_p(*c1[0]))} {_fmt(_p(*c1[1]))} {_fmt(_p(*c1[2]))} "
    s += f"C{_fmt(_p(*c2[0]))} {_fmt(_p(*c2[1]))} {_fmt(_p(*c2[2]))} "
    s += f"L{_fmt(_p(GTK_W + 2, GTK_H + 2))} L{_fmt(_p(-2, GTK_H + 2))} Z"
    return s


def _fmt(p):
    return f"{p[0]:.1f},{p[1]:.1f}"


FAR_HILL = _hill(((-2, 44), ((14, 30), (30, 28), (46, 36)), ((58, 42), (70, 30), (GTK_W + 2, 32))))
NEAR_HILL = _hill(((-2, 50), ((18, 40), (40, 40), (60, 48)), ((68, 51), (74, 52), (GTK_W + 2, 50))))
SUN_CENTRE = _p(22, 18)
SUN_R = 7 * PHOTO_W / GTK_W

# The badge: over the photo's lower right, inside the tile.
BADGE_CX, BADGE_CY, BADGE_R = 752, 770, 128


def _badge(cx, cy, r):
    """A glossy orange sphere with a white house, in a white ring that parts
    it from the photo beneath (as the GTK badge, and progress-mac's)."""
    k = r / 21  # the GTK badge's radius is 21 units
    house = (f"M{cx},{cy - 12 * k:.1f} L{cx + 13 * k:.1f},{cy} L{cx + 9 * k:.1f},{cy} "
             f"L{cx + 9 * k:.1f},{cy + 11 * k:.1f} L{cx + 3 * k:.1f},{cy + 11 * k:.1f} "
             f"L{cx + 3 * k:.1f},{cy + 4 * k:.1f} L{cx - 3 * k:.1f},{cy + 4 * k:.1f} "
             f"L{cx - 3 * k:.1f},{cy + 11 * k:.1f} L{cx - 9 * k:.1f},{cy + 11 * k:.1f} "
             f"L{cx - 9 * k:.1f},{cy} L{cx - 13 * k:.1f},{cy} Z")
    return [
        f'<circle cx="{cx}" cy="{cy}" r="{r + 14}" fill="#FFFFFF"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#face)"/>',
        f'<path d="{house}" fill="{FEATURES}" stroke="{FEATURES}" stroke-width="{2 * k:.1f}" '
        'stroke-linejoin="round" transform="translate(0,6)"/>',
        # specular highlight
        f'<ellipse cx="{cx - r * 0.30:.1f}" cy="{cy - r * 0.56:.1f}" rx="{r * 0.40:.1f}" '
        f'ry="{r * 0.17:.1f}" fill="#FFFFFF" opacity="0.42"/>',
    ]


def icon_svg():
    sigma = SHADOW_BLUR_RADIUS / 2
    sx, sy = SUN_CENTRE
    defs = [
        '<linearGradient id="paper" x1="0" y1="0" x2="0.15" y2="1">'
        f'<stop offset="0" stop-color="{PAPER_TOP}"/><stop offset="1" stop-color="{PAPER_BOTTOM}"/></linearGradient>',
        '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{SKY_TOP}"/><stop offset="0.75" stop-color="{SKY_BOTTOM}"/></linearGradient>',
        '<linearGradient id="far" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0.45" stop-color="{FAR_HILL_TOP}"/><stop offset="1" stop-color="{FAR_HILL_BOTTOM}"/>'
        '</linearGradient>',
        '<linearGradient id="near" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0.6" stop-color="{NEAR_HILL_TOP}"/><stop offset="1" stop-color="{NEAR_HILL_BOTTOM}"/>'
        '</linearGradient>',
        '<radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0.55" stop-color="{SUN_HALO}" stop-opacity="0.75"/>'
        f'<stop offset="1" stop-color="{SUN_HALO}" stop-opacity="0"/></radialGradient>',
        '<radialGradient id="face" cx="0.38" cy="0.30" r="0.8">'
        f'<stop offset="0" stop-color="{BADGE_LIGHT}"/><stop offset="0.55" stop-color="{BADGE_MID}"/>'
        f'<stop offset="1" stop-color="{BADGE_DARK}"/></radialGradient>',
        '<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#FFFFFF" stop-opacity="0.28"/>'
        '<stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>',
        '<linearGradient id="edge" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#000000" stop-opacity="0.04"/>'
        '<stop offset="1" stop-color="#000000" stop-opacity="0.12"/></linearGradient>',
        f'<clipPath id="clip"><path d="{TILE}"/></clipPath>',
        f'<clipPath id="photoclip"><path d="{PHOTO}"/></clipPath>',
        # Apple template shadow: black, 28 px blur radius, 12 px down, 50 %.
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">'
        f'<feGaussianBlur in="SourceAlpha" stdDeviation="{sigma}"/><feOffset dy="{SHADOW_OFFSET_Y}"/>'
        f'<feComponentTransfer><feFuncA type="linear" slope="{SHADOW_OPACITY}"/></feComponentTransfer>'
        '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
        # A soft orange-brown shadow under the badge, lifting it off the photo.
        '<filter id="objshadow" x="-30%" y="-30%" width="160%" height="160%">'
        '<feGaussianBlur in="SourceAlpha" stdDeviation="12"/><feOffset dx="0" dy="12"/>'
        f'<feFlood flood-color="{BADGE_DARK}" flood-opacity="0.35"/><feComposite operator="in" in2="SourceAlpha"/>'
        '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
    ]
    parts = [
        f'<g filter="url(#shadow)"><path d="{TILE}" fill="url(#paper)"/></g>',
        '<g clip-path="url(#clip)">',
        # The photo: sky, sun, far hill, near hill, a light sheen.
        '<g clip-path="url(#photoclip)">',
        f'<rect x="{PHOTO_X}" y="{PHOTO_Y}" width="{PHOTO_W}" height="{PHOTO_H}" fill="url(#sky)"/>',
        f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{SUN_R * 1.75:.1f}" fill="url(#halo)"/>',
        f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{SUN_R:.1f}" fill="{SUN}"/>',
        f'<path d="{FAR_HILL}" fill="url(#far)"/>',
        f'<path d="{NEAR_HILL}" fill="url(#near)"/>',
        f'<rect x="{PHOTO_X}" y="{PHOTO_Y}" width="{PHOTO_W}" height="{PHOTO_H}" fill="url(#sheen)"/>',
        '</g>',
        # A fine edge where the photo meets the paper.
        f'<path d="{PHOTO}" fill="none" stroke="#000000" stroke-opacity="0.10" stroke-width="3"/>',
        '<g filter="url(#objshadow)">', *_badge(BADGE_CX, BADGE_CY, BADGE_R), '</g>',
        '</g>',
        # A hairline edge, so the white tile holds its shape on white.
        f'<path d="{TILE}" fill="none" stroke="url(#edge)" stroke-width="3"/>',
    ]
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">'
            f'<defs>{"".join(defs)}</defs>{"".join(parts)}</svg>\n')


# ---------------------------------------------------------------------------
# .icns writing. These are the element types `iconutil` emits for a standard
# .iconset; all of them hold PNG data.

ICNS_ELEMENTS = [
    ("icp4", 16), ("ic11", 32), ("icp5", 32), ("ic12", 64),
    ("ic07", 128), ("ic13", 256), ("ic08", 256), ("ic14", 512),
    ("ic09", 512), ("ic10", 1024),
]


def render_png(svg_path, size, out_path):
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(svg_path), "-o", str(out_path)],
                   check=True)


def write_icns(pngs_by_size, out_path):
    chunks = b""
    for code, size in ICNS_ELEMENTS:
        data = pngs_by_size[size]
        chunks += code.encode("ascii") + struct.pack(">I", len(data) + 8) + data
    out_path.write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)


def main():
    parser = argparse.ArgumentParser(description="Generate Resources/AppIcon.svg and AppIcon.icns.")
    parser.add_argument("--preview", action="store_true",
                        help="also write build/icon-preview.png (1024 px)")
    args = parser.parse_args()

    try:
        subprocess.run(["rsvg-convert", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        sys.exit("error: rsvg-convert not found (macOS: brew install librsvg)")

    RESOURCES.mkdir(exist_ok=True)
    svg_path = RESOURCES / "AppIcon.svg"
    svg_path.write_text(icon_svg())

    with tempfile.TemporaryDirectory() as tmp:
        pngs = {}
        for size in sorted({s for _, s in ICNS_ELEMENTS}):
            png = Path(tmp) / f"icon_{size}.png"
            render_png(svg_path, size, png)
            pngs[size] = png.read_bytes()
        write_icns(pngs, RESOURCES / "AppIcon.icns")

    if args.preview:
        (ROOT / "build").mkdir(exist_ok=True)
        render_png(svg_path, 1024, ROOT / "build" / "icon-preview.png")
    print(f"Wrote {svg_path.relative_to(ROOT)} and Resources/AppIcon.icns")


if __name__ == "__main__":
    main()
