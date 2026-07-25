"""Load/save YAML config at the XDG location.

Thin wrapper exposing ``get(key, default)`` / ``set(key, value)`` over a
``DEFAULTS`` dict. Because every read supplies a default, no schema migration is
required when a new key is added. Holds application preferences only — never
business data (there is no business data in Tadaima; the filesystem is the data).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from . import APP_NAME  # noqa: F401  (kept for parity/debug)

_CONFIG_DIRNAME = "qdvc-tadaima"
_CONFIG_FILENAME = "config.yml"

DEFAULTS: dict[str, Any] = {
    # List of absolute folder paths the user has explicitly added.
    "scanned_folders": [],
    # Window geometry.
    "window": {"width": 1100, "height": 720},
    # Sidebar width in px (resizable; persisted).
    "sidebar_width": 300,
    # Density: "compact" (default, tight padding) or "relaxed" (GTK4 default).
    "density": "compact",
    # UI backend selector (spec parity). Only gtk4 is implemented in Tadaima.
    "ui_backend": "gtk4",
    # Optional user-selected custom icon (absolute .png/.svg path) or None.
    "custom_icon": None,
}

_VALID_DENSITY = ("compact", "relaxed")


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / _CONFIG_DIRNAME


def _config_path() -> Path:
    return _config_dir() / _CONFIG_FILENAME


class Config:
    """In-memory config backed by a YAML file. Reads always supply a default."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self.load()

    # --- persistence -----------------------------------------------------
    def load(self) -> None:
        path = _config_path()
        data: dict[str, Any] = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh)
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, yaml.YAMLError):
                data = {}
        self._data = data

    def save(self) -> None:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self._data, fh, default_flow_style=False, sort_keys=True)
        os.replace(tmp, path)  # atomic

    # --- generic accessors ----------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if default is not None:
            return default
        return DEFAULTS.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    # --- validated accessors --------------------------------------------
    @property
    def scanned_folders(self) -> list[str]:
        val = self.get("scanned_folders", [])
        if not isinstance(val, list):
            return []
        # Normalise, dedupe, keep only strings.
        out: list[str] = []
        seen: set[str] = set()
        for p in val:
            if not isinstance(p, str):
                continue
            norm = os.path.normpath(os.path.expanduser(p))
            if norm not in seen:
                seen.add(norm)
                out.append(norm)
        return out

    def add_scanned_folder(self, path: str) -> bool:
        norm = os.path.normpath(os.path.expanduser(path))
        folders = self.scanned_folders
        if norm in folders:
            return False
        folders.append(norm)
        self.set("scanned_folders", folders)
        return True

    def remove_scanned_folder(self, path: str) -> bool:
        norm = os.path.normpath(os.path.expanduser(path))
        folders = self.scanned_folders
        if norm not in folders:
            return False
        folders.remove(norm)
        self.set("scanned_folders", folders)
        return True

    @property
    def density(self) -> str:
        val = str(self.get("density", "compact")).strip().lower()
        return val if val in _VALID_DENSITY else "compact"

    @density.setter
    def density(self, value: str) -> None:
        v = str(value).strip().lower()
        self.set("density", v if v in _VALID_DENSITY else "compact")

    @property
    def ui_backend(self) -> str:
        # Validate + lower-case; fall back to gtk4 (only implemented backend).
        val = str(self.get("ui_backend", "gtk4")).strip().lower()
        return val if val in ("gtk4",) else "gtk4"

    @property
    def sidebar_width(self) -> int:
        try:
            return max(150, int(self.get("sidebar_width", 300)))
        except (TypeError, ValueError):
            return 300

    @sidebar_width.setter
    def sidebar_width(self, value: int) -> None:
        try:
            self.set("sidebar_width", max(150, int(value)))
        except (TypeError, ValueError):
            pass
