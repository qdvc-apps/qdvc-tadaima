"""Dataclasses for the domain records.

Pure — no GTK. Two record types:

* :class:`ImageRecord` — a single image file discovered on disk, plus the paths
  to its generated cache derivatives.
* :class:`FolderNode` — a node in the filtered folder hierarchy shown in the
  sidebar. Built by :mod:`qdvc.tree` from the flat index.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImageRecord:
    """A single image on disk and its cache derivatives."""

    path: str            # absolute source path (read-only; never modified)
    mtime: float         # source modification time (for change detection)
    size: int            # source byte size (for change detection)
    key: str             # stable cache key (hash of path)

    # Cache derivative paths (may be None until generated).
    thumb_path: str | None = None
    screen_path: str | None = None
    square_path: str | None = None

    @property
    def name(self) -> str:
        import os

        return os.path.basename(self.path)


@dataclass
class FolderNode:
    """A folder in the filtered sidebar hierarchy.

    ``image_count`` is the recursive count of images at this folder and all
    descendants. ``own_images`` are images directly in this folder (used to
    decide whether the folder gets a photo-crop icon and to populate the detail
    view).
    """

    path: str                                   # absolute folder path
    name: str                                   # display name (basename or "/")
    children: list["FolderNode"] = field(default_factory=list)
    own_images: list[ImageRecord] = field(default_factory=list)
    image_count: int = 0                        # recursive
    is_scanned_root: bool = False               # explicitly added by the user

    def add_child(self, node: "FolderNode") -> None:
        self.children.append(node)

    def sorted_children(self) -> list["FolderNode"]:
        return sorted(self.children, key=lambda n: n.name.lower())
