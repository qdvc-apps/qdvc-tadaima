"""Launch system apps (viewer, file manager). Pure — no GTK.

Branches on ``sys.platform`` / ``os.name``. All operations are read-only with
respect to the user's photo folders; they only ask the OS to *open* or *reveal*
paths, never to modify them.
"""

from __future__ import annotations

import os
import subprocess
import sys


def open_with_default_app(path: str) -> None:
    """Open *path* in the OS default viewer."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


def reveal_in_file_manager(path: str) -> None:
    """Reveal *path* in the system file manager ("Locate on disk").

    Best-effort: tries to select the item where the platform supports it, else
    opens the containing directory.
    """
    directory = path if os.path.isdir(path) else os.path.dirname(path)

    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
        return
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        return

    # Linux: try common file managers that support selecting a file, then fall
    # back to xdg-open on the directory.
    for launcher in (
        ["nautilus", "--select", path],
        ["nemo", path],
        ["caja", "--no-desktop", directory],
        ["dolphin", "--select", path],
    ):
        try:
            subprocess.Popen(launcher)
            return
        except (FileNotFoundError, OSError):
            continue
    subprocess.Popen(["xdg-open", directory])
