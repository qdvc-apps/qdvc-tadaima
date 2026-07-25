"""GObject row wrapper for the tree-structured sidebar.

Wraps a pure :class:`qdvc.models.FolderNode` so it can live in a
``Gio.ListStore`` / ``Gtk.TreeListModel``. Kept in the gtk4 package because it
imports ``gi``.
"""

from __future__ import annotations

from gi.repository import GObject

from ..models import FolderNode


class FolderItem(GObject.Object):
    """A single folder in the sidebar tree model."""

    __gtype_name__ = "TadaimaFolderItem"

    def __init__(self, node: FolderNode, style: str = "normal") -> None:
        super().__init__()
        self.node = node
        # style: "normal" | "ancestor" | "focused"
        self.style = style

    @property
    def path(self) -> str:
        return self.node.path
