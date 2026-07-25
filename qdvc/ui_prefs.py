"""Pure, toolkit-independent UI helpers shared across front-ends.

Holds the SHORTCUTS table and density specifications so a single source of truth
drives the (currently sole) GTK 4 front-end and any future front-end.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    action: str          # win.<name> for GTK4
    accel: str           # e.g. "Escape", "<Primary>q"
    label: str           # human-facing description
    group: str           # grouping in the shortcuts window


SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut("win.manage-folders", "<Primary>o", "Scanned folders", "Folders"),
    Shortcut("win.regenerate-cache", "<Primary>r", "Regenerate all caches", "Cache"),
    Shortcut("win.preferences", "<Primary>comma", "Preferences", "General"),
    Shortcut("win.back", "Escape", "Back to gallery (from full view)", "Navigation"),
    Shortcut("win.prev-photo", "Left", "Previous photo (in full view)", "Navigation"),
    Shortcut("win.next-photo", "Right", "Next photo (in full view)", "Navigation"),
    Shortcut("win.quit", "<Primary>q", "Quit", "General"),
)


# Density presets. Values are paddings (px) applied to sidebar rows via CSS.
# "compact" is Tadaima's opinionated default and is genuinely tight — much
# tighter than libadwaita's default. "relaxed" restores the spacious
# libadwaita default padding.
#
# row_pad_v / row_pad_h : vertical / horizontal padding inside each row.
# indent                : px added per hierarchy level.
# icon_px               : sidebar folder-icon pixel size.
# font_scale            : row label font scale (1.0 == theme default).
DENSITY_SPECS: dict[str, dict[str, float]] = {
    "compact": {
        "row_pad_v": 0,
        "row_pad_h": 4,
        "indent": 10,
        "icon_px": 16,
        "font_scale": 0.9,
    },
    "relaxed": {
        "row_pad_v": 10,
        "row_pad_h": 12,
        "indent": 18,
        "icon_px": 24,
        "font_scale": 1.0,
    },
}


def density_spec(name: str) -> dict[str, float]:
    return DENSITY_SPECS.get(name, DENSITY_SPECS["compact"])


def format_count(n: int) -> str:
    """Right-aligned image count string, thousands-separated."""
    return f"{n:,}"
