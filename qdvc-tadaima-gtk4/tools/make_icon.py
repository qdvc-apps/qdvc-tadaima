#!/usr/bin/env python3
"""Generate the app icons in the GNOME (Adwaita) style.

    python3 tools/make_icon.py            # writes data/icons/hicolor/...
    python3 tools/make_icon.py --preview  # also build/icon-preview.png (needs rsvg-convert)

The design follows the QDVC family on GNOME (Progress's spreadsheet card,
GTD EML's envelope): one light object with a soft darker bottom edge (the
HIG's "thickness" cue), lit from above, plus a round badge at the bottom right.
Tadaima's object is a pair of photo prints, the front one a landscape (sky, a
sun and two hills), and its badge is warm orange with a white house: tadaima
is what you call out when you come home. 128 × 128 canvas, per the GNOME app
icon grid, on the Adwaita palette.

Writes:
  data/icons/hicolor/scalable/apps/qdvc.Tadaima.svg
  data/icons/hicolor/symbolic/apps/qdvc.Tadaima-symbolic.svg
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ID = "qdvc.Tadaima"

# Adwaita palette.
LIGHT_1, LIGHT_2, LIGHT_3, LIGHT_4, LIGHT_5 = (
    "#ffffff", "#f6f5f4", "#deddda", "#c0bfbc", "#9a9996")
BLUE_1, BLUE_2 = "#99c1f1", "#62a0ea"
GREEN_3, GREEN_4, GREEN_5 = "#57e389", "#33d17a", "#26a269"
YELLOW_2, YELLOW_3 = "#f8e45c", "#f6d32d"
ORANGE_2, ORANGE_4, ORANGE_5 = "#ffa348", "#e66100", "#c64600"

# The front print and the photo inside its white border.
PX, PY, PW, PH, PR = 12, 26, 94, 74, 6
BORDER = 7
IX, IY, IW, IH = PX + BORDER, PY + BORDER, PW - 2 * BORDER, PH - 2 * BORDER - 4


def app_icon() -> str:
    bx, by, br = 96, 94, 21  # badge
    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">',
        "<defs>",
        '<linearGradient id="side" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{LIGHT_4}"/><stop offset="1" stop-color="{LIGHT_5}"/>'
        "</linearGradient>",
        '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{BLUE_2}"/><stop offset="1" stop-color="{BLUE_1}"/>'
        "</linearGradient>",
        '<linearGradient id="badge" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{ORANGE_2}"/><stop offset="1" stop-color="{ORANGE_4}"/>'
        "</linearGradient>",
        f'<clipPath id="photo"><rect x="{IX}" y="{IY}" width="{IW}" height="{IH}" rx="2"/>'
        "</clipPath>",
        "</defs>",
        # The print behind, tilted, with its own bottom edge.
        f'<g transform="rotate(-9 64 60)">',
        f'<rect x="{PX + 10}" y="{PY - 8 + 4}" width="{PW}" height="{PH}" rx="{PR}" '
        'fill="url(#side)"/>',
        f'<rect x="{PX + 10}" y="{PY - 8}" width="{PW}" height="{PH}" rx="{PR}" '
        f'fill="{LIGHT_2}"/>',
        f'<rect x="{PX + 10 + BORDER}" y="{PY - 8 + BORDER}" width="{IW}" height="{IH}" '
        f'rx="2" fill="{LIGHT_3}"/>',
        "</g>",
        # The front print: bottom edge, then the white print.
        f'<rect x="{PX}" y="{PY + 5}" width="{PW}" height="{PH}" rx="{PR}" fill="url(#side)"/>',
        f'<rect x="{PX}" y="{PY}" width="{PW}" height="{PH}" rx="{PR}" fill="{LIGHT_1}"/>',
        # The photo: sky, sun, far hill, near hill.
        '<g clip-path="url(#photo)">',
        f'<rect x="{IX}" y="{IY}" width="{IW}" height="{IH}" fill="url(#sky)"/>',
        f'<circle cx="{IX + 22}" cy="{IY + 18}" r="10" fill="{YELLOW_2}" fill-opacity="0.45"/>',
        f'<circle cx="{IX + 22}" cy="{IY + 18}" r="7" fill="{YELLOW_3}"/>',
        f'<path d="M{IX - 2} {IY + 44} C{IX + 14} {IY + 30} {IX + 30} {IY + 28} {IX + 46} {IY + 36} '
        f'C{IX + 58} {IY + 42} {IX + 70} {IY + 30} {IX + IW + 2} {IY + 32} '
        f'V{IY + IH + 2} H{IX - 2} z" fill="{GREEN_4}"/>',
        f'<path d="M{IX - 2} {IY + 50} C{IX + 18} {IY + 40} {IX + 40} {IY + 40} {IX + 60} {IY + 48} '
        f'C{IX + 68} {IY + 51} {IX + 74} {IY + 52} {IX + IW + 2} {IY + 50} '
        f'V{IY + IH + 2} H{IX - 2} z" fill="{GREEN_5}"/>',
        "</g>",
        # Badge with a house.
        f'<circle cx="{bx}" cy="{by + 3}" r="{br}" fill="{ORANGE_5}"/>',
        f'<circle cx="{bx}" cy="{by}" r="{br}" fill="url(#badge)"/>',
        f'<path d="M{bx} {by - 12} L{bx + 13} {by} H{bx + 9} V{by + 11} H{bx + 3} V{by + 4} '
        f'H{bx - 3} V{by + 11} H{bx - 9} V{by} H{bx - 13} z" fill="{LIGHT_1}" '
        f'stroke="{LIGHT_1}" stroke-width="2" stroke-linejoin="round"/>',
        "</svg>",
    ]
    return "\n".join(out) + "\n"


def symbolic_icon() -> str:
    """16 × 16, one colour (GNOME recolours symbolic icons): a photo frame with
    a house inside it."""
    return """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
<path fill="#2e3436" d="M3 1.5C1.9 1.5 1 2.4 1 3.5v9c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2v-9c0-1.1-.9-2-2-2zm0 2h10v9H3z"/>
<path fill="#2e3436" d="M8 4.5l-4 3.7h1.2V11.5h2v-2h1.6v2h2V8.2H12z"/>
</svg>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    base = os.path.join(ROOT, "data", "icons", "hicolor")
    paths = {
        os.path.join(base, "scalable", "apps", f"{APP_ID}.svg"): app_icon(),
        os.path.join(base, "symbolic", "apps", f"{APP_ID}-symbolic.svg"): symbolic_icon(),
    }
    for path, svg in paths.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        print("wrote", os.path.relpath(path, ROOT))
    if args.preview:
        os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
        out = os.path.join(ROOT, "build", "icon-preview.png")
        try:
            subprocess.run(["rsvg-convert", "-w", "256", "-h", "256", "-o", out,
                            list(paths)[0]], check=True)
            print("wrote", os.path.relpath(out, ROOT))
        except (OSError, subprocess.CalledProcessError) as e:
            print("preview needs rsvg-convert:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
