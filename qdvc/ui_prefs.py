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
    Shortcut("win.add-folder", "<Primary>o", "Add scanned folder", "Folders"),
    Shortcut("win.regenerate-cache", "<Primary>r", "Regenerate all caches", "Cache"),
    Shortcut("win.preferences", "<Primary>comma", "Preferences", "General"),
    Shortcut("win.back", "Escape", "Back to gallery (from full view)", "Navigation"),
    Shortcut("win.quit", "<Primary>q", "Quit", "General"),
)


# Density presets. Values are CSS-ish paddings applied to the sidebar rows.
# "compact" is Tadaima's opinionated default; "relaxed" restores libadwaita's
# spacious default padding.
DENSITY_SPECS: dict[str, dict[str, int]] = {
    "compact": {"row_pad_v": 2, "row_pad_h": 6, "spacing": 4},
    "relaxed": {"row_pad_v": 12, "row_pad_h": 12, "spacing": 8},
}


def density_spec(name: str) -> dict[str, int]:
    return DENSITY_SPECS.get(name, DENSITY_SPECS["compact"])


def format_count(n: int) -> str:
    """Right-aligned image count string, thousands-separated."""
    return f"{n:,}"
